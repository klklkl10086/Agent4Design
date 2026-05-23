import os
import sys
import re
import json
import atexit
import threading
import win32com.client
import pythoncom
from typing import Optional, List
import time
import httpx
from win32com.client import CastTo
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from dotenv import load_dotenv
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from pydantic import BaseModel, Field

import threading
import pythoncom
import win32com.client
import concurrent.futures
from queue import Queue
import atexit
import time

class COMDispatcher:
    def init(self):
        self.q = Queue()
        self.thread = threading.Thread(target=self._loop, name="RhpCOM", daemon=True)
        self._ready = threading.Event()

    def start(self):
        self.thread.start()
        self._ready.wait()

    def _loop(self):
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        self._ready.set()
        try:
            while True:
                fn, fut = self.q.get()
                if fn is None:
                    fut.set_result(None)
                    break
                try:
                    res = fn()
                    fut.set_result(res)
                except Exception as e:
                    fut.set_exception(e)
        finally:
            try:
                pythoncom.CoUninitialize()
            except:
                pass

    def call(self, fn):
        fut = concurrent.futures.Future()
        self.q.put((fn, fut))
        return fut.result()

    def stop(self):
        fut = concurrent.futures.Future()
        self.q.put((None, fut))
        return fut.result()

# 实例化调度器
com = COMDispatcher()
com.init()
com.start()

def run_on_com(fn):
    return com.call(fn)


# ==========================================
# 1. 核心并发控制锁
# ==========================================
rhp_com_lock = threading.Lock()

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)
API_TOKEN = os.getenv("API_TOKEN")

if not API_TOKEN:
    raise EnvironmentError("🚨 启动失败: 未能从环境中获取 API_TOKEN")

BASE_URL = "https://vio.automotive-wan.com:446"
VIO_HEADERS = {"useLegacyCompletionsEndpoint": "false", "X-Tenant-ID": "default_tenant"}


class RhapsodyContext:
    def init(self):
        self.app = None
        self.target = None
        self.project = None
        self.target_name = ""
        self.target_meta = ""
        self.target_path = ""

    def initialize(self):
        # 仅在 COM 线程内调用
        self.app = win32com.client.dynamic.Dispatch("Rhapsody2.Application")
        target = self.app.getSelectedElement()
        if not target:
            raise RuntimeError("没有选中任何节点！")
        target = get_effective_target(target)
        self.target = target
        self.project = self.app.activeProject()
        self.target_name = self.target.name
        self.target_meta = self.target.metaClass
        self.target_path = getattr(self.target, 'fullPathName', self.target_name)
        print(f"🎯 成功锁定容器: [{self.target_name}] (路径: {self.target_path}, 元类: {self.target_meta})")

    def ensure_connection_in_thread(self):
        # 仅在 COM 线程内调用
        try:
            _ = self.app.name
            _ = self.target.name
            return
        except Exception as e:
            print(f"⚠️ 警告: COM 访问异常 (可能跨线程或断开): {e}")
            print(f"🔄 尝试在当前线程重连 [{self.target_name}]...")
            try:
                self.app = None
                self.target = None
                pythoncom.CoFreeUnusedLibraries()
                time.sleep(0.5)
                self.app = win32com.client.dynamic.Dispatch("Rhapsody2.Application")
                self.project = self.app.activeProject()
                found_target = None
                try:
                    if self.project and self.target_name and self.target_meta:
                        found_target = self.project.findNestedElementRecursive(self.target_name, self.target_meta)
                except Exception:
                    found_target = None
                if found_target:
                    self.target = found_target
                    print(f"🔄 重连成功: [{self.target.name}]")
                else:
                    self.target = self.app.getSelectedElement()
                    print(f"🔄 降级重连: 依附当前界面焦点")
            except Exception as e2:
                print(f"❌ 致命错误: 重连彻底失败: {e2}")
                raise

    def cleanup(self):
        pass

rhp_ctx = RhapsodyContext()
atexit.register(lambda: com.stop())
rhp_ctx = RhapsodyContext()
atexit.register(rhp_ctx.cleanup)


# ==========================================
# 🌟 C 语言类型的高级语义解析模型 (Pydantic)
# ==========================================
class CTypeInfo(BaseModel):
    base_type: str = Field(..., description="纯净的基础类型名，剥离所有修饰符。例如 'T_UBYTE', 'void'")
    is_const: bool = Field(False, description="声明中是否包含 const 修饰符")
    is_static: bool = Field(False, description="声明中是否包含 static 修饰符")
    pointer_modifier: str = Field("", description="指针或引用符号，例如 '*', '**', '&'。没有则为空")
    array_multiplicity: str = Field("", description="数组大小或多重性，例如 '10' 或 '5][10'。没有则为空")
    raw_declaration: str = Field(..., description="原始完整的 C 语言声明文本，用于备份")


