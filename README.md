# Agent4Design

Agent4Design is a local IBM Rhapsody modeling assistant. It turns C source code
and structured model specifications into approval-gated, verifiable Rhapsody
updates through COM, XMI Toolkit, HTTP, MCP, LangGraph, and OpenAI-compatible
LLM adapters.

---

## 中文文档

### 项目定位

Agent4Design 的目标不是让 LLM 直接操作 Rhapsody COM，而是把建模流程拆成可验证的本地服务：

```text
C 代码 / 用户请求
  -> Pydantic 结构化模型
  -> 只读计划
  -> 人工批准
  -> Rhapsody COM 写入 / XMI 导入
  -> COM 验证报告
```

所有外部入口都走同一个稳定边界：

```text
LLM / MCP / HTTP / LangGraph
  -> Agent4DesignService
  -> services
  -> rhapsody / xmi
```

这意味着：

- LLM、MCP、HTTP 和 LangGraph 不直接调用 COM。
- 写入 Rhapsody 前默认需要显式批准。
- 项目保存必须显式执行。
- COM 操作在专用 STA 线程中串行执行。
- 代码路径抽取不使用正则表达式猜 C 语法，而是用 tree-sitter 分段，再由 LLM 输出严格 JSON。

### 环境要求

- Windows。
- Python 3.10 或更高版本。
- IBM Rhapsody 已安装，并且 Python 进程能访问当前桌面会话中的 Rhapsody。
- 如需 COM 写入：安装 `pywin32`。
- 如需代码路径抽取：安装 `tree-sitter` parser extra。
- 如需 LLM Agent：准备 OpenAI-compatible API key。
- 如需活动图 XMI 导入：准备 IBM Rhapsody XMI Toolkit batch 文件路径。

### 安装

基础 Rhapsody COM 能力：

```powershell
python -m pip install -e ".[rhapsody]"
```

开发或完整功能安装：

```powershell
python -m pip install -e ".[rhapsody,parser,llm,mcp,graph,dev]"
```

可选 extras：

```text
rhapsody  pywin32 / Rhapsody COM
parser    tree-sitter C parser
llm       OpenAI-compatible Agent
mcp       MCP Server
graph     LangGraph workflow
dev       pytest
```

安装后也可以使用脚本入口：

```powershell
agent4design-api
agent4design-agent
agent4design-mcp
```

### 配置方式

Agent4Design 只通过 `.env` 文件读取运行配置。系统环境变量会被忽略，避免 PowerShell、Windows 用户环境或旧服务残留变量影响本次运行。

配置查找顺序：

```text
当前工作目录 .env
项目根目录 .env
agent4design/.env
```

前面的 `.env` 优先级更高。推荐在项目根目录创建或编辑 `.env`。

最小配置：

```dotenv
AGENT4DESIGN_REQUIRE_WRITE_APPROVAL=true
AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE=false
```

LLM / VIO 配置：

```dotenv
AGENT4DESIGN_LLM_API_KEY=your-api-key
AGENT4DESIGN_LLM_MODEL=VIO:Claude 4.6 Sonnet
AGENT4DESIGN_LLM_BASE_URL=https://vio.automotive-wan.com:446
AGENT4DESIGN_LLM_TEMPERATURE=0.1
AGENT4DESIGN_LLM_MAX_TOOL_ROUNDS=30
AGENT4DESIGN_LLM_MAX_RETRIES=3
AGENT4DESIGN_LLM_HEADERS={"useLegacyCompletionsEndpoint":"false","X-Tenant-ID":"default_tenant"}
```

也兼容 legacy 变量名，但仍然必须写在 `.env` 文件中：

```dotenv
API_TOKEN=your-api-key
BASE_URL=https://vio.automotive-wan.com:446
VIO_HEADERS={"useLegacyCompletionsEndpoint":"false","X-Tenant-ID":"default_tenant"}
```

HTTP / MCP token：

```dotenv
AGENT4DESIGN_API_TOKEN=your-api-token
AGENT4DESIGN_MCP_TOKEN=your-mcp-token
```

