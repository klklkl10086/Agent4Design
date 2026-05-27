# Agent4Design Interview Roadmap

## Goal

Build a demo-ready version of Agent4Design that shows clear engineering value:

```text
C code -> structured model specs -> Rhapsody function/variable/macro sync -> activity XMI -> XMI Toolkit import -> verification report
```

The goal is not to finish every feature. The goal is to prove architecture, reliability thinking, and a working vertical slice.

## 7-Day Fast Track

### Day 1: Define the Architecture Story

Deliverables:

- Keep `docs/xmi_vs_com_architecture.md`.
- Add a short project README explaining the pipeline.
- Prepare one diagram:

```text
C source
  -> LLM/parser extraction
  -> Pydantic specs
  -> COM writer for C model elements
  -> XMI writer for activity diagrams
  -> Rhapsody model
```

Interview talking point:

> I separated code understanding, intermediate data models, Rhapsody writing, and workflow orchestration so the system is not tied to one LLM prompt or one import mechanism.

### Day 2: Create Shared Data Models

Deliverables:

- Create `agent4design/domain/models.py`.
- Move or recreate these Pydantic models:
  - `CTypeInfo`
  - `FunctionArgument`
  - `MacroSpec`
  - `VariableSpec`
  - `FunctionSpec`
  - `ActivityNode`
  - `ActivityEdge`
  - `ActivityGraph`

Interview talking point:

> I use structured schemas as contracts between the LLM, validation, COM writer, and XMI generator.

### Day 3: Extract XMI Generation

Deliverables:

- Create `agent4design/xmi/generator.py`.
- Move the working XMI template from `generate_xmi.py`.
- Add a function:

```python
generate_activity_xmi(function_name: str, graph: ActivityGraph) -> str
```

- Add a small sample graph and generate one XMI file offline.

Interview talking point:

> Activity diagrams are generated as artifacts first, which makes them testable before touching Rhapsody.

### Day 4: Extract XMI Import

Deliverables:

- Create `agent4design/xmi/importer.py`.
- Wrap `XMI4Rhapsody.bat` execution.
- Return a structured import result:

```text
success
return_code
stdout
stderr
log_path
```

Interview talking point:

> External tool execution is isolated and observable. I do not bury import failures inside the Agent loop.

### Day 5: Extract COM Runtime

Deliverables:

- Create `agent4design/rhapsody/com_runtime.py`.
- Move the STA single-thread dispatcher.
- Create `agent4design/rhapsody/context.py`.
- Keep COM operations serial.

Interview talking point:

> Rhapsody COM is STA-sensitive, so I use a dedicated dispatcher thread to avoid cross-thread COM instability.

### Day 6: Build One Vertical Demo

Deliverables:

- Pick one simple C function.
- Produce:
  - function signature spec
  - activity graph spec
  - generated XMI
  - import log
  - optional Rhapsody screenshot

Demo flow:

```text
Run one command -> generate XMI -> import into Rhapsody -> show activity diagram
```

Interview talking point:

> This is an end-to-end modeling assistant for legacy C systems targeting Rhapsody.

### Day 7: Prepare Interview Narrative

Prepare these answers:

1. Why COM and XMI both exist.
2. Why not let the LLM write directly to Rhapsody.
3. How schema validation reduces hallucination risk.
4. Why activity diagrams are imported through XMI.
5. How LangGraph would improve reliability.
6. How MCP would expose this as a tool server.
7. What you would improve next.

## Minimum Demo Scope

Do not try to support every C pattern.

Support only:

- One `.c` file.
- Complete function definitions.
- Simple `if/else`.
- Simple return statements.
- Basic types.
- Activity graph nodes:
  - Initial
  - Action
  - Decision
  - Merge
  - Final

This is enough for a strong interview demo.

## What To Say If Asked About Limitations

Use this framing:

> The current version is a vertical slice. It proves the architecture and Rhapsody integration path. The next step is expanding parser coverage with tree-sitter or clang, adding LangGraph for retryable workflows, and exposing the stable operations through MCP.

## Priority Order

```text
1. Working demo
2. Clean architecture explanation
3. Structured data models
4. XMI generation/import reliability
5. COM verification
6. LangGraph
7. MCP
```

Do not put MCP first. It is most valuable after the local pipeline is stable.