class FunctionArgument(BaseModel):
    name: str = Field(..., description="参数的纯净变量名")
    type_info: CTypeInfo = Field(..., description="参数的结构化类型信息")


# ==========================================
# 辅助工具函数
# ==========================================
def sanitize_name(raw_name: str) -> str:
    if not raw_name: return "Unnamed"
    return re.sub(r'[^a-zA-Z0-9_]', '', raw_name) or "Unnamed"


def get_effective_target(original_target):
    """
    🚀 核心修复：允许 File 成为合法容器。
    在 C 语言工程中，变量 (Variable) 和函数 (Function) 通常是挂载在 Package 或 File 下的。
    """
    target = original_target
    # 移除了 Component（因为它不能装变量），加入了 File
    valid_containers = ("Project", "Package", "Class", "File","Module")

    while target and getattr(target, 'metaClass', '') not in valid_containers:
        owner = getattr(target, 'owner', None)
        if not owner: break
        target = owner

    return target if target else original_target


def cast_to_specific_interface(elem, meta_class: str):
    """修正接口映射：Variable 映射为 IRPVariable"""
    interface_map = {
        "Attribute": "IRPAttribute",
        "Variable": "IRPVariable",
        "Operation": "IRPOperation",
        "Function": "IRPOperation",
        "Argument": "IRPArgument",
        "Type": "IRPType",
    }
    target_interface = interface_map.get(meta_class)
    if target_interface:
        try:
            return CastTo(elem, target_interface)
        except Exception:
            pass
    return elem

def get_define_container(original):
    """容器选择逻辑：优先落点在 File/Package/Project"""
    t = original
    while t and getattr(t, 'metaClass', '') not in ('File', 'Package', 'Project'):
        t = getattr(t, 'owner', None)
    return t or original

def is_writable_container(elem):
    """深度检查容器是否可写（检查只读属性、Unit 锁定状态）"""
    try:
        # 1. 自身只读检查
        if hasattr(elem, "isReadOnly") and elem.isReadOnly():
            return False
    except Exception:
        pass
    try:
        # 2. 关联单元只读检查
        unit = elem.getSaveUnit() if hasattr(elem, "getSaveUnit") else None
        if unit and hasattr(unit, "isReadOnly") and unit.isReadOnly():
            return False
    except Exception:
        pass
    try:
        # 3. 配置管理锁状态检查 (假设 0 为解锁)
        if hasattr(elem, "getCMState") and elem.getCMState() not in (0,):
            return False
    except Exception:
        pass
    return True

def get_or_create_element(target, meta_class: str, name: str):
    # 1. 查找是否已经存在目标类型的元素
    try:
        col = target.getNestedElementsByMetaClass(meta_class, 0)
        if col:
            for i in range(1, col.Count + 1):
                item = col.Item(i)
                if getattr(item, "name", "") == name:
                    return cast_to_specific_interface(item, meta_class)
    except Exception:
        pass

    # 🌟 2. 新增核心防御：检查是否存在同名但类型不同的元素 (解决历史残留冲突)
    try:
        # 获取容器下所有的子元素
        all_elements = target.nestedElements
        if all_elements:
            for i in range(1, all_elements.Count + 1):
                item = all_elements.Item(i)
                if getattr(item, "name", "") == name:
                    existing_meta = getattr(item, "metaClass", "Unknown")
                    print(f"🧹 发现同名冲突元素 '{name}' (当前是 {existing_meta}，期望是 {meta_class})，正在自动清理...")
                    item.deleteFromProject()  # 删掉旧的错误类型
                    break
    except Exception as e:
        pass  # 如果删不掉就静默跳过，留给下面的创建逻辑去报错

    # 3. 权限检查
    if not is_writable_container(target):
        print(f"⚠️ 容器只读或被锁定: {getattr(target, 'fullPathName', getattr(target, 'name', '<?>'))}")
        return None

    # 4. 创建逻辑
    try:
        new_elem = target.addNewAggr(meta_class, name)
        if new_elem:
            return cast_to_specific_interface(new_elem, meta_class)
    except Exception as e:
        print(f"⚠️ 无法在 {getattr(target, 'metaClass', '?')} '{getattr(target, 'name', '?')}' 创建 {meta_class} '{name}': {e}")
    return None
