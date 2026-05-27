# Agent4Design: XMI and COM Architecture Decision

## Background

Agent4Design currently uses two different ways to write into IBM Rhapsody:

- COM API: create or update functions, variables, macros, types, and project elements.
- XMI Toolkit: import generated XMI, currently used mainly for activity diagrams.

The project should not assume one path is always better. Rhapsody may treat imported XMI differently from elements created through COM, especially for C code generation properties, stereotypes, ownership, diagrams, and round-trip behavior.

## Main Principle

Use an intermediate model first, then choose the writer.

```text
C code / user request
  -> structured specs
  -> validation
  -> writer selection
  -> COM writer or XMI writer
  -> verification
```

The LLM should never directly write Rhapsody-specific COM calls or raw XMI. It should produce structured data such as `FunctionSpec`, `VariableSpec`, `MacroSpec`, and `ActivityGraph`.

## Recommended Default

Use a hybrid strategy:

| Model element | Preferred writer | Reason |
| --- | --- | --- |
| Function signature | COM | Easier to bind to selected package/file/class and set Rhapsody C properties. |
| Function arguments | COM | Direct object model access is less fragile than XMI ids. |
| Return type | COM | Type lookup and reuse are easier through the active project. |
| Variables | COM | Better for existing type references and initial values. |
| Macros | COM | Current implementation maps macros to variables/attributes with stereotype. |
| Activity diagram | XMI | Current working path, better for graph-shaped data and batch import. |
| Bulk model import | XMI | Easier to generate repeatable artifacts and logs. |
| Verification | COM | Query the active project after import or sync. |

This means the first stable pipeline should be:

```text
Extract C definitions
  -> sync functions/variables/macros by COM
  -> generate activity XMI
  -> import XMI with XMI Toolkit
  -> verify result by COM
```

## Why Not XMI For Everything Yet

XMI may be able to create more than activity diagrams, but this must be proven inside the target Rhapsody profile and C environment.

Risks:

- Imported elements may miss Rhapsody-specific C code generation properties.
- Type references may become duplicated instead of linked to existing project types.
- Ownership under File, Module, Package, or Class may not match the intended target.
- Stereotypes such as `Define` may import differently from COM-created stereotypes.
- Round-trip/code-generation behavior may differ from native COM-created elements.
- XMI ids and references can be fragile when importing repeatedly.

So XMI should become more general only after experiments show it preserves the model semantics Rhapsody needs.

## Experiments To Decide Scope

Create small XMI fixtures and import them one by one. After each import, verify with COM and manually inspect in Rhapsody.

### Experiment 1: Activity Diagram Only

Goal: confirm the current activity diagram import path is reliable.

Verify:

- Activity appears under the expected owner.
- Initial, final, action, decision, merge nodes are present.
- Control flows are connected.
- Guards are visible and meaningful.
- Re-import behavior is understood.

Decision:

- If stable, keep activity diagrams on XMI.

### Experiment 2: Function Signature By XMI

Goal: test whether XMI can create a C function/operation with return type and arguments.

Verify:

- Function appears under the correct File, Module, Package, or Class.
- Return type links to an existing type, not a duplicate.
- Arguments keep names, directions, pointer modifiers, arrays, and const/static metadata.
- Code generation sees the function correctly.

Decision:

- If any core C property is lost, keep function signatures on COM.

### Experiment 3: Variable By XMI

Goal: test whether XMI can create variables/attributes reliably.

Verify:

- Variable appears in the correct owner.
- Type is linked correctly.
- Initial value is preserved.
- Static, const, pointer, and array information are preserved.

Decision:

- If Rhapsody C properties are incomplete, keep variables on COM.

### Experiment 4: Macro By XMI

Goal: test whether XMI can create the same macro representation as current COM logic.

Verify:

- Element has the intended metaclass.
- `Define` stereotype is present.
- Value is preserved.
- Code generation or downstream tools interpret it correctly.

Decision:

- If stereotype/property behavior differs, keep macros on COM.

## Proposed Refactor Shape

```text
agent4design/
  domain/
    models.py
    validators.py

  rhapsody/
    com_runtime.py
    context.py
    repository.py
    verifier.py

  xmi/
    activity.py
    generator.py
    importer.py
    fixtures.py

  pipeline/
    workflow.py
    writer_policy.py

  harness/
    runner.py
    cases/
```

`writer_policy.py` decides which backend handles each spec:

```text
MacroSpec -> COM
VariableSpec -> COM
FunctionSpec -> COM
ActivityGraph -> XMI
```

Later, if experiments prove that XMI can safely create functions or variables, the policy can be changed without rewriting the rest of the system.

## First Implementation Step

Do not rewrite all scripts at once.

1. Keep existing scripts as `legacy`.
2. Extract shared data models first.
3. Extract XMI generation/import as reusable modules.
4. Add a small harness that can generate XMI from a hand-written `ActivityGraph`.
5. Add COM verification after import.
6. Only then decide whether to expand XMI to functions, variables, and macros.

## Current Decision

Adopt a hybrid architecture for now:

```text
COM for semantic C model elements.
XMI for activity diagrams.
COM for verification.
```

This is the safest path until XMI experiments prove that Rhapsody preserves all required C modeling semantics for additional element types.
