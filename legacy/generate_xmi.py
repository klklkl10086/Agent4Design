"""
根据函数生成对应的xmi文件
"""
from __future__ import annotations

import os
import re
import uuid
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
from jinja2 import Template

# ==========================================
# 🌟 XMI 官方标准模板 (基于 Rhapsody test.xmi 提取)
# ==========================================
XMI_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<xmi:XMI xmi:version="2.1" xmlns:uml="http://schema.omg.org/spec/UML/2.1" xmlns:xmi="http://schema.omg.org/spec/XMI/2.1">
  <uml:Package xmi:id="Pkg_tmp" name="{{ function_name }}_Import">
    <packagedElement xmi:type="uml:Activity" xmi:id="{{ act_id }}" name="AD_{{ function_name }}">

      {% for node in nodes %}
      {% if node.type == 'Action' %}
      <node xmi:type="uml:OpaqueAction" xmi:id="{{ node.id }}" name="{{ node.name }}">
        <body>{{ node.desc | e }}</body>
      </node>
      {% elif node.type == 'Decision' %}
      <node xmi:type="uml:DecisionNode" xmi:id="{{ node.id }}" name="{{ node.name }}"/>
      {% elif node.type == 'Merge' %}
      <node xmi:type="uml:MergeNode" xmi:id="{{ node.id }}" name="{{ node.name }}"/>
      {% elif node.type == 'Initial' %}
      <node xmi:type="uml:InitialNode" xmi:id="{{ node.id }}" name="{{ node.name }}"/>
      {% elif node.type == 'Final' %}
      <node xmi:type="uml:ActivityFinalNode" xmi:id="{{ node.id }}" name="{{ node.name }}"/>
      {% endif %}
      {% endfor %}

      {% for edge in edges %}
      <edge xmi:type="uml:ControlFlow" xmi:id="{{ edge.id }}" name="{{ loop.index0 }}" source="{{ edge.source }}" target="{{ edge.target }}">
        {% if edge.guard %}
        <guard xmi:type="uml:LiteralString" xmi:id="{{ edge.id }}_guard" value="{{ edge.guard | e }}"/>
        {% endif %}
        <weight xmi:type="uml:LiteralInteger" xmi:id="{{ edge.id }}_weight" value="1"/>
      </edge>
      {% endfor %}

    </packagedElement>
  </uml:Package>
</xmi:XMI>
"""


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


# ==========================================
# Helpers (Only non-UI ones kept)
# ==========================================
def sanitize_name(raw_name: str) -> str:
    if not raw_name:
        return "Unnamed"
    return re.sub(r"[^a-zA-Z0-9_]", "", raw_name) or "Unnamed"


def get_effective_target(original_target):
    t = original_target
    valid = ("Project", "Package", "Class", "File", "Module")
    while t and getattr(t, "metaClass", "") not in valid:
        owner = getattr(t, "owner", None)
        if not owner:
            break
        t = owner
    return t or original_target


def _edge_endpoints(e: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], str]:
    s = e.get("source", e.get("from", e.get("source_id")))
    t = e.get("target", e.get("to", e.get("target_id")))
    g = e.get("guard", "")
    return s, t, g


def _is_explicit_merge_node(n: Dict[str, Any]) -> bool:
    nid = (n.get("id") or "").strip().lower()
    label = (n.get("label") or "").strip().lower()
    desc = (n.get("description") or "").strip().lower()
    return any(k in nid or k in label or k in desc for k in ("m_", "merge", "合并"))


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

    for e in edges:
        s, t, _ = _edge_endpoints(e)
        if s not in ids or t not in ids:
            return False, f"edge 引用非法节点: {s}->{t}"
    return True, ""


# ==========================================
# 🌟 Tool: Generate XMI & Import
# ==========================================
@tool(args_schema=SyncFunctionWithActivityArgs)
def sync_function_with_activity_diagram(
        name: str,
        return_type_info: CTypeInfo,
        arguments: List[FunctionArgument],
        graph: ActivityGraph
) -> str:
    """提取C代码逻辑并直接生成符合 Rhapsody 标准的 XMI 文件，替代手动绘图"""

    def _impl():
        rhp_ctx.ensure_connection_in_thread()
        graph_dict = graph.model_dump()

        ok, msg = _validate_graph(graph_dict)
        if not ok:
            return f"❌ 无效图定义: {msg}"

        # 1. 提取并组装节点信息，符合 XMI 要求的格式
        act_id = f"GUID_{uuid.uuid4().hex}"
        nodes_data = []
        for n in graph_dict.get("nodes", []):
            nid_safe = sanitize_name(n['id'])
            nodes_data.append({
                "id": f"Node_{nid_safe}",
                "type": n["type"],
                "name": sanitize_name(n.get("label", f"{n['type']}_{nid_safe}")),
                "desc": n.get("description", "")
            })

        edges_data = []
        for idx, e in enumerate(graph_dict.get("edges", [])):
            s, t, guard = _edge_endpoints(e)
            edges_data.append({
                "id": f"Edge_{idx}_{uuid.uuid4().hex[:6]}",
                "source": f"Node_{sanitize_name(s)}",
                "target": f"Node_{sanitize_name(t)}",
                "guard": guard
            })

        # 2. 渲染 Jinja2 XMI 模板
        template = Template(XMI_TEMPLATE)
        xmi_content = template.render(
            function_name=sanitize_name(name),
            act_id=act_id,
            nodes=nodes_data,
            edges=edges_data
        )

        # 3. 输出并保存 XMI 到本地目录
        output_dir = os.path.abspath("./xmi_read")
        os.makedirs(output_dir, exist_ok=True)
        xmi_path = os.path.join(output_dir, f"{sanitize_name(name)}.xmi")

        with open(xmi_path, "w", encoding="utf-8") as f:
            f.write(xmi_content)


        return f"✅ 成功: 为函数 {name} 生成 XMI -> {xmi_path}"

    return run_on_com(_impl)


# ==========================================
# Agent
# ==========================================
def create_rhapsody_agent():
    # 仅保留一个核心工具：直接处理 JSON 图数据
    tools = [sync_function_with_activity_diagram]

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
        ("system", """你是“C代码到Rhapsody结构”的逻辑提取专家。