# ==========================================
# ==========================================
# 🌟 基于 JSON 的 UML 实体类型赋值引擎 (已修复 COM 属性跳过问题)
# ==========================================
def assign_type_from_json(element, type_info: dict, element_meta: str, is_return: bool = False):
    base_type = type_info.get("base_type", "void").strip()
    is_const = type_info.get("is_const", False)
    ptr_mod = type_info.get("pointer_modifier", "").strip()
    array_mult = type_info.get("array_multiplicity", "").strip()

    if array_mult and not is_return and element_meta in ("Attribute", "Variable", "Argument"):
        try: element.multiplicity = array_mult
        except: pass
        try: element.setPropertyValue(f"C_CG::{element_meta}::Array", f"[{array_mult}]")
        except: pass

    classifier = None
    if base_type and base_type != "void":
        try:
            classifier = rhp_ctx.project.findNestedElementRecursive(base_type, "Type")
            if not classifier:
                classifier = rhp_ctx.project.findNestedElementRecursive(base_type, "Class")
        except:
            pass

        # 🛠️ 自动补全缺失的类型：防止因为没找到类型导致空白
        if not classifier:
            try:
                container = get_effective_target(rhp_ctx.target)
                classifier = get_or_create_element(container, "Type", base_type)
                if classifier:
                    try: classifier.kind = "Language"
                    except: pass
                    try: classifier.declaration = base_type
                    except: pass
                    print(f"🛠️ 自动补全缺失的占位类型: {base_type}")
            except:
                pass

    clean_textual_type = f"{'const ' if is_const else ''}{base_type} {ptr_mod}".strip()

    # ---------------- 场景 A：找到引用 (直接暴力赋值，弃用 hasattr) ----------------
    if classifier:
        if is_return:
            try: element.returns = classifier
            except: pass
        else:
            try: element.type = classifier
            except: pass
            # 有些版本 API 叫 typeOf
            try: element.typeOf = classifier
            except: pass

        if is_const:
            try: element.isConstant = True
            except: pass
            try: element.isConst = True
            except: pass

        if ptr_mod and not is_return:
            try: element.typeModifier = ptr_mod
            except: pass

    # ---------------- 场景 B：兜底纯文本写入 ----------------
    if is_return:
        try: element.setReturnTypeDeclaration(clean_textual_type)
        except: pass
    else:
        try: element.setTypeDeclaration(clean_textual_type)
        except: pass
# ==========================================
# 🌟 核心进化：为每个工具显式定义参数 Schema
# ==========================================

def get_or_create_argument(op, name: str):
    """专门处理 IRPOperation 的参数创建/查找"""
    # 优先从 arguments 集合查重
    try:
        col = getattr(op, "arguments", None)
        if col:
            for i in range(1, col.Count + 1):
                it = col.Item(i)
                if getattr(it, "name", "") == name:
                    return CastTo(it, "IRPArgument")
    except Exception:
        pass

    # 用 addArgument 正确创建
    try:
        arg = op.addArgument(name)
        return CastTo(arg, "IRPArgument")
    except Exception as e:
        print(f"⚠️ 无法创建参数 '{name}': {e}")
        return None


def get_or_create_op_like(target, name: str):
    """修正创建 Operation/Function 的选择"""
    mc = getattr(target, "metaClass", "")
    # Class/Block 下建 Operation，其它容器建 Function
    meta_cls = "Operation" if mc in ("Class", "Block") else "Function"
    return get_or_create_element(target, meta_cls, sanitize_name(name)), meta_cls





class SyncMacroArgs(BaseModel):
    name: str = Field(..., description="宏的名称")
    value: str = Field(..., description="宏的值")
    type_info: Optional[CTypeInfo] = Field(default=None, description="宏的结构化类型信息")


class SyncVariableArgs(BaseModel):
    name: str = Field(..., description="变量的名称")
    type_info: CTypeInfo = Field(..., description="变量的结构化类型信息")
    initial_value: Optional[str] = Field(default=None, description="变量的初始值")


class SyncFunctionArgs(BaseModel):
    name: str = Field(..., description="函数的名称")
    return_type_info: CTypeInfo = Field(..., description="函数返回值的结构化类型信息")
    arguments: List[FunctionArgument] = Field(default_factory=list, description="函数的参数列表")


