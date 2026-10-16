# Documentation

Index of the project documentation.

| Document | Contents |
|---|---|
| [`DATABASE.md`](DATABASE.md) | Application metadata schema: entities, relationships, keys, constraints, indexes, ER diagram, migration history. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Service topology, agent pipeline, sensitive-data flow, security boundaries, tech stack. |
| [`SECURITY.md`](SECURITY.md) | Authentication / authorization model, backup gate, execution safety, rollback, append-only audit, secret handling. |
| [`RUNBOOK.md`](RUNBOOK.md) | Operational procedures: start/stop, switch LLM, run validators, recover from a wipe, troubleshooting. |
| [`PHASES.md`](PHASES.md) | Phase-by-phase delivery summary and test tallies. |
| [`PER_LANGUAGE_LLM.md`](PER_LANGUAGE_LLM.md) | Notes on per-language LLM prompting. |
| [`ITEMS_1_4_6_7.md`](ITEMS_1_4_6_7.md) | Item-specific notes (auth, etc.). |
| `phaseN_validation_report.md` | Live-validation evidence for each phase that has one. |
| `screenshots/` | UI screenshots captured by the automated tools. |

The top-level [`../README.md`](../README.md) has the quick-start and
setup instructions. Schema details live in [`DATABASE.md`](DATABASE.md);
the auth flow is documented in both [`../README.md`](../README.md) and
[`SECURITY.md`](SECURITY.md).
