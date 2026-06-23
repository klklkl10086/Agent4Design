# Agent4Design

Agent4Design 是一个运行在 Windows 本机的 IBM Rhapsody 建模助手。它可以根据
C/H 代码、结构化 JSON 请求、MCP 客户端或 HTTP API 创建和更新 Rhapsody 模型
元素。普通语义模型元素通过 Rhapsody COM API 写入，活动图通过 IBM Rhapsody
XMI Toolkit 生成并导入为独立 XMI 包。

英文文档：[README.md](README.en-EN.md)

## 安装和使用

### 运行环境要求

- Windows，并且已经安装 IBM Rhapsody。
- IBM Rhapsody 必须在运行 Agent4Design 的同一台机器、同一个桌面会话中打开。
- 必须确定IBM Rhapsody的COM (Component Object Model) 程序标识符是 `Rhapsody2.Application`，如果不是，需要更改[context.py](agent4design/rhapsody/context.py)中的`_connect_in_thread`
- 如果需要插入活动图需要运行IBM Rhapsody安装路径下的C:\LegacyApp\Rhapsody_902_64bit\Sodius\XMI_Toolkit\bin的plugin_rhp_ini_update.bat脚本
- Python 3.10 或更新版本。
- 使用 Rhapsody COM 写入时，需要安装 `rhapsody` extra，其中包含 `pywin32`。
- 读取和分段 C/H 代码时，需要安装 `parser` extra。
- 使用交互式 LLM Agent 时，需要安装 `llm` extra。

### 从源码安装

普通本机使用：

```powershell
python -m pip install -e ".[rhapsody,parser,llm]"
```


### 配置

在项目根目录，或启动命令所在目录创建 `.env` 文件。Agent4Design 会按下面的
顺序读取配置：

```text
当前工作目录 .env
项目根目录 .env
agent4design/.env 
```

最小 LLM 配置：

```dotenv
AGENT4DESIGN_LLM_API_KEY=你的API密钥
AGENT4DESIGN_LLM_MODEL=你的OpenAI兼容模型名
AGENT4DESIGN_LLM_BASE_URL=https://你的OpenAI兼容接口
```
活动图 XMI 导入配置：

```dotenv
AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true
AGENT4DESIGN_XMI_TOOLKIT_BAT=C:\path\to\XMI4Rhapsody.bat #本机XMI4Rhapsody.bat脚本的所在地址，需要自己确认
AGENT4DESIGN_XMI_OUTPUT_DIR=xmi_read                     #生成的XMI文件存放位置
AGENT4DESIGN_XMI_LOG_DIR=xmi_import_logs                 #XMI操作日志存放位置
AGENT4DESIGN_XMI_TIMEOUT=600
```

常用运行配置：

```dotenv
AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE=false
AGENT4DESIGN_REQUIRE_WRITE_APPROVAL=true

AGENT4DESIGN_LLM_TEMPERATURE=0.1
AGENT4DESIGN_LLM_MAX_TOOL_ROUNDS=30
AGENT4DESIGN_LLM_MAX_RETRIES=3
AGENT4DESIGN_LLM_SSL_VERIFY=true
AGENT4DESIGN_LLM_CA_BUNDLE=C:\path\to\ca.pem
```


### 交互式 Agent

先打开 IBM Rhapsody，加载目标项目，并在 Rhapsody 浏览器中选中目标模型元素。

允许写入模式：

```powershell
agent4design-agent --allow-writes
```


CLI 启动时会自动连接当前 Rhapsody 项目，读取当前 GUI 选中目标，并刷新已知类型
索引。

推荐交互流程：

```text
打开 Rhapsody
加载目标项目
选中目标 Class
启动 agent4design-agent --allow-writes
让 Agent4Design 读取 C/H 文件或目录
检查同步计划
执行同步
需要时保存项目
```

### 建模行为

Agent4Design 当前支持以下模型元素：

- 类型：Rhapsody `Type` 元素。结构体和联合体把成员写入 Attributes；枚举把成员
  和值写入 Literals；typedef 写入 Details 中的 Basic Type 和 Multiplicity。
  类型定义会写到选中的 Class/Block 下。如果当前目标是 Package/File/Module，
  Agent4Design 只会在其中恰好存在唯一一个嵌套 Class/Block 时自动下钻。
