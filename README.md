# Agent4Design

Agent4Design is a Windows-local assistant for creating and updating IBM
Rhapsody model elements from C/H code, structured JSON requests, MCP clients,
or a small HTTP API. Normal semantic model elements are written through the
Rhapsody COM API. Activity diagrams are generated as standalone XMI packages
and imported through the IBM Rhapsody XMI Toolkit.

Chinese documentation: [README.zh-CN.md](README.zh-CN.md)

## Installation And Usage

### Runtime Requirements

- Windows, with IBM Rhapsody installed.
- IBM Rhapsody must be open on the same desktop session where Agent4Design
  runs.
- Python 3.10 or newer.
- For COM access, install the `rhapsody` extra, which includes `pywin32`.
- For C/H file parsing, install the `parser` extra.
- For the interactive LLM agent, install the `llm` extra.
- For MCP support, install the `mcp` extra.

### Install From Source

For normal local usage:

```powershell
python -m pip install -e ".[rhapsody,parser,llm]"
```

For all adapters:

```powershell
python -m pip install -e ".[rhapsody,parser,llm,mcp]"
```

For development:

```powershell
python -m pip install -e ".[rhapsody,parser,llm,mcp,graph,dev]"
```

### Install From A Wheel

Build a wheel on the release machine:

```powershell
python -m pip install build
python -m build
```

Install the generated wheel on the target machine:

```powershell
python -m pip install .\dist\agent4design-0.1.0-py3-none-any.whl
python -m pip install "pywin32>=306" "tree-sitter>=0.24,<0.26" "tree-sitter-c>=0.23,<0.25" "openai>=1,<3" "mcp[cli]>=1,<2"
```

For internal distribution, a practical release folder is:

```text
release/
  dist/agent4design-0.1.0-py3-none-any.whl
  install.ps1
  .env.example
  README.md
  README.zh-CN.md
```

### Configuration

Create a `.env` file in the project root or in the directory where commands are
started. Agent4Design loads configuration from these locations, in order:

```text
current working directory .env
project root .env
agent4design/.env
```

Minimal LLM configuration:

```dotenv
AGENT4DESIGN_LLM_API_KEY=your-api-key
AGENT4DESIGN_LLM_MODEL=your-openai-compatible-model
AGENT4DESIGN_LLM_BASE_URL=https://your-openai-compatible-endpoint
```

Optional activity XMI import configuration:

```dotenv
AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT=true
AGENT4DESIGN_XMI_TOOLKIT_BAT=C:\path\to\XMI4Rhapsody.bat
AGENT4DESIGN_XMI_OUTPUT_DIR=xmi_read
AGENT4DESIGN_XMI_LOG_DIR=xmi_import_logs
AGENT4DESIGN_XMI_TIMEOUT=600
```

Useful runtime settings:

```dotenv
AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE=false
AGENT4DESIGN_REQUIRE_WRITE_APPROVAL=true

AGENT4DESIGN_LLM_TEMPERATURE=0.1
AGENT4DESIGN_LLM_MAX_TOOL_ROUNDS=30
AGENT4DESIGN_LLM_MAX_RETRIES=3
AGENT4DESIGN_LLM_SSL_VERIFY=true
AGENT4DESIGN_LLM_CA_BUNDLE=C:\path\to\ca.pem
```

Legacy names such as `API_TOKEN`, `BASE_URL`, and `VIO_HEADERS` are also
accepted when they are written in `.env`.

### Interactive Agent

Open IBM Rhapsody, load the target project, and select the target element in
the Rhapsody browser.

Read-only mode:

```powershell
agent4design-agent
```

Write-enabled mode:

```powershell
agent4design-agent --allow-writes
```

The module form also works:

```powershell
python -m agent4design.adapters.llm --allow-writes
```

On startup, the CLI connects to the active Rhapsody project, captures the
current GUI selection, and refreshes the known type registry. Write tools are
allowed only when the CLI is started with `--allow-writes`.

Recommended interactive flow:

