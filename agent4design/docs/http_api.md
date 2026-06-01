# HTTP API

The dependency-free HTTP adapter exposes `Agent4DesignService.call()` to local
programs. Start it with:

```powershell
python -m agent4design.adapters.http
```

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `AGENT4DESIGN_API_HOST` | `127.0.0.1` | HTTP bind host. |
| `AGENT4DESIGN_API_PORT` | `8765` | HTTP bind port. |
| `AGENT4DESIGN_API_TOKEN` | empty | Optional bearer token. Required outside localhost. |
| `AGENT4DESIGN_REQUIRE_WRITE_APPROVAL` | `true` | Require approval for write operations. |
| `AGENT4DESIGN_CREATE_PLACEHOLDER_TYPE` | `false` | Allow unknown custom types to create placeholders. |
| `AGENT4DESIGN_ENABLE_ACTIVITY_IMPORT` | `false` | Enable experimental standalone Activity import. |
| `AGENT4DESIGN_XMI_TOOLKIT_BAT` | empty | XMI Toolkit batch file path. |

When a token is configured, send either:

```text
Authorization: Bearer <token>
```

or:

```text
X-Agent4Design-Token: <token>
```

## Routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check that the HTTP process is running. Does not connect to COM. |
| `GET` | `/tools` | List validated Agent4Design tools and JSON schemas. |
| `POST` | `/call` | Call a tool using `{ "name": "...", "arguments": {} }`. |
| `POST` | `/tools/{name}` | Call one tool with the request body as its arguments. |

## Typical Flow

1. Open Rhapsody and load a project.
2. Select the target `Project`, `Package`, `Class`, `Block`, `File`, or `Module`.
3. Call `initialize_rhapsody`.
4. Call `refresh_type_registry`.
5. Call `plan_agent4design_sync`.
6. Review the dry-run report.
7. Call `execute_agent4design_sync` with `approved=true`.
8. Call `save_rhapsody_project` with `approved=true` if needed.

## Plan Example

```powershell
$request = @{
  model = @{
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

$body = @{
  name = "plan_agent4design_sync"
  arguments = $request
} | ConvertTo-Json -Depth 12

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/call `
  -ContentType application/json `
  -Body $body
```

## Approved Write Example

```powershell
$body = @{
  name = "execute_agent4design_sync"
  arguments = @{
    request = $request
    approved = $true
    verify_after_sync = $true
  }
} | ConvertTo-Json -Depth 12

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/call `
  -ContentType application/json `
  -Body $body
```
