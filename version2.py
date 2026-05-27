# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import atexit
import threading
import time
import concurrent.futures
from queue import Queue
from typing import Optional, List, Dict, Any, Tuple

import pythoncom
import httpx
import win32com.client
from win32com.client import CastTo, gencache

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language

# ==========================================
# 1. 线程调度器 (COM Dispatcher) - 保持 STA
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
        pythoncom.CoInitialize()  # 保持单一 STA 线程
        self._ready.set()
        while True:
            fn, fut = self.q.get()
            if fn is None:
                break
            try:
                res = fn()
                fut.set_result(res)
            except Exception as e:
                fut.set_exception(e)
        pythoncom.CoUninitialize()

    def call(self, fn):
        fut = concurrent.futures.Future()
        self.q.put((fn, fut))
        return fut.result()

    def stop(self) -> None:
        self.q.put((None, None))
        self.thread.join()

com = COMDispatcher()
com.start()

def run_on_com(fn):
    return com.call(fn)

# ==========================================
# 2. 全局环境与配置
# ==========================================
rhp_com_lock = threading.Lock()

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

BASE_URL = "https://vio.automotive-wan.com:446"
VIO_HEADERS = {"useLegacyCompletionsEndpoint": "false", "X-Tenant-ID": "default_tenant"}

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
            if not t: raise RuntimeError("未选中元素")
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
# 3. Mermaid 编译器 (核心新增)
# ==========================================
def parse_mermaid_to_graph(mermaid_str: str) -> dict:
    """将标准化的 Mermaid flowchart 代码编译为 Rhapsody Graph 字典"""
    nodes_dict = {}
    edges_list = []
    
    mermaid_str = mermaid_str.replace("```mermaid", "").replace("```", "").strip()
    
    # 匹配节点: 支持 (), [], {{}}, (())
    node_pattern = re.compile(r'^([a-zA-Z0-9_]+)\s*(\(\[|\[|\{\{|\{|\(\()\s*(.*?)\s*(\]\)|\]|\}\}|\}|\)\))$')
    # 匹配连线: src --> tgt 或 src -->|guard| tgt
    edge_pattern = re.compile(r'^([a-zA-Z0-9_]+)\s*-->\s*(?:\|([^|]+)\|\s*)?([a-zA-Z0-9_]+)$')
    
    for line in mermaid_str.splitlines():
        line = line.strip()
        if not line or line.startswith("flowchart") or line.startswith("%%"): continue
            
        node_match = node_pattern.match(line)
        if node_match:
            nid, bracket_open, label, bracket_close = node_match.groups()
            ntype = "Action"
            if bracket_open == '([':
                ntype = "Initial" if "start" in label.lower() or "init" in label.lower() else "Final"
            elif bracket_open in ('{{', '{'): ntype = "Decision"
            elif bracket_open == '((': ntype = "Merge"
            elif bracket_open == '[': ntype = "Action"
                
            nodes_dict[nid] = {"id": nid, "type": ntype, "label": label.strip()}
            continue
            
        edge_match = edge_pattern.match(line)
        if edge_match:
            src, guard, tgt = edge_match.groups()
            edges_list.append({
                "source": src, "target": tgt, "guard": guard.strip() if guard else ""
            })
            
    return {"nodes": list(nodes_dict.values()), "edges": edges_list}

