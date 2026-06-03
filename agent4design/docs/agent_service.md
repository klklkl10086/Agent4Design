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
| `services/code_extractor.py` | Segment C code with tree-sitter and merge LLM-extracted model specs. |
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

## Code Path Extraction

Code extraction is intentionally split into two steps:

```text
source path
  -> parser-backed syntax segments
  -> CodeSegmentModelExtractor
  -> validated Pydantic model specs
```

The default segmenter uses tree-sitter's C grammar. It preserves each segment's
original source, line range, byte offsets, symbol hint, and earlier local
context snippets. No COM writes happen in this layer.

`CodeSegmentModelExtractor` is a protocol. The OpenAI-compatible LLM adapter
injects an implementation that sends one syntax segment at a time to the model
and requires a strict `CodeSegmentExtraction` JSON response. HTTP and MCP
servers can still expose parser segments without an internal LLM by calling
`extract_code_path_model` with `require_model_extraction=false`.

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
5. Add a clang-backed extractor option when include resolution, macro expansion,
   or type-accurate analysis becomes the limiting factor.
