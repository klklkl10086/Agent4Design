from __future__ import annotations

import os
import re
import atexit
import threading
import time
from queue import Queue
from typing import Optional, List, Dict, Any, Tuple

import pythoncom
import httpx
import concurrent.futures
from win32com.client import CastTo, gencache

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language


# ==========================================
# COM Dispatcher (STA)
# ==========================================
class COMDispatcher:
    def __init__(self) -> None:
        self.q: Queue = Queue()
        self.thread = threading.Thread(target=self._loop, name="RhpCOM", daemon=True)
        self._ready = threading.Event()

    def start(self) -> None:
        self.thread.start()
        self._ready.wait()

    def _loop(self) -> None:
        pythoncom.CoInitialize()
        self._ready.set()
        while True:
            fn, fut = self.q.get()
            if fn is None:
                break
            try:
                fut.set_result(fn())
            except Exception as e:
                fut.set_exception(e)
        pythoncom.CoUninitialize()

    def call(self, fn):
        fut = concurrent.futures.Future()
        self.q.put((fn, fut))
        return fut.result()

    def stop(self):
        self.q.put((None, None))
        self.thread.join()


com = COMDispatcher()
com.start()


def run_on_com(fn):
    return com.call(fn)


# ==========================================
# Env
# ==========================================
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

BASE_URL = "https://vio.automotive-wan.com:446"
VIO_HEADERS = {"useLegacyCompletionsEndpoint": "false", "X-Tenant-ID": "default_tenant"}


# ==========================================
# Context
# ==========================================
class RhapsodyContext:
    def __init__(self):
        self.app = None
        self.project = None
        self.target = None
        self.target_path = ""

    def initialize(self):
        self.app = gencache.EnsureDispatch("Rhapsody2.Application")
        self.project = self.app.activeProject()
        if self.target is None:
            t = self.app.getSelectedElement()
            if not t:
                raise RuntimeError("未选中元素")
            self.target = get_effective_target(t)
            self.target_path = getattr(self.target, "fullPathName", "")

    def ensure_connection_in_thread(self):
        try:
            _ = self.app.name
        except Exception:
            self.app = gencache.EnsureDispatch("Rhapsody2.Application")
            self.project = self.app.activeProject()


rhp_ctx = RhapsodyContext()
atexit.register(lambda: com.stop())


# ==========================================
# Pydantic models
# ==========================================
class CTypeInfo(BaseModel):
    base_type: str = Field(...)
    is_const: bool = Field(False)
    is_static: bool = Field(False)
    pointer_modifier: str = Field("")
    array_multiplicity: str = Field("")
    raw_declaration: str = Field(...)


class FunctionArgument(BaseModel):
    name: str
    type_info: CTypeInfo


class ActivityNode(BaseModel):
    id: str
    type: str  # Initial, Action, Decision, Merge, Final
    label: str = ""
    description: str = ""


class ActivityEdge(BaseModel):
    source: str
    target: str
    guard: str = ""


class ActivityGraph(BaseModel):
    nodes: List[ActivityNode]
    edges: List[ActivityEdge]


class SyncFunctionWithActivityArgs(BaseModel):
    name: str
    return_type_info: CTypeInfo
    arguments: List[FunctionArgument] = Field(default_factory=list)
    graph: ActivityGraph
    open_view: bool = True


class SyncFunctionWithMermaidArgs(BaseModel):
    name: str
    return_type_info: CTypeInfo
    arguments: List[FunctionArgument] = Field(default_factory=list)
    mermaid: str = Field(...)
    open_view: bool = True


# ==========================================
# Helpers
# ==========================================
def sanitize_name(raw_name: str) -> str:
    if not raw_name:
        return "Unnamed"
    return re.sub(r"[^a-zA-Z0-9_]", "", raw_name) or "Unnamed"


def first(col):
    try:
        return col.Item(1) if col and getattr(col, "Count", 0) >= 1 else None
    except Exception:
        return None


def ensure_view(ad):
    try:
        ad.setShowDiagramFrame(True)
    except Exception:
        pass
    try:
        ad.OpenDiagramView()
    except Exception:
        pass
    try:
        ad.createGraphics()
    except Exception:
        pass
    time.sleep(0.25)