```text
open Rhapsody
load the target project
select the target Class, Block, File, Package, or Module
start agent4design-agent --allow-writes
ask Agent4Design to read a C/H file or directory
review the plan
execute the synchronization
save the project when needed
```

### Modeling Behavior

Agent4Design currently supports these model elements:

- Types: Rhapsody `Type` elements. Structs and unions write members to
  Attributes. Enums write Literals and values. Typedefs write Details Basic Type
  and Multiplicity. Type definitions are written under a selected Class/Block.
  If the target is a Package/File/Module, Agent4Design only auto-descends when
  it contains exactly one nested Class/Block.
- Functions: Rhapsody Operations under the selected Class or Block.
- Variables and macros: written under the selected target using the repository
  mapping.
- Activities: standalone XMI packages named `AD_<function_name>`.

`model.types` example:

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

Activity Action code is written into the UML action body and into the Rhapsody
model-element description extension for the OpaqueAction.

## MCP And HTTP

MCP and HTTP are integration adapters over the same Agent4Design service. Both
must run on the Windows machine where IBM Rhapsody is open, because Rhapsody COM
operations must execute in that local desktop session.

### MCP: Local Stdio

Use stdio when the MCP client and Rhapsody are on the same machine:

```powershell
agent4design-mcp --transport stdio
```

Typical MCP client configuration:

```json
{
  "mcpServers": {
    "agent4design": {
      "command": "agent4design-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

Equivalent module command:

```powershell
python -m agent4design.adapters.mcp --transport stdio
```

### MCP: Streamable HTTP

Use streamable HTTP when another MCP-capable client connects to the Rhapsody
machine:

```powershell
agent4design-mcp `
  --transport streamable-http `
  --host 0.0.0.0 `
  --port 8766 `
  --path /mcp `
  --token your-secret-token
```

Equivalent `.env` settings:

```dotenv
AGENT4DESIGN_MCP_TRANSPORT=streamable-http
AGENT4DESIGN_MCP_HOST=0.0.0.0
AGENT4DESIGN_MCP_PORT=8766
AGENT4DESIGN_MCP_PATH=/mcp
AGENT4DESIGN_MCP_TOKEN=your-secret-token
```

Remote clients connect to:

```text
http://<rhapsody-machine-ip>:8766/mcp
```

When a token is configured, include `auth_token` in tool arguments:

```json
{
  "auth_token": "your-secret-token"
}
```

Write tools also require `approved=true`:

```json
{
  "approved": true,
  "auth_token": "your-secret-token"
}
```

### MCP Tool Flow

Recommended MCP workflow:

1. Open Rhapsody and load a project.
2. Select the target model element in Rhapsody.
3. Call `initialize_rhapsody`.
4. Call `refresh_type_registry`.
5. Call `read_code_path` if the client needs source text chunks.
6. Call `plan_agent4design_sync`.
7. Review the plan.
8. Call `execute_agent4design_sync` with `approved=true`.
9. Call `save_rhapsody_project` with `approved=true` when needed.

Useful MCP tools:

```text
initialize_rhapsody
select_rhapsody_target
get_rhapsody_context
refresh_type_registry
read_code_path
plan_agent4design_sync
execute_agent4design_sync
verify_rhapsody_model
save_rhapsody_project
```

### HTTP API

Start the HTTP API:

```powershell
agent4design-api --host 127.0.0.1 --port 8765
```

Equivalent module command:

```powershell
python -m agent4design.adapters.http --host 127.0.0.1 --port 8765
```

If the API listens outside localhost, set a token:

```dotenv
AGENT4DESIGN_API_TOKEN=your-secret-token
```

Send the token as either:

```text
Authorization: Bearer your-secret-token
```

or:

```text
X-Agent4Design-Token: your-secret-token
```

HTTP routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check that the HTTP process is running. |
| `GET` | `/tools` | List tools and JSON schemas. |
| `POST` | `/call` | Call a tool with `{ "name": "...", "arguments": {} }`. |
| `POST` | `/tools/{name}` | Call one tool with the request body as arguments. |