- 函数：作为 Rhapsody Operation 写入选中的 Class 或 Block。
- 变量和宏：根据 repository 映射写入当前选中目标。
- 活动图：作为独立 XMI 包导入，命名为 `activity_<function_name>`。

`model.types` 示例：

```json
{
  "model": {
    "types": [
      {
        "name": "T_Point",
        "kind": "struct",
        "attributes": [
          {
            "name": "x",
            "type_info": { "base_type": "int" }
          }
        ]
      },
      {
        "name": "T_Mode",
        "kind": "enum",
        "literals": [
          { "name": "MODE_OFF", "value": 0 },
          { "name": "MODE_ON", "value": 1 }
        ]
      },
      {
        "name": "T_Count",
        "kind": "typedef",
        "basic_type": { "base_type": "unsigned int" },
        "multiplicity": ""
      }
    ]
  }
}
```

活动图中 Action 的代码会写入 UML action body，同时写入 Rhapsody
OpaqueAction 的 model-element description 扩展。



### 安全建议

- 同一个 Rhapsody 项目建议一次只执行一个会修改模型的任务。
- 不要把真实 `.env` 发给别人；只共享 `.env.example`。

## 项目结构

顶层文件：

```text
README.md              英文文档。
README.zh-CN.md        中文文档。
pyproject.toml         包元数据、依赖和命令行入口。
legacy/                旧脚本，保留作参考。
agent4design/          主 Python 包。
```

包结构：

```text
agent4design/
  adapters/
    llm/               OpenAI 兼容的交互式 Agent CLI。
    mcp/               MCP 服务适配器。
    http/              无额外 Web 框架依赖的 JSON HTTP API。
  domain/
    models.py          Pydantic 请求和响应数据模型。
    validators.py      活动图校验。
  rhapsody/
    com_runtime.py     COM 线程和运行时辅助。
    context.py         当前 Rhapsody 应用、项目和选中目标管理。
    repository.py      Types、变量、宏、函数的 COM 写入逻辑。
    type_registry.py   项目 Type/Class 索引和查找。
    verifier.py        同步后的只读验证。
  services/
    agent_service.py   与框架无关的工具门面。
    model_sync.py      批量同步用例。
    sync_plan.py       写入前的只读规划。
    code_extractor.py  C/H 读取和语法分段。
    activity_sync.py   活动图 XMI 生成和导入编排。
    verification.py    验证服务包装。
  xmi/
    generator.py       独立活动图 XMI 生成。
    importer.py        XMI Toolkit 导入封装。
  workflows/
    sync_graph.py      可选 LangGraph 工作流。
  docs/
    IBM_COM.py         生成的 Rhapsody COM API 参考。
    *.md               适配器和架构说明。
```

### 主要命令入口

安装后提供以下命令：

```text
agent4design-agent     交互式 LLM Agent。
agent4design-mcp       MCP 服务。
agent4design-api       HTTP API 服务。
```

等价模块入口：

```powershell
python -m agent4design.adapters.llm
python -m agent4design.adapters.mcp
python -m agent4design.adapters.http
```

### 当前限制和后续方向

- 普通 COM 写入仍依赖 Rhapsody 当前选中目标。
- Type 定义需要 Class 目标，或者需要当前容器中恰好只有一个嵌套
  Class。
- 活动图当前作为独立活动图包导入。
- 后续希望支持目录级自动建模：传入包含大量 `.c` 和 `.h` 文件的目录后，
  自动识别每个文件对应的 Rhapsody 包裹和文件元素位置。

期望路径约定：

```text
Root
  -> Packages
    -> Design
      -> Packages
        -> Project
          -> Packages
            -> <以 C 文件命名的包裹>
              -> Files
                -> <对应文件元素>
```

示例：

```text
CD_AppMain.c
  -> package: CD_App_Main
    -> main target: CD_App_Main
       -> Files
          -> CD_AppMain
```