def get_effective_target(original_target):
    t = original_target
    valid = ("Project", "Package", "Class", "File", "Module")
    while t and getattr(t, "metaClass", "") not in valid:
        owner = getattr(t, "owner", None)
        if not owner:
            break
        t = owner
    return t or original_target


def cast_to_specific_interface(elem, meta_class: str):
    m = {
        "Attribute": "IRPAttribute",
        "Variable": "IRPVariable",
        "Operation": "IRPOperation",
        "Function": "IRPOperation",
        "Argument": "IRPArgument",
        "Type": "IRPType",
    }
    itf = m.get(meta_class)
    if itf:
        try:
            return CastTo(elem, itf)
        except Exception:
            pass
    return elem


def is_writable_container(elem) -> bool:
    return True


def get_or_create_element(target, meta_class: str, name: str):
    try:
        col = target.getNestedElementsByMetaClass(meta_class, 0)
        if col:
            for i in range(1, col.Count + 1):
                item = col.Item(i)
                if getattr(item, "name", "") == name:
                    return cast_to_specific_interface(item, meta_class)
    except Exception:
        pass

    if not is_writable_container(target):
        print(f"⚠️ 容器不可写: {getattr(target, 'name', '?')}")
        return None

    try:
        new_elem = target.addNewAggr(meta_class, name)
        if new_elem:
            return cast_to_specific_interface(new_elem, meta_class)
    except Exception as e:
        print(f"⚠️ 创建失败 {meta_class}::{name} -> {e}")
    return None


def get_or_create_op_like(target, name: str):
    mc = getattr(target, "metaClass", "")
    meta_cls = "Operation" if mc in ("Class", "Block") else "Function"
    return get_or_create_element(target, meta_cls, sanitize_name(name)), meta_cls


def get_or_create_argument(op, name: str):
    try:
        col = getattr(op, "arguments", None)
        if col:
            for i in range(1, col.Count + 1):
                it = col.Item(i)
                if getattr(it, "name", "") == name:
                    return CastTo(it, "IRPArgument")
    except Exception:
        pass
    try:
        return CastTo(op.addArgument(name), "IRPArgument")
    except Exception as e:
        print(f"⚠️ 参数创建失败 {name}: {e}")
        return None


def assign_type_from_json(element, type_info: dict, element_meta: str, is_return: bool = False):
    base_type = (type_info.get("base_type", "void") or "void").strip()
    is_const = bool(type_info.get("is_const", False))
    ptr_mod = (type_info.get("pointer_modifier", "") or "").strip()
    array_mult = (type_info.get("array_multiplicity", "") or "").strip()

    classifier = None
    if base_type != "void":
        try:
            classifier = rhp_ctx.project.findNestedElementRecursive(base_type, "Type")
            if not classifier:
                classifier = rhp_ctx.project.findNestedElementRecursive(base_type, "Class")
        except Exception:
            pass
        if not classifier:
            try:
                classifier = get_or_create_element(get_effective_target(rhp_ctx.target), "Type", base_type)
                if classifier:
                    try:
                        classifier.kind = "Language"
                    except Exception:
                        pass
                    try:
                        classifier.declaration = base_type
                    except Exception:
                        pass
            except Exception:
                pass

    clean_text = f"{'const ' if is_const else ''}{base_type} {ptr_mod}".strip()

    if classifier:
        try:
            if is_return:
                element.returns = classifier
            else:
                element.type = classifier
                element.typeOf = classifier
        except Exception:
            pass

    if array_mult and (not is_return) and element_meta in ("Attribute", "Variable", "Argument"):
        try:
            element.multiplicity = array_mult
        except Exception:
            pass

    if is_return:
        try:
            element.setReturnTypeDeclaration(clean_text)
        except Exception:
            pass
    else:
        try:
            element.setTypeDeclaration(clean_text)
        except Exception:
            pass


def _edge_endpoints(e: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], str]:
    s = e.get("source", e.get("from", e.get("source_id")))
    t = e.get("target", e.get("to", e.get("target_id")))
    g = e.get("guard", "")
    return s, t, g


def _is_explicit_merge_node(n: Dict[str, Any]) -> bool:
    nid = (n.get("id") or "").strip().lower()
    label = (n.get("label") or "").strip().lower()
    desc = (n.get("description") or "").strip().lower()
    if nid.startswith("m_") or nid.startswith("merge_"):
        return True
    if "<<merge>>" in label or "<<merge>>" in desc:
        return True
    if label in ("merge", "合并") or desc in ("merge", "合并"):
        return True
    return False


