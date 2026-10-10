# Agent Platform

A platform for building, shipping and running AI agents: nine subsystems plus Features on a shared data layer. Models and agents make decisions; the platform makes guarantees.

The repo is at the start of the build. `docs/design/` is the source of truth for what each subsystem does; read the relevant file before changing a subsystem. Start with [docs/design/README.md](docs/design/README.md) and [00-overview.md](docs/design/00-overview.md).

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

Intended layout. Only `docs/` exists so far; create the others as the first slice in each lands.

```
contracts/   JSON Schema, the single definition of every data model and cross-plane call;
             Java classes and pydantic models are generated from it
platform/    Gradle multi-module build, one module per platform-plane service
agent/       uv workspace, one package per agent-plane component
deploy/      Local environment on k3s (Helm charts or manifests)
docs/        design/ (one file per subsystem) and adr/
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
- **Tests with every change.** Java: JUnit with Testcontainers for anything touching Postgres or Kafka. Python: pytest. A change without tests is not done.
- **One pull request per slice.** A slice is one thin, working path through one or more subsystems. Keep unrelated changes out.
- **Contracts first.** When a slice crosses planes, change `contracts/` first, regenerate, then change both sides.

## Commands

| Area | Build | Test |
| --- | --- | --- |
| `contracts/` | TODO (code generation) | TODO |
| `platform/` | TODO (`./gradlew build` once the Gradle build exists) | TODO (`./gradlew test`) |
| `agent/` | TODO (`uv sync` once the workspace exists) | TODO (`uv run pytest`) |
| `deploy/` | TODO (bring up the local k3s environment) | — |

Replace each TODO with the real command in the pull request that creates the module.
