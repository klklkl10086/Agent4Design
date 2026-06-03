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

When the Agent calls `extract_code_path_model`, the service first segments C
source with tree-sitter. The LLM adapter then reuses the same
OpenAI-compatible client as an internal `CodeSegmentModelExtractor`: each
syntax segment is sent to the model with its original source, line range, byte
offsets, and local context, and the response must validate as strict JSON.

## Configuration

Edit `.env`; Agent4Design intentionally ignores OS-level environment variables
for runtime settings.

Required `.env` setting:

```text
AGENT4DESIGN_LLM_API_KEY or OPENAI_API_KEY
```

Optional `.env` settings:

```text
AGENT4DESIGN_LLM_MODEL=VIO:Claude 4.6 Sonnet
AGENT4DESIGN_LLM_BASE_URL=https://vio.automotive-wan.com:446
AGENT4DESIGN_LLM_TEMPERATURE=0.1
AGENT4DESIGN_LLM_MAX_TOOL_ROUNDS=30
AGENT4DESIGN_LLM_MAX_RETRIES=3
AGENT4DESIGN_LLM_HEADERS={"useLegacyCompletionsEndpoint":"false","X-Tenant-ID":"default_tenant"}
```

Code path extraction also needs the parser extra:

```powershell
python -m pip install -e ".[parser]"
```

The defaults mirror the legacy VIO Agent setup. Override model/base URL when
using a different OpenAI-compatible provider.

Legacy names are still accepted when they are written in `.env`:

```text
API_TOKEN
BASE_URL
VIO_HEADERS
```

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