def _validate_graph(graph: Dict[str, Any]) -> Tuple[bool, str]:
    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    if not nodes or not edges:
        return False, "缺少 nodes 或 edges"

    ids = {n.get("id") for n in nodes}
    types = [n.get("type", "") for n in nodes]
    if types.count("Initial") != 1 or types.count("Final") != 1:
        return False, "Initial/Final 必须且仅有一个"

    allowed = {"Initial", "Action", "Decision", "Merge", "Final"}
    for n in nodes:
        t = n.get("type")
        if t not in allowed:
            return False, f"非法节点类型: {t}"
        if t == "Merge" and not _is_explicit_merge_node(n):
            return False, f"Merge 必须显式标记，当前: {n.get('id')}"
        if t == "Decision" and not (n.get("label") or n.get("description")):
            return False, f"Decision 缺少条件文本: {n.get('id')}"

    for e in edges:
        s, t, _ = _edge_endpoints(e)
        if s not in ids or t not in ids:
            return False, f"edge 引用非法节点: {s}->{t}"
    return True, ""


def _to_safe_text(text: str) -> str:
    if text is None:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    s = "".join(ch for ch in s if ch in ("\n", "\t") or ord(ch) >= 32)
    return s[:12000]


def _set_action_content(action_state, text: str):
    s = _to_safe_text(text)
    if not s:
        return

    for key in ("C_CG::Action::Body", "C_CG::State::Body", "CPP_CG::Action::Body", "CPP_CG::State::Body"):
        try:
            if hasattr(action_state, "setPropertyValue"):
                action_state.setPropertyValue(key, s)
        except Exception:
            pass

    for fn in (
        lambda: setattr(action_state, "description", s),
        lambda: setattr(action_state, "Description", s),
        lambda: action_state.setDescription(s) if hasattr(action_state, "setDescription") else None,
        lambda: action_state.setPropertyValue("Description", s) if hasattr(action_state, "setPropertyValue") else None,
    ):
        try:
            fn()
        except Exception:
            pass


def _get_xywh(n: Dict[str, Any], idx: int, x0: int, y0: int, dy: int) -> Tuple[int, int, int, int]:
    if all(k in n for k in ("x", "y")):
        return int(n.get("x", x0)), int(n.get("y", y0 + idx * dy)), int(n.get("w", 180)), int(n.get("h", 40))
    t = n.get("type", "Action")
    x = x0 + (240 if t in ("Decision", "Merge") else 0)
    y = y0 + idx * dy
    if t in ("Decision", "Merge"):
        return x, y, 40, 40
    if t in ("Initial", "Final"):
        return x, y, 25, 25
    return x, y, 200, 50


def get_or_create_activity_diagram(op):
    try:
        col = op.getNestedElementsByMetaClass("ActivityDiagram", 0)
        if col and col.Count >= 1:
            for i in range(1, col.Count + 1):
                try:
                    return CastTo(col.Item(i), "IRPActivityDiagram")
                except Exception:
                    pass
    except Exception:
        pass

    try:
        flow = op.addActivityDiagram()
        ad = flow.getFlowchartDiagram() if hasattr(flow, "getFlowchartDiagram") else flow
        return CastTo(ad, "IRPActivityDiagram")
    except Exception:
        pass

    try:
        e = op.addNewAggr("ActivityDiagram", f"{op.name}_AD")
        return CastTo(e, "IRPActivityDiagram")
    except Exception:
        return None