# ==========================================
# 🌟 活动图 (Activity Diagram) 的结构化解析模型
# ==========================================
class FlowNode(BaseModel):
    id: str = Field(..., description="节点唯一标识符，如 'n1', 'n2'")
    name: str = Field(..., description="节点内容，例如 'x = a + b;' 或 'if (x > 0)' 或 'switch (state)'")
    type: str = Field(..., description="节点类型，只能是: 'Initial', 'Action', 'Final'")

class FlowEdge(BaseModel):
    source_id: str = Field(..., description="源节点ID")
    target_id: str = Field(..., description="目标节点ID")
    guard: Optional[str] = Field(
        default="", 
        description="转移条件(Guard)。对于 if/else 分支，必须填写 'true' 或 'false'；对于 switch 分支，必须填写对应的 'case X' 或 'default'；普通顺序执行留空。"
    )

class SyncActivityDiagramArgs(BaseModel):
    function_name: str = Field(..., description="需要绘制活动图的所在函数名称")
    nodes: List[FlowNode] = Field(..., description="活动图的所有节点列表（必须包含1个Initial，至少1个Action和Final）")
    edges: List[FlowEdge] = Field(..., description="节点之间的控制流连线列表")


@tool(args_schema=SyncActivityDiagramArgs)
def sync_activity_diagram_to_rhapsody(function_name: str, nodes: List[FlowNode], edges: List[FlowEdge]) -> str:
    """根据提取的 C 代码内部执行逻辑，为函数生成并绘制 UML 活动图(流程图)。"""
    
    def _impl():
        rhp_ctx.ensure_connection_in_thread()
        try:
            # 1. 查找目标函数
            target = get_effective_target(rhp_ctx.target)
            op, meta_cls = get_or_create_op_like(target, function_name)
            if not op:
                return f"❌ 失败: 无法定位或创建函数 {function_name}"

            # 2. 获取或创建 ActivityDiagram
            ad_el = None
            nested = op.getNestedElementsByMetaClass("ActivityDiagram", 0)
            if nested and nested.Count > 0:
                ad_el = nested.Item(1)

            if ad_el:
                flowchart = ad_el if hasattr(ad_el, "getFlowchartDiagram") else None
                ad = flowchart.getFlowchartDiagram() if flowchart else ad_el
            else:
                flowchart = op.addActivityDiagram()
                ad = flowchart.getFlowchartDiagram()

            if not ad:
                return "❌ 失败: 无法获取 ActivityDiagram 视图。"

            ag = ad.getStatechart()
            ag_state = ag.rootState
            
            print(f"开始为 {function_name} 构建活动图模型...")

            # 3. 构建模型层 (Model Layer)
            model_map = {}
            initial_id = None

            # 创建节点模型
            for node in nodes:
                if node.type == "Initial":
                    initial_id = node.id
                    # Initial 不需要实体节点，仅做标记
                elif node.type == "Final":
                    final_node = ag_state.addActivityFinal()
                    model_map[node.id] = final_node
                else: # Action
                    action_node = ag_state.addActivityAction(node.name) if hasattr(ag_state, "addActivityAction") else ag_state.addState(node.name)
                    model_map[node.id] = CastTo(action_node, "IRPState")

            edge_models = []
            
            # 创建连线模型
            for edge in edges:
                src_id = edge.source_id
                tgt_id = edge.target_id
                
                if src_id == initial_id:
                    tgt_model = model_map.get(tgt_id)
                    if tgt_model:
                        init_flow = ag_state.createDefaultTransition(tgt_model)
                        # 【核心修正经验】：强制校准底层 Target
                        init_flow.itsTarget = tgt_model 
                        edge_models.append({
                            "type": "InitialFlow", "model": init_flow, 
                            "src_id": src_id, "tgt_id": tgt_id
                        })
                else:
                    src_model = model_map.get(src_id)
                    tgt_model = model_map.get(tgt_id)
                    if src_model and tgt_model:
                        trans = src_model.addTransition(tgt_model)
                        trans.itsTarget = tgt_model
                        if edge.guard:
                            try:
                                trans.setItsLabel("", f"[{edge.guard}]", "")
                            except: pass
                        edge_models.append({
                            "type": "Transition", "model": trans, 
                            "src_id": src_id, "tgt_id": tgt_id
                        })

            # 4. 极简自动布局计算引擎 (避免坐标重叠导致画图引擎崩溃)
            levels = {n.id: 0 for n in nodes}
            # 简单拓扑分层推导
            for _ in range(len(nodes)):
                for edge in edges:
                    if levels[edge.target_id] <= levels[edge.source_id]:
                        levels[edge.target_id] = levels[edge.source_id] + 1

            layout = {}
            level_counts = {}
            for n in nodes:
                lvl = levels[n.id]
                idx = level_counts.get(lvl, 0)
                level_counts[lvl] = idx + 1
                # 每一层高度递增 120，同一层若有多个并列节点向右偏移 200
                x = 300 + (idx * 200)
                y = 50 + (lvl * 120)
                layout[n.id] = (x, y)

            print("模型构建完毕，开始绘图映射...")
            
            # 5. 绘图映射 (View Layer)
            graph_map = {}
            for node in nodes:
                if node.id not in layout: continue
                x, y = layout[node.id]
                
                if node.type == "Initial":
                    pass # 绝对不画初始节点！
                elif node.type == "Final":
                    final_model = model_map.get(node.id)
                    if final_model:
                        graph_map[node.id] = ad.AddNewNodeForElement(final_model, x, y, 25, 25)
                else:
                    action_model = model_map.get(node.id)
                    if action_model:
                        # 【核心修正经验】： Action必须给 120, 50 尺寸！
                        graph_map[node.id] = ad.AddNewNodeForElement(action_model, x, y, 120, 50)

            # 绘制连线
            for edata in edge_models:
                src_id = edata["src_id"]
                tgt_id = edata["tgt_id"]
                trans = edata["model"]

                if edata["type"] == "InitialFlow":
                    tgt_graph = graph_map.get(tgt_id)
                    if tgt_graph:
                        # 【核心修正经验】： 起点设为空(None)，Rhapsody 自动画黑点
                        ad.AddNewEdgeForElement(trans, None, 300, 50, tgt_graph, 0, 0)
                else:
                    src_graph = graph_map.get(src_id)
                    tgt_graph = graph_map.get(tgt_id)
                    if src_graph and tgt_graph:
                        ad.AddNewEdgeForElement(trans, src_graph, 0, 0, tgt_graph, 0, 0)

            return f"✅ 成功: 函数 {function_name} 活动图绘制完毕！"
            
        except Exception as e:
            return f"❌ 失败: 绘制活动图异常 - {str(e)}"

    return run_on_com(_impl)