活动图 XMI 导入：

```dotenv
AGENT4DESIGN_XMI_TOOLKIT_BAT=C:\path\to\XMI4Rhapsody.bat
AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true
AGENT4DESIGN_XMI_OUTPUT_DIR=xmi_read
AGENT4DESIGN_XMI_LOG_DIR=xmi_import_logs
AGENT4DESIGN_XMI_TIMEOUT=600
```

SSL / CA 配置：

```dotenv
AGENT4DESIGN_LLM_SSL_VERIFY=true
AGENT4DESIGN_LLM_CA_BUNDLE=C:\path\to\vio_root_ca.pem
```

如果当前只是临时绕过证书问题：

```dotenv
AGENT4DESIGN_LLM_SSL_VERIFY=false
```

### Rhapsody 使用流程

1. 打开 Rhapsody。
2. 加载目标项目。
3. 在 Rhapsody 浏览器中选择可写目标。
4. 如果要创建函数，推荐选择 `Class` 或 `Block`。
5. 初始化 Agent4Design 上下文：

```text
initialize_rhapsody
```

如果 Rhapsody 中切换了选择目标，调用：

```text
select_rhapsody_target
```

推荐完整同步流程：

```text
initialize_rhapsody
  -> refresh_type_registry
  -> plan_agent4design_sync
  -> 人工检查计划
  -> execute_agent4design_sync(approved=true)
  -> verify_rhapsody_model
  -> save_rhapsody_project(approved=true)
```

### 函数创建目标限制

函数同步现在只允许写入 `Class` 或 `Block`，并创建为 `Operation`。

如果当前目标是 `Module`，计划阶段和执行阶段会拒绝函数写入。这是为了避免 Rhapsody COM 抛出类似：

```text
发生意外 (-2147221495)
```

正确处理方式：

```text
在 Rhapsody 中选择目标 Class 或 Block
  -> 调用 select_rhapsody_target
  -> 重新执行 plan / execute
```

不要依赖重试。目标元类不正确时，重试不会解决问题。

### 常用工具

`Agent4DesignService` 暴露以下工具：

```text
initialize_rhapsody
select_rhapsody_target
get_rhapsody_context
refresh_type_registry
save_type_index
load_type_index
extract_code_path_model
plan_code_path_modeling
execute_code_path_modeling
plan_agent4design_sync
execute_agent4design_sync
verify_rhapsody_model
save_rhapsody_project
```

工具说明：

```text
initialize_rhapsody          连接 Rhapsody，可选择当前 GUI 目标
select_rhapsody_target      从 Rhapsody GUI 刷新当前写入目标
get_rhapsody_context        返回当前项目和目标摘要
refresh_type_registry       扫描项目 Type / Class 元数据
save_type_index             保存类型索引用于诊断
load_type_index             加载类型索引
extract_code_path_model     对 C 路径做 parser 分段和 LLM 结构化抽取
plan_code_path_modeling     从代码路径生成只读同步计划
execute_code_path_modeling  经批准后执行代码路径建模
plan_agent4design_sync      对结构化模型请求生成只读计划
execute_agent4design_sync   经批准后执行 COM / XMI 写入和验证
verify_rhapsody_model       只读验证模型元素
save_rhapsody_project       经批准后保存当前项目
```

### 代码路径抽取

`extract_code_path_model` 不使用正则表达式匹配 C 代码结构。当前流程是：

```text
指定 .c / .h 路径
  -> tree-sitter 按语法节点分段
  -> 每段保留原始源码、行号、byte offset 和上下文
  -> LLM 对每段输出严格 JSON
  -> Pydantic 校验
  -> 合并为 Rhapsody 建模规格
```

需要安装：

```powershell
python -m pip install -e ".[parser]"
```

通过 LLM CLI 使用时，LLM adapter 会把同一个 OpenAI-compatible client 注入为代码片段抽取器。

通过 HTTP / MCP 直接调用时，如果没有内部 LLM 抽取器，可以只获取 parser 分段：