Initialize Rhapsody:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/tools/initialize_rhapsody `
  -ContentType application/json `
  -Body (@{ select_current_target = $true } | ConvertTo-Json)
```

Plan a sync:

```powershell
$request = @{
  model = @{
    types = @()
    macros = @()
    variables = @()
    functions = @(
      @{
        name = "add"
        arguments = @(
          @{ name = "a"; type_info = @{ base_type = "int" } },
          @{ name = "b"; type_info = @{ base_type = "int" } }
        )
        return_type_info = @{ base_type = "int" }
      }
    )
  }
}

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/tools/plan_agent4design_sync `
  -ContentType application/json `
  -Body ($request | ConvertTo-Json -Depth 20)
```

Execute an approved sync:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/tools/execute_agent4design_sync `
  -ContentType application/json `
  -Body (@{
    request = $request
    approved = $true
    verify_after_sync = $true
  } | ConvertTo-Json -Depth 20)
```

### Security Notes

- Do not expose MCP or HTTP directly to the public Internet.
- Prefer VPN or a trusted LAN for remote access.
- Use `AGENT4DESIGN_MCP_TOKEN` or `AGENT4DESIGN_API_TOKEN` for non-localhost
  services.
- Run one model-changing task at a time against a Rhapsody project.
- Keep `.env` files private. Share `.env.example`, not real credentials.

## Project Structure

Top-level files:

```text
README.md              English documentation.
README.zh-CN.md        Chinese documentation.
pyproject.toml         Package metadata, dependencies, and console commands.
legacy/                Older scripts kept as references.
agent4design/          Main Python package.
```

Package layout:

```text
agent4design/
  adapters/
    llm/               Interactive OpenAI-compatible agent CLI.
    mcp/               MCP server adapter.
    http/              Dependency-free JSON HTTP API.
  domain/
    models.py          Pydantic request/response data contracts.
    validators.py      Activity graph validation.
  rhapsody/
    com_runtime.py     COM thread/runtime helper.
    context.py         Active Rhapsody app/project/selection management.
    repository.py      COM write logic for Types, variables, macros, functions.
    type_registry.py   Project Type/Class index and lookup.
    verifier.py        Read-only post-sync verification.
  services/
    agent_service.py   Framework-neutral tool facade.
    model_sync.py      Batch sync use case.
    sync_plan.py       Read-only planning before writes.
    code_extractor.py  C/H reading and segmentation.
    activity_sync.py   Activity XMI generation/import orchestration.
    verification.py    Verification service wrapper.
  xmi/
    generator.py       Standalone activity XMI generation.
    importer.py        XMI Toolkit import wrapper.
  workflows/
    sync_graph.py      Optional LangGraph workflow.
  docs/
    IBM_COM.py         Generated Rhapsody COM API reference.
    *.md               Adapter and architecture notes.
```

### Main Entry Points

The package exposes these commands:

```text
agent4design-agent     Interactive LLM agent.
agent4design-mcp       MCP server.
agent4design-api       HTTP API server.
```

The equivalent module entry points are:

```powershell
python -m agent4design.adapters.llm
python -m agent4design.adapters.mcp
python -m agent4design.adapters.http
```

### Current Limitations And Roadmap

- Normal COM writes still rely on the active Rhapsody selection.
- Type definitions require a Class/Block target, or a container with exactly one
  nested Class/Block.
- Activity diagrams are imported as standalone activity packages.
- A future directory-based workflow should map many `.c` and `.h` files into
  Rhapsody packages automatically. The desired convention is:

```text
Root
  -> Packages
  -> Design
  -> Packages
  -> Project
  -> Packages
  -> <package named after the C file>
  -> Files
  -> <file element named after the C file>
```

Example:

```text
CD_AppMain.c
  -> package: CD_App_Main
  -> main target: CD_App_Main -> Files -> CD_AppMain
```