@tool(args_schema=SyncMacroArgs)
def sync_macro_to_rhapsody(name: str, value: str, type_info: Optional[CTypeInfo] = None) -> str:
    """同步宏定义到 Rhapsody 模型中。"""

    def _impl():
        rhp_ctx.ensure_connection_in_thread()
        try:
            clean_name = sanitize_name(name)

            # 🌟 核心修复：直接使用 get_effective_target，坚决不向外跳跃！
            # 这样当你选中 Class 时，它就会老老实实呆在 Class 里
            container = get_effective_target(rhp_ctx.target)
            container_meta = getattr(container, 'metaClass', '')

            # 智能判断元类：在 Class 下建 Attribute，在 Package/File 下建 Variable
            meta_cls = "Attribute" if container_meta in ("Class", "Block") else "Variable"

            elem = get_or_create_element(container, meta_cls, clean_name)
            if not elem:
                return f"❌ 失败: 无法在 {container_meta} '{getattr(container, 'name', '未知')}' 下创建 {meta_cls}"

            # 添加 <<Define>> 构造型
            try:
                elem.addStereotype("Define", meta_cls)
            except:
                pass

            # 宏的值赋给 Initial Value (defaultValue)
            val = (value or "").strip(" =;")
            try:
                elem.defaultValue = val
            except:
                pass

            # 设置类型
            if type_info:
                assign_type_from_json(elem, type_info.dict(), meta_cls, is_return=False)

            return f"✅ 成功: 宏 {clean_name} (作为 {meta_cls} 创建)"

        except Exception as e:
            return f"❌ 失败: 宏 {name} 异常 - {str(e)}"

    return run_on_com(_impl)



@tool(args_schema=SyncVariableArgs)
def sync_variable_to_rhapsody(name: str, type_info: CTypeInfo, initial_value: Optional[str] = None) -> str:
    """同步变量定义到 Rhapsody 模型中。"""
    def _impl():
        rhp_ctx.ensure_connection_in_thread()
        try:
            clean_name = sanitize_name(name)
            target = get_effective_target(rhp_ctx.target)
            target_meta = getattr(target, 'metaClass', '')
            meta_cls = "Attribute" if target_meta == "Class" else "Variable"
            var = get_or_create_element(target, meta_cls, clean_name)
            if not var: return f"❌ 失败: 变量 {clean_name} 被拒绝。"
            assign_type_from_json(var, type_info.dict(), meta_cls, is_return=False)
            if type_info.is_static:
                try: var.isStatic = True
                except: pass
            if initial_value:
                try: var.defaultValue = initial_value.strip(' =;')
                except: pass
            return f"✅ 成功: 变量 {clean_name}"
        except Exception as e:
            return f"❌ 失败: 变量 {name} 异常 - {str(e)}"
    return run_on_com(_impl)