```json
{
  "path": "src",
  "recursive": true,
  "include_headers": true,
  "include_activities": true,
  "require_model_extraction": false
}
```

返回结果会包含 `segments`，每个 segment 包含：

```text
path
language
kind
symbol
start_line / end_line
start_byte / end_byte
source
context
```

经 LLM 抽取后，结果会合并为：

```text
macros
variables
functions
activities
warnings
errors
```

### HTTP API

启动：

```powershell
python -m agent4design.adapters.http
```

默认地址：

```text
http://127.0.0.1:8765
```

检查服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/tools
```

调用通用工具接口：

```powershell
$body = @{
  name = "initialize_rhapsody"
  arguments = @{ select_current_target = $true }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/call `
  -ContentType application/json `
  -Body $body
```

调用命名工具接口：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/tools/get_rhapsody_context `
  -ContentType application/json `
  -Body "{}"
```

如果 HTTP 监听非 localhost 地址，必须在 `.env` 中配置：

```dotenv
AGENT4DESIGN_API_TOKEN=your-token
```

### MCP 服务

本地 stdio MCP：

```powershell
python -m agent4design.adapters.mcp --transport stdio
```

远程 streamable HTTP MCP：

```powershell
python -m agent4design.adapters.mcp `
  --transport streamable-http `
  --host 0.0.0.0 `
  --port 8766 `
  --path /mcp `
  --token your-secret-token
```

远程 MCP 仍然操作运行该服务的 Windows 机器上的本地 Rhapsody 会话。不要直接暴露到公网。

### LLM Agent

启动交互式 Agent：

```powershell
python -m agent4design.adapters.llm
```

默认启动方式支持读操作、计划生成，以及在对话中使用明确授权语句触发一次写入。
推荐先用这个模式完成日常同步流程：

```text
You> 建立连接
Agent> ... initialize_rhapsody ...

You> 根据 "D:\project\src\CD_AppMain.c" 这个路径的代码同步除了活动图之外的所有元素
Agent> ... read_code_path / plan_agent4design_sync ...

You> 批准执行
Agent> ... execute_agent4design_sync ...
```

可识别的明确授权示例：

```text
批准执行
同意执行
授权执行
确认执行
允许执行
允许写入
刷新并执行同步不需要询问
```

如果回复中包含否定语义，例如“不批准执行”“不授权”“取消”，本地 adapter 不会放行写工具。

单轮输入：

```powershell
python -m agent4design.adapters.llm --message "Inspect the selected Rhapsody target."
```

需要额外终端确认时，使用 `--allow-writes`：

```powershell
python -m agent4design.adapters.llm --allow-writes
```

在该模式下，如果最新用户消息已经明确授权，写入会直接放行；否则终端会显示写工具名称和 JSON 参数，并提示：

```text
Approve this Rhapsody write? [y/N]
```

输入 `y` 或 `yes` 才会继续执行写入。

推荐完整流程：

```text
1. 打开 Rhapsody，并加载目标项目。
2. 在 Rhapsody Browser 中选中要写入的 Class 或 Block。
3. 启动 Agent：python -m agent4design.adapters.llm
4. 输入“建立连接”或“初始化 Rhapsody”。
5. 如果切换过 Rhapsody 选中目标，输入“刷新目标”。
6. 输入代码路径同步请求，例如：
   根据 "D:\project\src\CD_AppMain.c" 这个路径的代码同步除了活动图之外的所有元素
7. 检查 Agent 输出的 plan_agent4design_sync 计划，确认 create / update / reject 项。
8. 确认无误后输入“批准执行”。
9. 以 execute_agent4design_sync 和 verify_rhapsody_model 的工具结果判断是否成功。
10. 需要保存项目时，再明确要求保存并授权。
```

常见问题：

- 如果一直报 `Human approval was not granted for this write operation.`，请确认使用的是当前代码，并且最新一条用户消息是明确授权语句。
- 如果计划中的函数被拒绝，先检查 Rhapsody 当前目标是否为 `Class` 或 `Block`，然后执行 `select_rhapsody_target`。
- 如果类型解析失败，先调用 `refresh_type_registry`，或在 Rhapsody 项目中补齐缺失类型。
- 写入不会自动保存项目，保存需要单独调用 `save_rhapsody_project` 并授权。

安全边界：

- 模型看不到 `approved` schema。
- 模型即使生成 `approved=true` 也会被本地 adapter 覆盖。
- 没有明确聊天授权或终端确认时，写入工具会被拒绝。
- 写入成功与否以工具返回结果为准。

### LangGraph 工作流

LangGraph 是可选能力，用于可恢复的计划、批准、执行、验证流程。

安装：

```powershell
python -m pip install -e ".[graph]"
```

核心入口：

```text
agent4design.workflows.sync_graph.build_sync_graph
agent4design.workflows.sync_graph.create_sqlite_checkpointer
```

当前工作流仍然调用 `Agent4DesignService`，不直接访问 COM。

### 活动图 XMI 导入

活动图导入依赖 IBM Rhapsody XMI Toolkit。没有配置 Toolkit 时，活动图导入会失败：

```text
Activity sync is not configured
```

这是启动配置问题，不是可重试问题。配置 `.env` 后需要重启 HTTP、MCP 或 LLM Agent 服务。

当前活动图导入仍是实验能力，但已按 Rhapsody 导出的正确 XMI 结构挂载到函数：执行前会在当前选中目标下解析同名 `Operation` 的 GUID，生成 `AD_函数名` 活动图，并在 XMI 中写入 `specification="OperationGUID"`。如果对应函数不在本次同步计划中、且当前模型中也找不到同名 Operation，计划阶段会拒绝该活动图。

### 项目结构

```text
Agent4Design/
  README.md
  pyproject.toml
  agent4design/
    adapters/
      http/               本地 JSON-over-HTTP API
      llm/                OpenAI-compatible tool-calling Agent
      mcp/                MCP Server adapter
    docs/                 设计说明、API 使用说明、历史资料
    domain/
      models.py           Pydantic 数据模型
      validators.py       Activity graph 等校验逻辑
    rhapsody/
      com_runtime.py      COM STA 线程调度
      context.py          Rhapsody 应用、项目、目标上下文
      repository.py       COM 读写仓库
      type_registry.py    Type / Class 元数据索引
      verifier.py         只读验证器
    services/
      agent_service.py    统一 Agent-facing 门面
      code_extractor.py   tree-sitter 分段和 LLM 抽取协议
      model_sync.py       宏、变量、函数同步服务
      sync_plan.py        写入前只读计划
      verification.py     验证服务封装
      activity_sync.py    XMI 生成和导入服务
    tools/
      tool.py             通用工具函数
    workflows/
      sync_graph.py       LangGraph 工作流构建
      nodes.py            工作流节点
      state.py            工作流状态
    xmi/
      generator.py        Activity XMI 生成
      importer.py         XMI Toolkit 调用
  tests/                  离线测试
  legacy/                 历史实验脚本
```

### 设计边界

核心边界：

```text
外部 adapter
  -> Agent4DesignService.call(name, arguments)
  -> Pydantic request validation
  -> service layer
  -> repository / XMI toolkit
```

原则：

- adapter 只做协议转换。
- service 层负责业务编排。
- rhapsody 层负责 COM 细节。
- xmi 层负责 XMI 文件生成和导入。
- LLM 只负责调用工具和抽取结构化 JSON，不直接操作 COM。

### 故障排查

#### `-2147221495`

通常是当前目标不适合创建函数，例如选中了 `Module`。

处理：

```text
选择 Class 或 Block
调用 select_rhapsody_target
重新执行 plan / execute
```

#### `Activity sync is not configured`

原因是未配置 XMI Toolkit。

处理：

```dotenv
AGENT4DESIGN_XMI_TOOLKIT_BAT=C:\path\to\XMI4Rhapsody.bat
AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true
```

然后重启服务。

#### 自定义类型找不到

默认不会自动创建未知自定义类型，避免拼写错误污染模型。

处理：

```text
先在 Rhapsody 项目中创建正确类型
或在 .env 中设置 AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE=true
```

#### LLM API key 没生效

确认配置写在 `.env` 文件里，而不是系统环境变量里。

可用键：

```dotenv
AGENT4DESIGN_LLM_API_KEY=...
```

或 legacy 键：

```dotenv
API_TOKEN=...
```

#### tree-sitter 未安装

错误信息通常会提示安装 parser extra。

处理：

```powershell
python -m pip install -e ".[parser]"
```

### 测试

运行离线测试：

```powershell
python -m pytest -q
```

如果机器上有多个 Python，请使用安装了依赖的解释器：

```powershell
D:\Environment\miniconda\python.exe -m pytest -q
```

编译检查：

```powershell
python -m compileall -q agent4design tests
```

---

## English Documentation

### Purpose

Agent4Design is a local modeling assistant for IBM Rhapsody. It converts C code
and structured model data into verifiable Rhapsody updates through a stable
service boundary.

The intended workflow is:

```text
C source / user request
  -> Pydantic structured model
  -> read-only plan
  -> human approval
  -> Rhapsody COM write / XMI import
  -> COM verification report
```

Every adapter calls the same facade:

```text
LLM / MCP / HTTP / LangGraph
  -> Agent4DesignService
  -> services
  -> rhapsody / xmi
```

Important defaults:

- LLM, MCP, HTTP, and LangGraph do not call COM directly.
- Rhapsody writes require explicit approval by default.
- Saving the project is explicit.
- COM operations run serially on a dedicated STA thread.
- Code path extraction uses tree-sitter syntax segmentation plus strict LLM JSON extraction, not regex-based C parsing.

### Requirements

- Windows.
- Python 3.10 or newer.
- IBM Rhapsody installed and available in the current desktop session.
- `pywin32` for COM access.
- tree-sitter parser extra for code path extraction.
- An OpenAI-compatible API key for the LLM Agent.
- IBM Rhapsody XMI Toolkit if you want standalone activity import.

### Installation

Install the Rhapsody COM support:

```powershell
python -m pip install -e ".[rhapsody]"
```

Install all optional adapters and development tools:

```powershell
python -m pip install -e ".[rhapsody,parser,llm,mcp,graph,dev]"
```

Optional extras:

```text
rhapsody  pywin32 / Rhapsody COM
parser    tree-sitter C parser
llm       OpenAI-compatible Agent
mcp       MCP Server
graph     LangGraph workflow
dev       pytest
```

Script entry points:

```powershell
agent4design-api
agent4design-agent
agent4design-mcp
```

### Configuration

Agent4Design reads runtime settings only from `.env` files. OS-level
environment variables are intentionally ignored.

Lookup order:

```text
current working directory .env
project root .env
agent4design/.env
```

Earlier files have higher priority. The recommended place is the project root
`.env`.

Minimal configuration:

```dotenv
AGENT4DESIGN_REQUIRE_WRITE_APPROVAL=true
AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE=false
```

LLM / VIO configuration:

```dotenv
AGENT4DESIGN_LLM_API_KEY=your-api-key
AGENT4DESIGN_LLM_MODEL=VIO:Claude 4.6 Sonnet
AGENT4DESIGN_LLM_BASE_URL=https://vio.automotive-wan.com:446
AGENT4DESIGN_LLM_TEMPERATURE=0.1
AGENT4DESIGN_LLM_MAX_TOOL_ROUNDS=30
AGENT4DESIGN_LLM_MAX_RETRIES=3
AGENT4DESIGN_LLM_HEADERS={"useLegacyCompletionsEndpoint":"false","X-Tenant-ID":"default_tenant"}
```

Legacy names are also accepted, but they must still be written in `.env`:

```dotenv
API_TOKEN=your-api-key
BASE_URL=https://vio.automotive-wan.com:446
VIO_HEADERS={"useLegacyCompletionsEndpoint":"false","X-Tenant-ID":"default_tenant"}
```

HTTP / MCP token:

```dotenv
AGENT4DESIGN_API_TOKEN=your-api-token
AGENT4DESIGN_MCP_TOKEN=your-mcp-token
```

Standalone activity XMI import:

```dotenv
AGENT4DESIGN_XMI_TOOLKIT_BAT=C:\path\to\XMI4Rhapsody.bat
AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true
AGENT4DESIGN_XMI_OUTPUT_DIR=xmi_read
AGENT4DESIGN_XMI_LOG_DIR=xmi_import_logs
AGENT4DESIGN_XMI_TIMEOUT=600
```

SSL / CA settings:

```dotenv
AGENT4DESIGN_LLM_SSL_VERIFY=true
AGENT4DESIGN_LLM_CA_BUNDLE=C:\path\to\vio_root_ca.pem
```

Temporary certificate bypass:

```dotenv
AGENT4DESIGN_LLM_SSL_VERIFY=false
```

### Rhapsody Workflow

1. Open IBM Rhapsody.
2. Load the target project.
3. Select a writable target in the Rhapsody browser.
4. If you want to create functions, select a `Class` or `Block`.
5. Initialize the Agent4Design context:

```text
initialize_rhapsody
```

After changing the GUI selection, refresh the target:

```text
select_rhapsody_target
```

Recommended sync flow:

```text
initialize_rhapsody
  -> refresh_type_registry
  -> plan_agent4design_sync
  -> review the plan
  -> execute_agent4design_sync(approved=true)
  -> verify_rhapsody_model
  -> save_rhapsody_project(approved=true)
```

### Function Target Rule

Function sync only writes to `Class` or `Block`, creating Rhapsody `Operation`
elements.

If the selected target is a `Module`, planning and execution reject the function
write to avoid COM errors such as:

```text
Unexpected error (-2147221495)
```

Fix:

```text
Select a Class or Block in Rhapsody
Call select_rhapsody_target
Run plan / execute again
```

Retrying the same request will not fix an invalid target metaclass.

### Tools

`Agent4DesignService` exposes:

```text
initialize_rhapsody
select_rhapsody_target
get_rhapsody_context
refresh_type_registry
save_type_index
load_type_index
extract_code_path_model
plan_code_path_modeling
execute_code_path_modeling
plan_agent4design_sync
execute_agent4design_sync
verify_rhapsody_model
save_rhapsody_project
```

Tool summary:

```text
initialize_rhapsody          Connect to Rhapsody and optionally select the GUI target
select_rhapsody_target      Refresh the writable target from the GUI selection
get_rhapsody_context        Return active project and target summary
refresh_type_registry       Scan Type / Class metadata
save_type_index             Save type metadata for diagnostics
load_type_index             Load type metadata
extract_code_path_model     Segment C code and extract model specs through LLM JSON
plan_code_path_modeling     Build a read-only plan from a code path
execute_code_path_modeling  Execute approved code-path modeling
plan_agent4design_sync      Build a read-only plan from structured specs
execute_agent4design_sync   Execute approved COM / XMI writes and verify
verify_rhapsody_model       Verify expected model elements read-only
save_rhapsody_project       Save the active project after approval
```

### Code Path Extraction

`extract_code_path_model` does not parse C with regex. The flow is:

```text
source path
  -> tree-sitter syntax segments
  -> original source, lines, byte offsets, and context per segment
  -> strict JSON extraction by LLM
  -> Pydantic validation
  -> merged Rhapsody model specs
```

Install parser support:

```powershell
python -m pip install -e ".[parser]"
```

When using the LLM CLI, the LLM adapter injects the same OpenAI-compatible
client as the code segment extractor.

When calling through HTTP / MCP without an internal LLM extractor, request
segments only:

```json
{
  "path": "src",
  "recursive": true,
  "include_headers": true,
  "include_activities": true,
  "require_model_extraction": false
}
```

Each returned segment includes:

```text
path
language
kind
symbol
start_line / end_line
start_byte / end_byte
source
context
```

After LLM extraction, merged output includes:

```text
macros
variables
functions
activities
warnings
errors
```

### HTTP API

Start:

```powershell
python -m agent4design.adapters.http
```

Default address:

```text
http://127.0.0.1:8765
```

Health and tool discovery:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/tools
```

Generic tool call:

```powershell
$body = @{
  name = "initialize_rhapsody"
  arguments = @{ select_current_target = $true }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/call `
  -ContentType application/json `
  -Body $body
```

Named tool call:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/tools/get_rhapsody_context `
  -ContentType application/json `
  -Body "{}"
```

Listening outside localhost requires:

```dotenv
AGENT4DESIGN_API_TOKEN=your-token
```

### MCP Server

Local stdio MCP:

```powershell
python -m agent4design.adapters.mcp --transport stdio
```

Remote streamable HTTP MCP:

```powershell
python -m agent4design.adapters.mcp `
  --transport streamable-http `
  --host 0.0.0.0 `
  --port 8766 `
  --path /mcp `
  --token your-secret-token
```

Remote MCP still operates the Rhapsody desktop session on the Windows machine
running the server. Do not expose it directly to the public Internet.

### LLM Agent

Start the interactive Agent:

```powershell
python -m agent4design.adapters.llm
```

The default mode supports read-only inspection, plan generation, and one write
execution when the latest user message explicitly grants approval. A typical
session looks like this:

```text
You> Connect to Rhapsody
Agent> ... initialize_rhapsody ...

You> Sync all non-activity elements from "D:\project\src\CD_AppMain.c"
Agent> ... read_code_path / plan_agent4design_sync ...

You> approved
Agent> ... execute_agent4design_sync ...
```

Explicit approval examples:

```text
approved
approve
proceed
execute
yes
批准执行
授权执行
允许写入
```

If the latest message contains a denial such as `cancel`, `reject`, `no`, or
`不批准执行`, the local adapter will not approve the write tool.

One-shot mode:

```powershell
python -m agent4design.adapters.llm --message "Inspect the selected Rhapsody target."
```

Use `--allow-writes` when you want an extra terminal prompt:

```powershell
python -m agent4design.adapters.llm --allow-writes
```

With this flag, an explicit approval message still approves the write directly.
If the latest message is not an approval, the terminal prints the write tool
name and JSON arguments, then asks:

```text
Approve this Rhapsody write? [y/N]
```

Enter `y` or `yes` to continue.

Recommended full workflow:

```text
1. Open Rhapsody and load the target project.
2. Select the target Class or Block in the Rhapsody Browser.
3. Start the Agent: python -m agent4design.adapters.llm
4. Ask the Agent to connect to Rhapsody.
5. If you change the selected Rhapsody target, ask the Agent to refresh it.
6. Ask for a code-path sync, for example:
   Sync all non-activity elements from "D:\project\src\CD_AppMain.c"
7. Review the plan_agent4design_sync result, especially create / update / reject items.
8. If the plan is correct, reply `approved` or `批准执行`.
9. Trust execute_agent4design_sync and verify_rhapsody_model tool results, not prose alone.
10. Save the project only after a separate save request and approval.
```

Troubleshooting:

- If `Human approval was not granted for this write operation.` repeats, make sure you are running the current code and that the latest user message is an explicit approval.
- If functions are rejected, make sure the selected Rhapsody target is a `Class` or `Block`, then refresh the target.
- If type resolution fails, call `refresh_type_registry` or add the missing type definitions to the Rhapsody project.
- Writes do not save the project automatically; saving requires `save_rhapsody_project` and approval.

Safety boundary:

- The model-visible schema does not include `approved`.
- Model-generated `approved=true` is overwritten locally.
- Writes are rejected unless the latest user message or terminal prompt approves them.
- Success must be based on tool results.

### LangGraph Workflow

LangGraph is optional and provides a resumable plan / approval / execute /
verify workflow.

Install:

```powershell
python -m pip install -e ".[graph]"
```

Main entry points:

```text
agent4design.workflows.sync_graph.build_sync_graph
agent4design.workflows.sync_graph.create_sqlite_checkpointer
```

The workflow still calls `Agent4DesignService`; it does not access COM
directly.

### Activity XMI Import

Activity import requires IBM Rhapsody XMI Toolkit. Without Toolkit
configuration, import fails with:

```text
Activity sync is not configured
```

This is a startup configuration issue, not a retryable request issue. Edit
`.env` and restart the HTTP, MCP, or LLM Agent service.

Activity import is still experimental, but generated XMI now follows the
Rhapsody-exported function binding structure. Before execution, Agent4Design
resolves the matching `Operation` GUID under the selected target, generates an
`AD_functionName` Activity, and writes `specification="OperationGUID"` into the
XMI. If the function is neither part of the current sync request nor present as
an existing Operation, the plan rejects that activity.

### Project Structure

```text
Agent4Design/
  README.md
  pyproject.toml
  agent4design/
    adapters/
      http/               Local JSON-over-HTTP API
      llm/                OpenAI-compatible tool-calling Agent
      mcp/                MCP Server adapter
    docs/                 Design notes, API docs, legacy references
    domain/
      models.py           Pydantic data contracts
      validators.py       Activity graph validators
    rhapsody/
      com_runtime.py      COM STA thread dispatcher
      context.py          Rhapsody app/project/target context
      repository.py       COM read/write repository
      type_registry.py    Type / Class metadata index
      verifier.py         Read-only verifier
    services/
      agent_service.py    Stable Agent-facing facade
      code_extractor.py   tree-sitter segmentation and LLM extraction protocol
      model_sync.py       Macro, variable, function sync service
      sync_plan.py        Read-only planning before writes
      verification.py     Verification service wrapper
      activity_sync.py    XMI generation and import service
    tools/
      tool.py             Shared helper functions
    workflows/
      sync_graph.py       LangGraph builder
      nodes.py            Workflow nodes
      state.py            Workflow state
    xmi/
      generator.py        Activity XMI generation
      importer.py         XMI Toolkit invocation
  tests/                  Offline tests
  legacy/                 Historical experiment scripts
```

### Design Boundaries

Core boundary:

```text
external adapter
  -> Agent4DesignService.call(name, arguments)
  -> Pydantic request validation
  -> service layer
  -> repository / XMI toolkit
```

Principles:

- Adapters only translate protocols.
- Services orchestrate application behavior.
- The Rhapsody layer owns COM details.
- The XMI layer owns file generation and import.
- The LLM calls tools and extracts strict JSON; it does not operate COM directly.

### Troubleshooting

#### `-2147221495`

Usually the selected target cannot accept functions, for example a `Module`.

Fix:

```text
Select a Class or Block
Call select_rhapsody_target
Run plan / execute again
```

#### `Activity sync is not configured`

XMI Toolkit is not configured.

Fix:

```dotenv
AGENT4DESIGN_XMI_TOOLKIT_BAT=C:\path\to\XMI4Rhapsody.bat
AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true
```

Then restart the service.

#### Custom type not found

Unknown custom types are rejected by default to avoid polluting the model with
typos.

Fix:

```text
Create the correct type in Rhapsody first
or set AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE=true in .env
```

#### LLM API key is not picked up

Make sure the key is written in `.env`, not in the OS environment.

Preferred:

```dotenv
AGENT4DESIGN_LLM_API_KEY=...
```

Legacy-compatible:

```dotenv
API_TOKEN=...
```

#### tree-sitter missing

Install parser support:

```powershell
python -m pip install -e ".[parser]"
```

### Testing

Run offline tests:

```powershell
python -m pytest -q
```

If the machine has multiple Python installations, use the interpreter that has
the project dependencies:

```powershell
D:\Environment\miniconda\python.exe -m pytest -q
```

Compile check:

```powershell
python -m compileall -q agent4design tests
```