请直接将代码逻辑抽象为 ActivityGraph 数据结构，通过调用工具 `sync_function_with_activity_diagram` 输出。

硬性要求：
0. 不需要生成过多的节点，对于没有分支的顺序执行代码部分，合并为 1 个 Action 节点即可！
1. 提取出的 node type 必须且只能是：Initial, Action, Decision, Merge, Final。
2. 必须有且仅有 1 个 Initial 节点和 1 个 Final 节点。
3. 代码中的 if/switch 分支结构，必须抽象为 Decision 节点。多条分支结束的地方，必须有一个 Merge 节点汇合。
4. 常规代码片段直接作为 Action 的 description 字段。
5. 边 (edges) 中遇到条件分支时，必须在 guard 字段填入分支条件（例如 "TRUE", "FALSE" 等）。
6. 禁止输出 Markdown 解释或 Mermaid！只能使用工具调用提交 JSON 参数。
"""),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=15,
        handle_parsing_errors=True,
    )


def safe_invoke_agent(executor, content: str):
    try:
        executor.invoke({"input": content})
    except Exception as e:
        print(f"⚠️ 当前代码块跳过: {e}")


# ==========================================
# Main
# ==========================================
if __name__ == "__main__":
    file_path = r"D:\project_design\VM_CEA2X_AGT\VW_CEA2.X_AGT\Source\CO\Project\CODE\CrashOutput.c"

    if not os.path.exists(file_path):
        print(f"❌ 找不到文件: {file_path}")
    else:
        try:
            # 1. 依然初始化环境
            run_on_com(lambda: rhp_ctx.initialize())

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                c_content = f.read()

            # 2. 对长代码进行拆分
            splitter = RecursiveCharacterTextSplitter.from_language(
                language=Language.C, chunk_size=8000, chunk_overlap=3000
            )
            docs = splitter.create_documents([c_content])

            # 3. 运行 Agent 生成 XMI
            agent_executor = create_rhapsody_agent()
            for i, doc in enumerate(docs, 1):
                safe_invoke_agent(agent_executor, doc.page_content)
                if i < len(docs):
                    time.sleep(1)

            print("\n🎉 全部逻辑提取完毕！请查看当前目录下的 xmi_outputs 文件夹，并导入 Rhapsody。")
        except Exception as e:
            print(f"💥 致命错误: {e}")
        finally:
            try:
                com.stop()
            except Exception:
                pass