@tool(args_schema=SyncFunctionArgs)
def sync_function_to_rhapsody(name: str, return_type_info: CTypeInfo, arguments: List[FunctionArgument]) -> str:
    """同步函数定义到 Rhapsody 模型中。"""

    def _impl():
        rhp_ctx.ensure_connection_in_thread()
        try:
            # 1. 确定容器与操作类型
            op, meta_cls = get_or_create_op_like(get_effective_target(rhp_ctx.target), name)
            if not op:
                return f"❌ 失败: 无法创建{meta_cls} {name}"

            # 2. 返回类型
            assign_type_from_json(op, return_type_info.dict(), meta_cls, is_return=True)

            # 3. 静态属性
            try:
                if return_type_info.is_static:
                    op.isStatic = True
            except:
                pass

            # 4. void 无参处理
            if len(arguments) == 1 and arguments[0].type_info.base_type.strip() == "void":
                return f"✅ 成功: {meta_cls} {sanitize_name(name)}（无参）"

            # 5. 参数处理
            for idx, a in enumerate(arguments):
                arg_name = sanitize_name(a.name) or f"arg{idx + 1}"
                arg_el = get_or_create_argument(op, arg_name)
                if not arg_el:
                    continue

                try:
                    arg_el.direction = 0  # 显式设为 in
                except:
                    pass

                # 统一复用类型赋值引擎，保留指针/数组结构
                assign_type_from_json(arg_el, a.type_info.dict(), "Argument", is_return=False)

            return f"✅ 成功: {meta_cls} {sanitize_name(name)}"
        except Exception as e:
            return f"❌ 失败: {e}"

    return run_on_com(_impl)


# ==========================================
# Agent 执行器构建
# ==========================================
def create_rhapsody_agent():
    # 加入新的工具
    tools = [
        sync_macro_to_rhapsody, 
        sync_variable_to_rhapsody, 
        sync_function_to_rhapsody,
        sync_activity_diagram_to_rhapsody  # 🌟 新增的活动图工具
    ]

    custom_http_client = httpx.Client(
        timeout=httpx.Timeout(connect=60.0, read=300.0, write=60.0, pool=60.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    )

    llm = ChatOpenAI(
        model="VIO:Claude 4.6 Sonnet",
        openai_api_base=BASE_URL,
        openai_api_key=API_TOKEN,
        default_headers={**VIO_HEADERS},
        temperature=0.1,
        http_client=custom_http_client,
        max_retries=3,
        streaming=False
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个顶级的 C 语言语义分析与 Rhapsody UML 架构师。

            【核心任务与能力】
            你的任务是从 C 代码中精确提取变量、函数定义，以及**函数的内部流程逻辑**。

            【结构化 JSON 解析规则】（极度重要）
            1. 基本提取类型: `base_type` 剥离一切（如 `T_UBYTE`, `void`）。`pointer_modifier`填指针符号。`array_multiplicity`填数组大小。
            
            【流程分析规则】（新能力）
            当分析到包含逻辑块的函数时，请调用 `sync_activity_diagram_to_rhapsody` 为其绘制执行流程。
            - nodes需包含：1个 'Initial' 节点，若干 'Action' 节点（对应代码步骤），1个或多个 'Final' 节点。
            - 提取代码的顺序，用 edges 连线（如果有条件判断，可在 guard 属性填入条件）。
            - 例如：[Initial] -> [Action: a = b+1] -> [Action: call_func()] -> [Final]
            
            【并发限制】
            必须按顺序串行调用工具，严禁并发。"""),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=30)

@retry(
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(Exception),
    reraise=True
)
def safe_invoke_agent(executor, content):
    try:
        executor.invoke({"input": content})
    except Exception as e:
        print(f"⚠️ 网络或执行异常，进行指数退避重试... 详情: {e}")
        raise e


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
                language=Language.C, chunk_size=8000, chunk_overlap=500
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
            try:
                com.stop()
            except:
                pass