def _create_model_node(ag_state, ntype: str, nid: str, label: str = "", desc: str = ""):
    """修复版：确保所有节点类型都能正确返回模型对象"""
    
    # 1. Initial 节点处理
    if ntype == "Initial":
        return "INITIAL_MARKER"

    # 2. Final 节点处理
    if ntype == "Final":
        try:
            return ag_state.addNewAggr("ActivityFinalNode", f"Final_{nid}")
        except:
            return ag_state.addActivityFinal()

    # 3. Decision 节点处理 (必须正确返回创建的对象)
    if ntype == "Decision":
        model_node = ag_state.addNewAggr("Condition", f"Dec_{nid}")
        try: model_node.addStereotype("DecisionNode", "Condition")
        except: pass
        return CastTo(model_node, "IRPModelElement") # 确保返回通用的模型对象

    # 4. Merge 节点处理 (必须正确返回创建的对象)
    if ntype == "Merge":
        model_node = ag_state.addNewAggr("JunctionConnector", f"mer_{nid}")
        try: model_node.addStereotype("MergeNode", "JunctionConnector")
        except: pass
        return CastTo(model_node, "IRPModelElement")

    # 5. Action 节点处理 (默认逻辑)
    name = (label or f"Act_{nid}").replace("\n", " ").replace("\r", " ")
    try:
        act = ag_state.addActivityAction(name) if hasattr(ag_state, "addActivityAction") else ag_state.addState(name)
    except:
        act = ag_state.addActivityAction(f"Act_{nid}") if hasattr(ag_state, "addActivityAction") else ag_state.addState(f"Act_{nid}")
    
    st = CastTo(act, "IRPState")
    _set_action_content(st, desc)
    return st

def _create_model_transition(src_model, tgt_model, guard: str, ag_state):
    # 处理 Initial 到 Action 的连接
    if src_model == "INITIAL_MARKER":
        tr = ag_state.createDefaultTransition(tgt_model)
        tr.itsTarget = tgt_model
        return tr

    # 兼容性处理：如果源节点是 Decision/Merge，它们可能没有 addTransition 方法
    # 此时改用 ag_state.addTransition 连接两个元素
    try:
        if hasattr(src_model, "addTransition"):
            tr = src_model.addTransition(tgt_model)
        else:
            tr = ag_state.addTransition(src_model, tgt_model)
            
        tr.itsTarget = tgt_model
        if guard and guard not in ("null", "None", ""):
            try: tr.setItsLabel("", str(guard), "")
            except: 
                try: tr.itsName = str(guard)[:80]
                except: pass
        return tr
    except Exception as e:
        print(f"⚠️ 连线创建失败: {e}")
        return None