def _validate_graph(graph: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(graph, dict): return False, "graph 需为 dict"
    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    if not nodes or not edges: return False, "缺少 nodes 或 edges，可能 Mermaid 语法错误"
    types = [n.get("type", "") for n in nodes]
    if types.count("Initial") != 1 or types.count("Final") != 1:
        return False, "Initial/Final 节点必须且仅有一个"
    ids = {n.get("id") for n in nodes}
    for e in edges:
        if e.get("source") not in ids or e.get("target") not in ids:
            return False, f"连线引用了未定义的节点"
    return True, ""

# ==========================================
# 4. Rhapsody 辅助与图元构建函数
# ==========================================
def sanitize_name(raw_name: str) -> str:
    if not raw_name: return "Unnamed"
    return re.sub(r'[^a-zA-Z0-9_]', '', raw_name) or "Unnamed"

def get_effective_target(original_target):
    target = original_target
    valid_containers = ("Project", "Package", "Class", "File", "Module")
    while target and getattr(target, 'metaClass', '') not in valid_containers:
        owner = getattr(target, 'owner', None)
        if not owner: break
        target = owner
    return target if target else original_target

def cast_to_specific_interface(elem, meta_class: str):
    interface_map = {
        "Attribute": "IRPAttribute", "Variable": "IRPVariable",
        "Operation": "IRPOperation", "Function": "IRPOperation",
        "Argument": "IRPArgument", "Type": "IRPType",
    }
    target_interface = interface_map.get(meta_class)
    if target_interface:
        try: return CastTo(elem, target_interface)
        except: pass
    return elem

def get_or_create_element(target, meta_class: str, name: str):
    try:
        col = target.getNestedElementsByMetaClass(meta_class, 0)
        if col:
            for i in range(1, col.Count + 1):
                item = col.Item(i)
                if getattr(item, "name", "") == name:
                    return cast_to_specific_interface(item, meta_class)
    except: pass

    try:
        all_elements = target.nestedElements
        if all_elements:
            for i in range(1, all_elements.Count + 1):
                item = all_elements.Item(i)
                if getattr(item, "name", "") == name:
                    try: item.deleteFromProject()
                    except: pass
                    break
    except: pass

    try:
        new_elem = target.addNewAggr(meta_class, name)
        if new_elem: return cast_to_specific_interface(new_elem, meta_class)
    except: pass
    return None

def assign_type_from_json(element, type_info: dict, element_meta: str, is_return: bool = False):
    base_type = type_info.get("base_type", "void").strip()
    is_const = type_info.get("is_const", False)
    ptr_mod = type_info.get("pointer_modifier", "").strip()
    array_mult = type_info.get("array_multiplicity", "").strip()

    if array_mult and not is_return and element_meta in ("Attribute", "Variable", "Argument"):
        try: element.multiplicity = array_mult
        except: pass

    classifier = None
    if base_type and base_type != "void":
        try:
            classifier = rhp_ctx.project.findNestedElementRecursive(base_type, "Type")
            if not classifier: classifier = rhp_ctx.project.findNestedElementRecursive(base_type, "Class")
        except: pass

        if not classifier:
            try:
                container = get_effective_target(rhp_ctx.target)
                classifier = get_or_create_element(container, "Type", base_type)
                if classifier:
                    try: classifier.kind = "Language"
                    except: pass
                    try: classifier.declaration = base_type
                    except: pass
            except: pass

    clean_textual_type = f"{'const ' if is_const else ''}{base_type} {ptr_mod}".strip()

    if classifier:
        if is_return:
            try: element.returns = classifier
            except: pass
        else:
            try: element.type = classifier
            except: pass
            try: element.typeOf = classifier
            except: pass

        if is_const:
            try: element.isConstant = True
            except: pass
        if ptr_mod and not is_return:
            try: element.typeModifier = ptr_mod
            except: pass

    if is_return:
        try: element.setReturnTypeDeclaration(clean_textual_type)
        except: pass
    else:
        try: element.setTypeDeclaration(clean_textual_type)
        except: pass

def _get_xywh(n: Dict[str, Any], idx: int, x0: int, y0: int, dy: int) -> Tuple[int, int, int, int]:
    t = n.get("type", "Action")
    x = x0 + (240 if t in ("Decision", "Merge") else 0)
    y = y0 + idx * dy
    w, h = (200, 50) if t == "Action" else (40, 40)
    if t in ("Initial", "Final"): w, h = 20, 20
    return x, y, w, h

def _add_label_box(ad, text: str, x: int, y: int, w: int, h: int, dy: int = 18):
    if not text: return
    try: ad.AddTextBox(str(text)[:2000], x, y - dy, max(120, w), 24 if dy < 40 else dy)
    except: pass

def _label_edge_guard(ad, guard: str, x1: int, y1: int, x2: int, y2: int):
    if not guard: return
    try: ad.AddTextBox(str(guard)[:200], (x1 + x2) // 2, (y1 + y2) // 2 - 16, 140, 22)
    except: pass

def get_root_state_safely(ad_view):
    """曲线救国：通过 Owner 安全获取底层 rootState"""
    try:
        owner = getattr(ad_view, "owner", None)
        if not owner: return None
        graphs = owner.getNestedElementsByMetaClass("ActivityGraph", 0)
        if graphs and graphs.Count > 0: return graphs.Item(1).rootState
        charts = owner.getNestedElementsByMetaClass("Statechart", 0)
        if charts and charts.Count > 0: return charts.Item(1).rootState
    except: pass
    return None

def _add_native_connector(ad_view, root_state, ntype: str, nid: str, x: int, y: int, w: int, h: int):
    """专属原生判定/合并节点构建逻辑"""
    meta_class = "Condition" if ntype == "Decision" else "JunctionConnector"
    stereotype = "DecisionNode" if ntype == "Decision" else "MergeNode"
    name = sanitize_name(f"{ntype}_{nid}")
    
    model_node = root_state.addNewAggr(meta_class, name)
    try: model_node.addStereotype(stereotype, meta_class)
    except: pass
        
    gn = _place_node_for_element(ad_view, model_node, x, y, w, h)
    return gn if gn else model_node

def get_or_create_activity_diagram(op):
    try:
        col = op.getNestedElementsByMetaClass("ActivityDiagram", 0)
        if col and col.Count >= 1:
            return CastTo(col.Item(1), "IRPActivityDiagram")
    except: pass
    try:
        op.addActivityDiagram()
        col = op.getNestedElementsByMetaClass("ActivityDiagram", 0)
        if col and col.Count >= 1: return CastTo(col.Item(col.Count), "IRPActivityDiagram")
    except: pass
    return None

def _ensure_model_initial(op):
    for mc in ("ActivityInitialNode", "InitialNode", "ActivityInitial", "Initial"):
        try:
            col = op.getNestedElementsByMetaClass(mc, 0)
            if col and col.Count >= 1: return col.Item(1)
        except: pass
        try: return op.addNewAggr(mc, "Initial0")
        except: pass
    return None

def _ensure_model_activity_final(op):
    for mc in ("ActivityFinal", "FinalNode", "ActivityFinalNode"):
        try:
            col = op.getNestedElementsByMetaClass(mc, 0)
            if col and col.Count >= 1: return col.Item(1)
        except: pass
    for mc, name in (("ActivityFinal", "Final0"), ("FinalNode", "Final0")):
        try: return op.addNewAggr(mc, name)
        except: pass
    return None

def _place_node_for_element(ad, element, x, y, w, h):
    if not element: return None
    try: return ad.AddNewNodeForElement(element, x, y, w, h)
    except: return None

def _meta_candidates(t: str) -> List[str]:
    return {"Action": ["OpaqueAction", "Action", "ActionState"]}.get(t, ["OpaqueAction"])

def _add_node_safe(ad, ntype, x, y, w, h, op=None):
    if op:
        if ntype == "Initial":
            gn = _place_node_for_element(ad, _ensure_model_initial(op), x, y, w, h)
            if gn: return gn
        if ntype == "Final":
            gn = _place_node_for_element(ad, _ensure_model_activity_final(op), x, y, w, h)
            if gn: return gn

    for meta in _meta_candidates(ntype):
        try: return ad.AddNewNodeByType(meta, x, y, w, h)
        except: continue
    raise RuntimeError(f"无法创建节点类型: {ntype}")

def get_or_create_argument(op, name: str):
    try:
        col = getattr(op, "arguments", None)
        if col:
            for i in range(1, col.Count + 1):
                it = col.Item(i)
                if getattr(it, "name", "") == name: return CastTo(it, "IRPArgument")
    except: pass
    try: return CastTo(op.addArgument(name), "IRPArgument")
    except: return None

def get_or_create_op_like(target, name: str):
    mc = getattr(target, "metaClass", "")
    meta_cls = "Operation" if mc in ("Class", "Block") else "Function"
    return get_or_create_element(target, meta_cls, sanitize_name(name)), meta_cls

def _add_edge_safe(ad, src, trg, guard, x1, y1, x2, y2, src_is_initial=False):
    metas = ["ControlFlow", "DefaultTransition", "Transition"] if src_is_initial else ["ControlFlow", "Transition"]
    for meta in metas:
        try:
            e = ad.AddNewEdgeByType(meta, src, x1, y1, trg, x2, y2)
            if guard: _label_edge_guard(ad, guard, x1, y1, x2, y2)
            return e
        except: continue
    raise RuntimeError("无法创建连线")


# ==========================================
# 5. Pydantic 数据结构 (全新极简版)
# ==========================================
class CTypeInfo(BaseModel):
    base_type: str = Field(..., description="纯净基础类型名")
    is_const: bool = Field(False)
    is_static: bool = Field(False)
    pointer_modifier: str = Field("", description="指针修饰符")
    array_multiplicity: str = Field("", description="数组大小")
    raw_declaration: str = Field(...)

class FunctionArgument(BaseModel):
    name: str = Field(..., description="参数纯净变量名")
    type_info: CTypeInfo = Field(...)

class SyncMacroArgs(BaseModel):
    name: str = Field(...)
    value: str = Field(...)
    type_info: Optional[CTypeInfo] = Field(default=None)

class SyncVariableArgs(BaseModel):
    name: str = Field(...)
    type_info: CTypeInfo = Field(...)
    initial_value: Optional[str] = Field(default=None)

class SyncFunctionWithActivityArgs(BaseModel):
    name: str = Field(..., description="函数名称")
    return_type_info: CTypeInfo = Field(..., description="返回值类型")
    arguments: List[FunctionArgument] = Field(default_factory=list, description="参数列表")
    mermaid_code: str = Field(..., description="代表函数控制流逻辑的 Mermaid 代码")
    open_view: bool = Field(True, description="是否在画布打开")


# ==========================================
# 6. LangChain 工具
# ==========================================
@tool(args_schema=SyncMacroArgs)
def sync_macro_to_rhapsody(name: str, value: str, type_info: Optional[CTypeInfo] = None) -> str:
    def _impl():
        rhp_ctx.ensure_connection_in_thread()
        try:
            clean_name = sanitize_name(name)
            container = get_effective_target(rhp_ctx.target)
            meta_cls = "Attribute" if getattr(container, 'metaClass', '') in ("Class", "Block") else "Variable"
            elem = get_or_create_element(container, meta_cls, clean_name)
            if not elem: return f"❌ 失败: 宏 {clean_name}"
            try: elem.addStereotype("Define", meta_cls)
            except: pass
            try: elem.defaultValue = (value or "").strip(" =;")
            except: pass
            if type_info: assign_type_from_json(elem, type_info.dict(), meta_cls, is_return=False)
            return f"✅ 成功: 宏 {clean_name}"
        except Exception as e: return f"❌ 失败: {str(e)}"
    return run_on_com(_impl)

@tool(args_schema=SyncVariableArgs)
def sync_variable_to_rhapsody(name: str, type_info: CTypeInfo, initial_value: Optional[str] = None) -> str:
    def _impl():
        rhp_ctx.ensure_connection_in_thread()
        try:
            clean_name = sanitize_name(name)
            target = get_effective_target(rhp_ctx.target)
            meta_cls = "Attribute" if getattr(target, 'metaClass', '') == "Class" else "Variable"
            var = get_or_create_element(target, meta_cls, clean_name)
            if not var: return f"❌ 失败: 变量 {clean_name}"
            assign_type_from_json(var, type_info.dict(), meta_cls, is_return=False)
            if initial_value:
                try: var.defaultValue = initial_value.strip(' =;')
                except: pass
            return f"✅ 成功: 变量 {clean_name}"
        except Exception as e: return f"❌ 失败: {str(e)}"
    return run_on_com(_impl)

@tool(args_schema=SyncFunctionWithActivityArgs)
def sync_function_with_activity_diagram(name: str, return_type_info: CTypeInfo, arguments: List[FunctionArgument],
                                        mermaid_code: str, open_view: bool = True) -> str:
    """提取函数定义并使用 Mermaid 编译为 Rhapsody 活动图。"""
    def _impl():
        rhp_ctx.ensure_connection_in_thread()
        try:
            # 🌟 编译 Mermaid 代码
            graph_dict = parse_mermaid_to_graph(mermaid_code)
            ok, msg = _validate_graph(graph_dict)
            if not ok: 
                return f"❌ Mermaid 编译失败: {msg}\n{mermaid_code}"

            tgt = get_effective_target(rhp_ctx.target)
            op, meta_cls = get_or_create_op_like(tgt, name)
            if not op: return f"❌ 失败: 无法创建 {meta_cls} {name}"

            # 同步函数签名
            assign_type_from_json(op, return_type_info.dict(), meta_cls, is_return=True)
            if return_type_info.is_static:
                try: op.isStatic = True
                except: pass

            if not (len(arguments) == 1 and arguments[0].type_info.base_type.strip() == "void"):
                for idx, a in enumerate(arguments):
                    arg_name = sanitize_name(a.name) or f"arg{idx + 1}"
                    arg_el = get_or_create_argument(op, arg_name)
                    if arg_el:
                        try: arg_el.direction = 0 
                        except: pass
                        assign_type_from_json(arg_el, a.type_info.dict(), "Argument", is_return=False)

            ad = get_or_create_activity_diagram(op)
            if not ad: return f"⚠️ 警告: 获取活动图失败。"

            # 获取底层状态机
            root_state = get_root_state_safely(ad)

            try: ad.createGraphics()
            except: pass

            if open_view:
                try:
                    ad.setShowDiagramFrame(True)
                    ad.OpenDiagramView()
                except: pass

            base_x, base_y, step_y = 140, 120, 110
            id2node, id2pos = {}, {}

            # 绘制节点
            for idx, n in enumerate(graph_dict.get("nodes", [])):
                nid, ntype, label_text = n.get("id"), n.get("type"), n.get("label", "")
                if not nid or not ntype: continue

                x, y, w, h = _get_xywh(n, idx, base_x, base_y, step_y)

                if ntype in ("Decision", "Merge"):
                    gn = _add_native_connector(ad, root_state, ntype, nid, x, y, w, h)
                else:
                    gn = _add_node_safe(ad, ntype, x, y, w, h, op=op)
                    
                id2node[nid], id2pos[nid] = gn, (x, y, w, h)
                _add_label_box(ad, label_text, x, y, w, h)

            # 绘制连线
            initial_ids = {n.get("id") for n in graph_dict.get("nodes", []) if n.get("type") == "Initial"}
            for e in graph_dict.get("edges", []):
                s, t, guard = e.get("source"), e.get("target"), e.get("guard", "")
                if s and t and s in id2node and t in id2node:
                    x1, y1, w1, h1 = id2pos[s]
                    x2, y2, w2, h2 = id2pos[t]
                    cx1, cy1 = x1 + w1 // 2, y1 + h1 // 2
                    cx2, cy2 = x2 + w2 // 2, y2 + h2 // 2
                    _add_edge_safe(ad, id2node[s], id2node[t], guard, cx1, cy1, cx2, cy2, src_is_initial=(s in initial_ids))

            return f"✅ 成功: 函数及活动图 {name} 编译同步完毕。"
        except Exception as ex:
            import traceback
            print(traceback.format_exc())
            return f"❌ 失败: {ex}"

    return run_on_com(_impl)


# ==========================================
# 7. Agent 执行器构建
# ==========================================
def create_rhapsody_agent():
    tools = [sync_function_with_activity_diagram] # 开启其他工具请自行解除注释

    api_token = os.getenv("API_TOKEN")
    if not api_token: raise EnvironmentError("🚨 启动失败: 未获取到 API_TOKEN")

    custom_http_client = httpx.Client(
        timeout=httpx.Timeout(connect=60.0, read=300.0, write=60.0, pool=60.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    )

    llm = ChatOpenAI(
        model="VIO:Gemini 2.0 Flash",
        openai_api_base=BASE_URL,
        openai_api_key=api_token,
        default_headers={**VIO_HEADERS},
        temperature=0.1,
        http_client=custom_http_client,
        max_retries=3,
        streaming=False
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是顶级的 C 语言分析与 Rhapsody UML 架构师。
任务：分析 C 语言代码，提取完整函数并同步到 Rhapsody。
规则：
1. 遇到完整函数，调用 `sync_function_with_activity_diagram` 工具。
2. 【Mermaid 严格规范】：必须生成 flowchart TD 纯文本代码，严格遵守格式供解析器读取：
   - 必须拆分为两部分：先逐行定义节点，再逐行定义连线！禁止把节点定义写在连线里！
   - 节点 ID 由字母和数字组成 (如 n1, n2)。
   - 开始/结束节点使用 ([ ])：n1([Start]) / n9([End])
   - Action节点使用 [ ]：n2[Process Data]
   - 判定节点使用 {{ }}：n3{{x > 0}}
   - 合并节点使用 (( ))：n4((Merge))
   - 连线格式：n1 --> n2 或带条件的 n3 -->|Yes| n4

【正确的 Mermaid 示例】
flowchart TD
%% 节点区
n1([Start])
n2[Init x]
n3{{x > 10}}
n4[Return True]
n5[Return False]
n6((Merge))
n7([End])
%% 连线区
n1 --> n2
n2 --> n3
n3 -->|Yes| n4
n3 -->|No| n5
n4 --> n6
n5 --> n6
n6 --> n7

3. 绝对不要发送被截断的代码。如果信息不全，直接回复“跳过”。
"""),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=30)

def safe_invoke_agent(executor, content):
    try:
        executor.invoke({"input": content})
    except ValidationError: print(f"\n⚠️ 跳过：大模型生成参数格式错误。")
    except ValueError: print(f"\n⚠️ 跳过：输出格式解析失败。")
    except Exception as e: print(f"\n⚠️ 执行失败跳过: {e}")

if __name__ == "__main__":
    file_path = r"D:\project_design\GTMC_V57_CD\sw.cmp.CD\Source\CD\Project\Code\CD_AppDataTransmRead.c"
    if not os.path.exists(file_path):
        print(f"❌ 找不到文件: {file_path}")
    else:
        try:
            run_on_com(lambda: rhp_ctx.initialize())
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                c_content = f.read()

            c_splitter = RecursiveCharacterTextSplitter.from_language(
                language=Language.C, chunk_size=50000, chunk_overlap=1000
            )
            docs = c_splitter.create_documents([c_content])
            agent_executor = create_rhapsody_agent()

            for i, doc in enumerate(docs, 1):
                safe_invoke_agent(agent_executor, doc.page_content)
                if i < len(docs): time.sleep(5)

            run_on_com(lambda: rhp_ctx.project.save())
            print("\n🎉 全部语义结构化模型同步完毕！")
        except Exception as e:
            print(f"💥 致命错误: {e}")
        finally:
            try: com.stop()
            except: pass