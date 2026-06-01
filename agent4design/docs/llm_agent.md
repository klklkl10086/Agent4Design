# Model API Agent

## Purpose

`agent4design.adapters.llm` connects an OpenAI-compatible chat API to the
framework-neutral `Agent4DesignService`.

```text
OpenAI-compatible chat API
  -> tool-calling Agent loop
  -> Agent4DesignService.call(...)
  -> planning, COM sync, verification, or XMI generation
```

The adapter uses Chat Completions tool calls for broad provider compatibility.
The rest of the project does not depend on this protocol and can gain another
model adapter later.

## Configuration

Required:

```text
AGENT4DESIGN_LLM_MODEL
AGENT4DESIGN_LLM_API_KEY or OPENAI_API_KEY
```

Optional:

```text
AGENT4DESIGN_LLM_BASE_URL
AGENT4DESIGN_LLM_MAX_TOOL_ROUNDS=8
```

`AGENT4DESIGN_LLM_BASE_URL` can point to an OpenAI-compatible endpoint. Omit it
for the default OpenAI API URL.

## Run

Interactive read-only mode:

```powershell
python -m agent4design.adapters.llm
```

Interactive mode with human approval prompts for writes:

```powershell
python -m agent4design.adapters.llm --allow-writes
```

One-shot mode:

```powershell
python -m agent4design.adapters.llm --message "Inspect the selected Rhapsody target."
```

## Safety Boundary

The model can request write tools, but it cannot grant approval. The adapter
removes `approved` from model-visible JSON schemas and overwrites any
model-generated approval value. A write proceeds only after the local approval
handler returns `True`.
