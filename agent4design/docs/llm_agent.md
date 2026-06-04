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

For normal C/H modeling, the Agent uses a direct CODE-to-tool flow. If the user
pastes CODE, the model generates `plan_agent4design_sync` JSON directly from
the message. If the user provides a local C/H path, the Agent calls
`read_code_path` to read CODE text. Long files are returned one syntax chunk at
a time, so the model can call `plan_agent4design_sync` for one chunk, then ask
`read_code_path` for the next chunk when `has_more=true`.

`extract_code_path_model` and `plan_code_path_modeling` remain available as
legacy parser/LLM extraction tools for diagnostics, but they are no longer the
recommended default path.

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

The CLI streams final assistant text as it arrives. When code extraction tools
run, it also prints status lines for tool calls and individual source segments.

For a local C/H path, the direct read step accepts bounded arguments such as:

```json
{
  "path": "D:\\project_design\\GTMC_V57_CD\\sw.cmp.CD\\Source\\CD\\Project\\Code\\CD_AppMain.c",
  "max_bytes": 120000,
  "encoding": "auto",
  "syntax_chunks": true,
  "max_chunk_chars": 30000,
  "chunk_index": 0
}
```

## Safety Boundary

The model can request write tools, but it cannot grant approval. The adapter
removes `approved` from model-visible JSON schemas and overwrites any
model-generated approval value. A write proceeds only after the local approval
handler returns `True`.
