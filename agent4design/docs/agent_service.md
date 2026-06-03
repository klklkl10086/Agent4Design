# Agent Service Boundary

## Purpose

`Agent4DesignService` is the stable local boundary for future Agent, LangGraph,
and MCP integrations. It keeps framework-specific decorators and prompts out of
the COM and XMI implementation layers.

```text
LLM / LangGraph / MCP adapter
  -> Agent4DesignService.call(name, JSON arguments)
  -> application service
  -> repository or XMI importer
  -> Rhapsody
```

## Service Responsibilities

| Module | Responsibility |
| --- | --- |
| `services/code_extractor.py` | Extract conservative model specs from C source files. |
| `services/model_sync.py` | Synchronize macros, variables, and functions through COM. |
| `services/activity_sync.py` | Validate, generate, and import one standalone activity XMI artifact. |
| `services/sync_plan.py` | Build read-only approval plans before writes. |
| `services/verification.py` | Run read-only post-sync COM checks. |
| `services/agent_service.py` | Expose JSON-schema tools and dispatch validated Agent calls. |
| `rhapsody/repository.py` | Perform small COM read/write operations. |
| `rhapsody/type_registry.py` | Index type metadata and relocate COM objects in the active session. |

The repository is intentionally not the Agent API. It is infrastructure used by
the service layer.

## Available Agent Tools

`Agent4DesignService.list_tools()` exposes:

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

Project saving is explicit and requires approval. `execute_agent4design_sync`
can save after successful synchronization and verification only when its
validated request includes `save_project=true`.

## Adapter Rule

LangChain, LangGraph, and MCP integrations should be thin adapters:

```text
framework tool definition
  -> Agent4DesignService.call(...)
  -> serialize AgentToolResult
```

Do not duplicate COM calls, XMI templates, or type lookup logic inside prompts,
tool decorators, or workflow nodes.

## Remaining Follow-Up

Before enabling automatic activity synchronization in a production Agent:

1. Export a manually created function activity diagram with XMI Toolkit.
2. Compare the export with `xmi/generator.py`.
3. Determine how Rhapsody represents Function-to-Activity ownership.
4. Broaden `rhapsody/verifier.py` once ownership mapping is confirmed.
5. Replace the conservative regex C extractor with tree-sitter or clang when
   parser coverage becomes the limiting factor.
