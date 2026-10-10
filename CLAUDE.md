# Agent Platform

A platform for building, shipping and running AI agents: nine subsystems plus Features on a shared data layer. Models and agents make decisions; the platform makes guarantees.

The MVP is being built slice by slice. `docs/design/` is the source of truth for what each subsystem does; read the relevant file before changing a subsystem. Start with [docs/design/README.md](docs/design/README.md) and [00-overview.md](docs/design/00-overview.md).

## Subsystem map

| # | Subsystem | Owns | Plane | Design |
| --- | --- | --- | --- | --- |
| 1 | Builder Canvas | Agent, workflow, skill and tool builders; entitlements; approvals | Platform (backend), UI | [01-builder-canvas.md](docs/design/01-builder-canvas.md) |
| 2 | Deployments | Versioned definitions, environments, releases, evaluation gates, discovery | Platform | [02-deployments.md](docs/design/02-deployments.md) |
| 3 | Transport | WebSocket, HTTP + SSE, A2A and Kafka endpoints; session binding; version routing | Platform | [03-transport.md](docs/design/03-transport.md) |
| 4 | Agent runtime | Turn routing, frames, delegation, durable execution, working set, summarization | Agent | [04-agent-runtime.md](docs/design/04-agent-runtime.md) |
| 5 | Governance and security | Identity, model and tool gateways, guardrails, credentials, audit writes | Platform (guardrails: Agent) | [05-governance-and-security.md](docs/design/05-governance-and-security.md) |
| 6 | Context | Conversation history, retention and erasure | Platform | [06-context.md](docs/design/06-context.md) |
| 7 | Knowledge | Ingestion, permission-aware indexes, retrieval | Agent | [07-knowledge.md](docs/design/07-knowledge.md) |
| 8 | Data layer and cross-cutting | Stores, shared identifiers, events | — | [08-data-layer-and-cross-cutting.md](docs/design/08-data-layer-and-cross-cutting.md) |
| 9 | Observability | Traces, metrics, logs, A/B testing, topology, audit log | Both | [09-observability.md](docs/design/09-observability.md) |
| 10 | Features | CDC consumers, Icebreaker questions | Platform | [10-features.md](docs/design/10-features.md) |

Unresolved decisions are in [11-open-questions.md](docs/design/11-open-questions.md). Architecture decisions are in `docs/adr/`.

## Language per plane

From [ADR 0001](docs/adr/0001-language-split.md). Code is written in the language of the plane it belongs to.

| Plane | Language | What lives there |
| --- | --- | --- |
| Agent plane | Python | Agent SDK (runtime kernel, tool/function SDK, client SDK), agent runtime, code sandbox runners, guardrails service, Knowledge, evaluation workers |
| Platform plane | Java 21, Spring Boot | Transport, model gateway, tool gateway, identity provider, Deployments, Builder Canvas backend, Context, Features |
| UI | TypeScript | Builder Canvas UI |

The pydantic classes in `docs/design/` are the reference for the schemas in `contracts/`, not code to copy.

## Change data capture

CDC is not built here. It comes from `../database-change-explorer` ([repo](https://github.com/b1nch3f/database-change-explorer)): Debezium on Postgres, publishing to Kafka. This repo supplies the connector values for its own tables and owns the consumers.

- Topics are `<topicPrefix>.<schema>.<table>`, for example `platform.public.messages`.
- Message changes are keyed by `conversation_id` (`connector.messageKeyColumns`), so one conversation's changes arrive in order.
- Captured tables use replica identity FULL; deletes emit tombstones (used by erasure).
- Connector settings and an example for this platform are in `../database-change-explorer/services/debezium/README.md`.

## Layout

Python first; Java implementations of the transport and Agent SDK come later against the same contracts ([ADR 0002](docs/adr/0002-python-first-mvp.md)). Folders are named by language, and modules that must behave the same share a name on both sides.

```
contracts/     JSON Schema, the single definition of every data model and wire format;
               generate.sh produces the language models, which are committed
python/        uv workspace
  contracts/   generated pydantic models (ap_contracts) - do not edit
  gateway/     pass-through model and tool gateway, Anthropic adapter, scripted fake model (ap_gateway)
  sdk/         Agent SDK: runtime kernel, tools, callbacks, conversation store, test helpers (ap_sdk)
  transport/   HTTP + SSE app, client, the agent-platform CLI (ap_transport)
java/          Gradle multi-module, same module names - not created yet
conformance/   language-neutral test cases every implementation must pass
agents/        agent definitions (YAML) with their tools and tests
deploy/        local environment on k3s - not created yet
docs/          design/ (one file per subsystem) and adr/
```

## Rules every change must follow

1. **Secrets are vault references.** Secret values never appear in environment variables, config files or definitions; only `secret://` references do, resolved at call time. See 05, "Secret references".
2. **Agents never hold refresh tokens.** Agents get short-lived, down-scoped credentials through token exchange. Refresh tokens exist only for connector grants and only in the vault. See 05, "Identity provider".
3. **Every model and tool call goes through the gateway.** No direct provider or tool calls from the runtime, including retrieval from Knowledge. See 05 and 00.
4. **Versions are pinned.** A conversation, and a running execution, stay on the version they started on, across suspends, resumes, rollbacks and new releases. See 02 and 04.
5. **SQL first.** The runtime inserts each event into the SQL DB before appending it to the in-memory working set. See 04, "Append through".
6. **Cross-language calls go through `contracts/`.** Every cross-plane call is a network contract (HTTP, gRPC or Kafka) defined there. No shared code across languages.
7. **Design changes ship with the code.** If a change alters the design, update the matching `docs/design/` file in the same pull request. A new architecture decision gets an ADR.

Each subsystem's "Never does" column in [00-overview.md](docs/design/00-overview.md) is also binding: do not put business logic in the data layer, decisions in the transport, or authorization in the runtime.

## Workflow

- **Plan before coding.** Read the subsystem's design file, state the slice and the plan, and get agreement before writing code.
- **Tests with every change.** Python: pytest with the scripted model. Java: JUnit, with Testcontainers for anything touching Postgres or Kafka. A change without tests is not done.
- **One pull request per slice.** A slice is one thin, working path through one or more subsystems. Keep unrelated changes out.
- **Contracts first.** When a slice crosses planes, change `contracts/` first, regenerate, then change both sides.

## Commands

Run from `python/` unless noted.

| Task | Command |
| --- | --- |
| Install | `uv sync` |
| Test (fake model, no API key) | `uv run pytest` |
| One test file | `uv run pytest sdk/tests/test_runtime.py` |
| Live test against the real model | `uv run pytest -m live` (needs `ANTHROPIC_API_KEY`; costs money) |
| Lint and format | `uv run ruff check . ../agents` and `uv run ruff format . ../agents` |
| Regenerate contract models | `./contracts/generate.sh` from the repo root, after any schema change |
| Serve the example agent | `uv run agent-platform serve --agent ../agents/example/agent.yaml` |
| Chat with it | `uv run agent-platform chat` |
| `java/`, `deploy/` | TODO: add when the module is created |

## Testing

- Tests use `ScriptedModel`, a fake model that replays a script, through the helpers in `ap_sdk.testing`. See `agents/example/test_agent.py` for how to test an agent.
- A behaviour that both languages must share goes in `conformance/` as data, not only in a Python test.
- MVP gaps against the rules above are listed in ADR 0002; do not add new ones without updating it.