# ==========================================
# Mermaid -> ActivityGraph
# ==========================================
def _clean_mermaid(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^```mermaid\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^```\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _detect_node_type(node_id: str, shape_text: str, inner_text: str) -> str:
    nid = (node_id or "").lower()
    txt = (inner_text or "").strip().lower()

    # Merge 显式规则（不靠拓扑）
    if nid.startswith("m_") or nid.startswith("merge_") or "<<merge>>" in txt or txt in ("merge", "合并"):
        return "Merge"

    # Initial / Final
    if shape_text.startswith("([") and shape_text.endswith("])"):
        if re.search(r"\b(start|begin|开始|入口)\b", txt):
            return "Initial"
        if re.search(r"\b(end|stop|exit|结束)\b", txt):
            return "Final"
        return "Action"

    if shape_text.startswith("((") and shape_text.endswith("))"):
        if re.search(r"\b(start|begin|开始|入口)\b", txt):
            return "Initial"
        if re.search(r"\b(end|stop|exit|结束)\b", txt):
            return "Final"
        return "Action"

    # Decision
    if shape_text.startswith("{") and shape_text.endswith("}"):
        return "Decision"

    # [] / [[]] 默认 Action
    return "Action"


def mermaid_to_activity_graph(mermaid: str) -> ActivityGraph:
    mmd = _clean_mermaid(mermaid)
    lines = [ln.strip() for ln in mmd.splitlines() if ln.strip() and not ln.strip().startswith("%%")]

    if lines and re.match(r"^(flowchart|graph)\s+", lines[0], re.IGNORECASE):
        lines = lines[1:]

    node_map: Dict[str, Dict[str, Any]] = {}
    edges: List[ActivityEdge] = []

    shape_pat = r"(\(\[[^\]]*\]\)|\(\([^\)]*\)\)|\{[^\}]*\}|\[\[[^\]]*\]\]|\[[^\]]*\])"
    node_decl_pat = re.compile(rf"^([A-Za-z_]\w*)\s*{shape_pat}$")
    edge_with_guard_pat = re.compile(
        rf"^([A-Za-z_]\w*)\s*{shape_pat}?\s*--\s*([^-]*)\s*-->\s*([A-Za-z_]\w*)\s*{shape_pat}?$"
    )
    simple_edge_pat = re.compile(
        rf"^([A-Za-z_]\w*)\s*{shape_pat}?\s*-->\s*([A-Za-z_]\w*)\s*{shape_pat}?$"
    )

    def upsert_node(nid: str, shape: Optional[str]):
        if nid not in node_map:
            label = ""
            ntype = "Action"
            if shape:
                if shape.startswith("((") and shape.endswith("))"):
                    inner = shape[2:-2]
                elif shape.startswith("([") and shape.endswith("])"):
                    inner = shape[2:-2]
                else:
                    inner = shape[1:-1]
                label = inner.strip()
                ntype = _detect_node_type(nid, shape, inner)
            node_map[nid] = {"id": nid, "type": ntype, "label": label, "description": ""}
        else:
            if shape:
                if shape.startswith("((") and shape.endswith("))"):
                    inner = shape[2:-2]
                elif shape.startswith("([") and shape.endswith("])"):
                    inner = shape[2:-2]
                else:
                    inner = shape[1:-1]
                if not node_map[nid]["label"]:
                    node_map[nid]["label"] = inner.strip()
                t2 = _detect_node_type(nid, shape, inner)
                if node_map[nid]["type"] == "Action" and t2 in ("Initial", "Final", "Decision", "Merge"):
                    node_map[nid]["type"] = t2

    for ln in lines:
        m0 = node_decl_pat.match(ln)
        if m0:
            upsert_node(m0.group(1), m0.group(2))
            continue

        m1 = edge_with_guard_pat.match(ln)
        if m1:
            s, s_shape, guard, t, t_shape = m1.group(1), m1.group(2), m1.group(3), m1.group(4), m1.group(5)
            upsert_node(s, s_shape)
            upsert_node(t, t_shape)
            edges.append(ActivityEdge(source=s, target=t, guard=(guard or "").strip(" []")))
            continue

        m2 = simple_edge_pat.match(ln)
        if m2:
            s, s_shape, t, t_shape = m2.group(1), m2.group(2), m2.group(3), m2.group(4)
            upsert_node(s, s_shape)
            upsert_node(t, t_shape)
            edges.append(ActivityEdge(source=s, target=t, guard=""))
            continue

    # 自动补 Initial/Final（若缺失）
    types = [n["type"] for n in node_map.values()]

    if types.count("Initial") == 0 and node_map:
        sid = "n_start"
        while sid in node_map:
            sid += "_x"
        node_map[sid] = {"id": sid, "type": "Initial", "label": "Start", "description": ""}
        indeg = {k: 0 for k in node_map.keys()}
        for e in edges:
            indeg[e.target] = indeg.get(e.target, 0) + 1
        tgt = next((k for k, v in indeg.items() if v == 0 and k != sid), None)
        if tgt:
            edges.insert(0, ActivityEdge(source=sid, target=tgt, guard=""))

    if types.count("Final") == 0 and node_map:
        eid = "n_end"
        while eid in node_map:
            eid += "_x"
        node_map[eid] = {"id": eid, "type": "Final", "label": "End", "description": ""}
        outdeg = {k: 0 for k in node_map.keys()}
        for e in edges:
            outdeg[e.source] = outdeg.get(e.source, 0) + 1
        src = next((k for k, v in outdeg.items() if v == 0 and k != eid), None)
        if src:
            edges.append(ActivityEdge(source=src, target=eid, guard=""))

    graph = ActivityGraph(
        nodes=[ActivityNode(**v) for v in node_map.values()],
        edges=edges
    )

    ok, msg = _validate_graph(graph.model_dump())
    if not ok:
        raise ValueError(msg)
    return graph


# ==========================================
# Tool: final sync by graph
# ==========================================
@tool(args_schema=SyncFunctionWithActivityArgs)
def sync_function_with_activity_diagram(
    name: str,
    return_type_info: CTypeInfo,
    arguments: List[FunctionArgument],
    graph: ActivityGraph,
    open_view: bool = True,
) -> str:
    """同步函数签名并绘制活动图。"""

    def _impl():
        rhp_ctx.ensure_connection_in_thread()

        graph_dict = graph.model_dump()
        ok, msg = _validate_graph(graph_dict)
        if not ok:
            return f"❌ 无效图定义: {msg}"

        tgt = get_effective_target(rhp_ctx.target)
        op, meta_cls = get_or_create_op_like(tgt, name)
        if not op:
            return f"❌ 失败: 无法创建 {meta_cls} {name}"

        assign_type_from_json(op, return_type_info.model_dump(), meta_cls, is_return=True)
        if return_type_info.is_static:
            try:
                op.isStatic = True
            except Exception:
                pass

        for idx, a in enumerate(arguments):
            arg_name = sanitize_name(a.name) or f"arg{idx + 1}"
            bt = a.type_info.base_type.strip().lower()
            if arg_name.lower() == "void" or bt in ("void", "t_void"):
                continue
            arg_el = get_or_create_argument(op, arg_name)
            if not arg_el:
                continue
            try:
                arg_el.direction = 0
            except Exception:
                pass
            assign_type_from_json(arg_el, a.type_info.model_dump(), "Argument", is_return=False)

        ad = get_or_create_activity_diagram(op)
        if not ad:
            return f"⚠️ 警告: 无法创建活动图容器 {name}"

        if open_view:
            ensure_view(ad)

        ag = ad.getStatechart()
        if not ag:
            return f"⚠️ 警告: 无 Statechart {name}"
        ag_state = ag.rootState

        id2model: Dict[str, Any] = {}
        id2pos: Dict[str, Tuple[int, int, int, int]] = {}
        id2graphic: Dict[str, Any] = {}
        edge2model: Dict[Tuple[str, str], Any] = {}

        base_x, base_y, step_y = 140, 120, 110

        for idx, n in enumerate(graph_dict.get("nodes", [])):
            nid, ntype = n.get("id"), n.get("type")
            if not nid or not ntype:
                continue
            x, y, w, h = _get_xywh(n, idx, base_x, base_y, step_y)
            id2pos[nid] = (x, y, w, h)
            try:
                id2model[nid] = _create_model_node(
                    ag_state=ag_state,
                    ntype=ntype,
                    nid=nid,
                    label=n.get("label", ""),
                    desc=n.get("description", ""),
                )
            except Exception as e:
                print(f"⚠️ 节点建模失败 {nid}/{ntype}: {e}")
                id2model[nid] = None

        for e in graph_dict.get("edges", []):
            s, t, guard = _edge_endpoints(e)
            if not s or not t:
                continue
            src_model = id2model.get(s)
            tgt_model = id2model.get(t)
            if not src_model or not tgt_model or tgt_model == "INITIAL_MARKER":
                continue
            try:
                edge2model[(s, t)] = _create_model_transition(src_model, tgt_model, guard, ag_state)
            except Exception as ex:
                print(f"⚠️ 连线建模失败 {s}->{t}: {ex}")

        for n in graph_dict.get("nodes", []):
            nid, ntype = n.get("id"), n.get("type")
            if not nid or not ntype or ntype == "Initial":
                continue
            me = id2model.get(nid)
            if not me:
                continue
            x, y, w, h = id2pos[nid]
            if ntype in ("Decision", "Merge"):
                w, h = 40, 40
            elif ntype == "Final":
                w, h = 25, 25
            else:
                w, h = max(120, w), max(40, h)

            try:
                gn = ad.AddNewNodeForElement(me, x, y, w, h)
                if gn:
                    id2graphic[nid] = gn
            except Exception as ex:
                print(f"⚠️ 图形节点失败 {nid}: {ex}")

        for e in graph_dict.get("edges", []):
            s, t, _ = _edge_endpoints(e)
            if not s or not t:
                continue
            tr = edge2model.get((s, t))
            if not tr:
                continue
            tgt_g = id2graphic.get(t)
            if not tgt_g:
                continue
            try:
                if id2model.get(s) == "INITIAL_MARKER":
                    sx, sy, _, _ = id2pos.get(s, (140, 50, 0, 0))
                    ad.AddNewEdgeForElement(tr, None, sx, sy, tgt_g, 0, 0)
                else:
                    src_g = id2graphic.get(s)
                    if src_g:
                        ad.AddNewEdgeForElement(tr, src_g, 0, 0, tgt_g, 0, 0)
            except Exception as ex:
                print(f"⚠️ 图形连线失败 {s}->{t}: {ex}")

        try:
            ad.createGraphics()
        except Exception:
            pass

        return f"✅ 成功: 函数及活动图 {name} 同步完毕。"

    return run_on_com(_impl)


# ==========================================
# Tool: Mermaid entry
# ==========================================
@tool(args_schema=SyncFunctionWithMermaidArgs)
def sync_function_with_mermaid(
    name: str,
    return_type_info: CTypeInfo,
    arguments: List[FunctionArgument],
    mermaid: str,
    open_view: bool = True
) -> str:
    """输入 Mermaid flowchart，转换后同步到 Rhapsody。"""
    try:
        graph = mermaid_to_activity_graph(mermaid)
    except Exception as e:
        return f"❌ Mermaid 解析失败: {e}"

    return sync_function_with_activity_diagram.invoke({
        "name": name,
        "return_type_info": return_type_info.model_dump(),
        "arguments": [a.model_dump() for a in arguments],
        "graph": graph.model_dump(),
        "open_view": open_view
    })


# ==========================================
# Agent
# ==========================================
def create_rhapsody_agent():
    tools = [sync_function_with_mermaid]

    api_token = os.getenv("API_TOKEN")
    if not api_token:
        raise EnvironmentError("🚨 启动失败: 缺少 API_TOKEN")

    llm = ChatOpenAI(
        model="VIO:Claude 4.5 Opus",
        openai_api_base=BASE_URL,
        openai_api_key=api_token,
        default_headers={**VIO_HEADERS},
        temperature=0.0,
        http_client=httpx.Client(
            timeout=httpx.Timeout(connect=60.0, read=300.0, write=60.0, pool=60.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        ),
        max_retries=3,
        streaming=False
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是“C代码到Rhapsody活动图”结构化提取器。
仅当输入包含完整函数定义（含函数体）时，调用工具 sync_function_with_mermaid；否则输出“跳过”。

硬性要求：
1) 仅允许两种行为：工具调用 或 输出“跳过”
2) 禁止输出解释文字
3) 工具参数键名必须是：name, return_type_info, arguments, mermaid, open_view
4) mermaid 必须是 flowchart TD
5) 节点映射约束：
   - ([Start]) 或 ((Start)) => Initial
   - ([End]) 或 ((End)) => Final
   - {{cond}} => Decision
   - [action] => Action
   - Merge 必须显式标记：id 前缀 m_ / merge_ 或文本 <<Merge>> / merge / 合并
