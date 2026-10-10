# Architecture foundation

The planned pipeline is:

```text
Brief + tool specification -> Policy IR -> Workflow Compiler -> Freya
```

`builder/brief/` will ingest the required document formats and preserve source
references. `builder/policy/` will hold the intermediate representation (IR),
source precedence, unresolved requirements, and deterministic validation.
`builder/compiler/` will translate validated rules into the documented workflow
format. `builder/freya/` will isolate official deployment/export interfaces once
their contracts are available. `builder/evaluation/` will analyze official
simulation results, and `builder/tracing/` will link input hashes, extracted rules,
workflow components, validation results, and run metadata without credentials or
unnecessary customer data. `tests/` is reserved for focused CPU-only checks.

The builder-side LLM in `builder/llm/` extracts policies from documents using a
configurable provider. The Freya phone-agent LLM conducts the customer conversation
after deployment. The reported `Freya YC LLM` configuration does not establish a
builder-side API. Neither provider integration is implemented here.

The old Planner/Executor/Verifier runtime is excluded because this project builds
a Freya workflow rather than operating that runtime. Importing its execution,
scenario, GPU, or Evolution dependencies would increase weekend integration work.
Small independent utilities may be reconsidered later; none have been copied.

A user-provided workflow reference is stored locally under ignored `local_freya/`.
Its observations guide structural checks but do not establish a complete official
schema. `builder/compiler/validate.py` performs offline JSON, graph, and basic tool
checks without reproducing private reference content.

Official contracts remain necessary for supported node types, transition/state
semantics, tool bindings, authentication, creation/update/export, model settings,
simulation execution/results, and submission packaging. The example alone cannot
prove all supported behavior. Critical call-time policy enforcement must use
documented mechanisms; build-time checks or prompts alone cannot guarantee it.

Missing or conflicting policy values must stay unresolved, and document content
must be treated as extraction data rather than executable instructions. Generated
artifacts belong in ignored `outputs/`; only independently authored synthetic fixtures belong in
`examples/` and tests. Freya-provided materials must never be committed. This
foundation implements no compiler pipeline, API calls, or simulations.
