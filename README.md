# Freya Agent Builder

For the Freya × YC Build the Builder Hackathon, this project will read bank
collections briefs and their tool specifications, extract and validate policies,
and generate a Freya voice-agent workflow. The resulting phone agent runs on Freya.

This repository contains the package foundation and an offline structural workflow
validator. Python 3.11 is the initial development baseline. Current code uses only
the standard library; no dependency installation is required.

Planned single-command entry point (not implemented yet):

```text
python -m builder --brief <brief-path> --tool-spec <spec-path> --output-dir outputs/<run-id>
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for module responsibilities and integration
prerequisites. Builder configuration names in [.env.example](.env.example) are
provisional; automatic environment-file loading is not implemented.

Run the offline validator and synthetic unit tests:

```text
python -B -m builder.compiler.validate local_freya/workflows/freya_bank_collections_base.json
python -B -m builder.compiler.validate <workflow-path> --json
python -B -m unittest discover -s tests -v
```

See [docs/WORKFLOW_VALIDATION.md](docs/WORKFLOW_VALIDATION.md) for checks and
limitations. Structural validity does not establish official Freya acceptance or
collections-policy compliance.

Keep all Freya-provided materials under ignored `local_freya/`: workflow exports,
briefs, specifications, starter-kit source, private documentation, customer records,
and simulation payloads. Never commit these materials, even to a private remote.
`examples/` is reserved for independently authored synthetic fixtures only.

Credentials, cookies, sessions, customer transcripts/recordings, generated workflows,
and traces or raw model messages containing private content must stay local-only.
`outputs/` is ignored except for its directory placeholder. Ignore rules cannot
classify arbitrary content; review an explicit file allowlist before staging.
Official API documentation and starter-kit contracts remain necessary.