6) 边写法：
   - A --> B
   - A -- true --> B
"""),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=20,
        handle_parsing_errors=True,
    )

def safe_invoke_agent(executor, content: str):
    try:
        executor.invoke({"input": content})
        return
    except ValidationError:
        print("⚠️ 跳过当前代码块：参数校验失败")
        return
    except Exception as e:
        msg = str(e)

        if "Field required" in msg or "validation error" in msg.lower() or "missing variables" in msg.lower():
            try:
                repair_prompt = (
                    "上一次输出不符合工具参数约束。\n"
                    "请严格执行以下要求：\n"
                    "1) 仅允许一次工具调用 sync_function_with_mermaid，或输出“跳过”。\n"
                    "2) 工具参数键名必须是 name, return_type_info, arguments, mermaid, open_view。\n"
                    "3) 不要输出解释性文字。\n"
                    "4) mermaid 必须是 flowchart TD。\n\n"
                    "原始代码片段如下：\n"
                    f"{content}"
                )
                executor.invoke({"input": repair_prompt})
                return
            except Exception as e2:
                print(f"⚠️ 二次纠偏失败，跳过当前代码块: {e2}")
                return

        print(f"⚠️ 当前代码块跳过: {e}")

# ==========================================
# Main
# ==========================================
if __name__ == "__main__":
    file_path = r"D:\project_design\GTMC_V57_CD\sw.cmp.CD\Source\CD\Project\Code\CD_AppDataTransmWrite.c"

    if not os.path.exists(file_path):
        print(f"❌ 找不到文件: {file_path}")
    else:
        try:
            run_on_com(lambda: rhp_ctx.initialize())

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                c_content = f.read()

            splitter = RecursiveCharacterTextSplitter.from_language(
                language=Language.C, chunk_size=10000, chunk_overlap=1000
            )
            docs = splitter.create_documents([c_content])

            agent_executor = create_rhapsody_agent()

            for i, doc in enumerate(docs, 1):
                safe_invoke_agent(agent_executor, doc.page_content)
                if i < len(docs):
                    time.sleep(2)

            run_on_com(lambda: rhp_ctx.project.save())
            print("\n🎉 全部同步完成")
        except Exception as e:
            print(f"💥 致命错误: {e}")
        finally:
            try:
                com.stop()
            except Exception:
                pass
