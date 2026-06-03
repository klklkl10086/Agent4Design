# Agent4Design

Agent4Design 是一个面向 IBM Rhapsody 的本地建模助手。它把 C 代码和结构化模型数据转换为可验证的 Rhapsody 写入操作，并通过 COM API、XMI Toolkit、HTTP、MCP 和 LLM 适配器提供统一入口。

当前设计原则：

```text
C 代码 / 用户请求
  -> Pydantic 结构化模型
  -> 只读计划
  -> 人工批准
  -> Rhapsody COM 写入 / XMI 导入
  -> COM 验证报告
```

LLM、MCP、HTTP 和 LangGraph 不直接调用 COM。它们只调用 `Agent4DesignService`。

## 安装

在能访问当前 Rhapsody 桌面会话的 Windows Python 环境中安装：

```powershell
python -m pip install -e ".[rhapsody]"
```

安装全部可选适配器：

```powershell
python -m pip install -e ".[rhapsody,llm,mcp,graph,dev]"
```

## 基本配置

可以在项目根目录 `.env` 中配置：

```powershell
AGENT4DESIGN_REQUIRE_WRITE_APPROVAL=true
AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE=false
```

默认行为：

- 写入 Rhapsody 前需要显式批准。
- 未知自定义类型默认拒绝创建占位 `Type`。
- 项目保存必须显式调用保存工具。
- COM 操作在专用 STA 线程中串行执行。

## Rhapsody 使用流程

1. 打开 Rhapsody 并加载目标项目。
2. 在 Rhapsody 浏览器中选择一个可写目标。
3. 如果要创建函数，建议选择 `Class` 或 `Block`，不要选择 `Module`。
4. 调用初始化工具：

```text
initialize_rhapsody
```

或刷新当前选择：

```text
select_rhapsody_target
```

## 函数创建目标限制

函数同步现在只允许写入 `Class` 或 `Block`，并创建为 `Operation`。

如果当前目标是 `Module`，计划阶段和执行阶段都会拒绝写入，并提示重新选择目标。这是为了避免 Rhapsody COM 抛出类似：

```text
发生意外 (-2147221495)
```

正确处理方式：

```text
在 Rhapsody 中选择目标 Class
  -> 调用 select_rhapsody_target
  -> 再执行 plan / execute
```

不要依赖重试。目标元类不正确时，重试本身不会解决问题。

## 活动图 XMI 导入配置

活动图导入依赖 IBM Rhapsody XMI Toolkit。服务启动时必须配置 Toolkit 路径。

在 `.env` 中设置：

```powershell
AGENT4DESIGN_XMI_TOOLKIT_BAT=C:\path\to\XMI4Rhapsody.bat
```

设置该路径后，服务会默认启用活动图导入。也可以显式启用：

```powershell
AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true
AGENT4DESIGN_XMI_OUTPUT_DIR=xmi_read
AGENT4DESIGN_XMI_LOG_DIR=xmi_import_logs
AGENT4DESIGN_XMI_TIMEOUT=600
```

如果未配置 Toolkit，活动图导入会报错：

```text
Activity sync is not configured
```

这是服务启动配置问题，不能通过重试同一个请求解决。需要配置环境变量并重启 HTTP、MCP 或 LLM Agent 服务。

注意：当前活动图导入仍是实验能力。生成的 XMI 会导入为独立 Activity Package，Function 与 Activity 的归属关系需要通过手工 XMI 导出实验继续确认。

## HTTP API

启动本地 HTTP 服务：

```powershell
python -m agent4design.adapters.http
```

默认地址：

```text
http://127.0.0.1:8765
```

常用接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/tools
```

调用工具：

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

监听非 localhost 地址时必须设置：

```powershell
AGENT4DESIGN_API_TOKEN=your-token
```

## MCP 服务

启动本地 MCP Server：

```powershell
python -m agent4design.adapters.mcp
```

MCP 暴露的是 `Agent4DesignService` 的薄适配，不包含 COM 写入逻辑。写入类工具仍需要显式批准。

## LLM Agent

配置 OpenAI 或 OpenAI-compatible 接口：

```powershell
AGENT4DESIGN_LLM_MODEL=your-model
AGENT4DESIGN_LLM_API_KEY=your-api-key
AGENT4DESIGN_LLM_BASE_URL=https://provider.example/v1
```

启动只读交互：

```powershell
python -m agent4design.adapters.llm
```

允许模型请求写入时，由终端人工确认：

```powershell
python -m agent4design.adapters.llm --allow-writes
```

模型不能自己批准写入。即使模型参数中传入 `approved=true`，适配器也会移除并要求人工确认。

## 常用工具

主要工具包括：

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

推荐流程：

```text
initialize_rhapsody
  -> refresh_type_registry
  -> plan_agent4design_sync
  -> 人工检查计划
  -> execute_agent4design_sync(approved=true)
  -> verify_rhapsody_model
  -> save_rhapsody_project(approved=true)
```

## 故障排查

### 函数创建报 `-2147221495`

原因通常是当前目标元类不适合写入函数，例如选中了 `Module`。

处理：

```text
选择 Class 或 Block
调用 select_rhapsody_target
重新执行计划和写入
```

### 活动图导入报 `Activity sync is not configured`

原因是服务启动时未配置 XMI Toolkit。

处理：

```powershell
AGENT4DESIGN_XMI_TOOLKIT_BAT=C:\path\to\XMI4Rhapsody.bat
AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true
```

然后重启服务。

### 自定义类型找不到

默认不会自动创建占位类型，避免拼写错误污染模型。

处理：

```text
先在 Rhapsody 项目中创建正确类型
或显式设置 AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE=true
```

## 项目结构

```text
agent4design/
  adapters/      HTTP、MCP、LLM 适配层
  domain/        Pydantic 数据模型和校验
  rhapsody/      COM 运行时、上下文、仓库、类型索引、验证器
  services/      计划、同步、验证、代码提取和 Agent 门面
  tools/         小型通用工具
  xmi/           活动图 XMI 生成和导入
```

旧脚本已移动到 `legacy/`，作为历史实验代码保留。
