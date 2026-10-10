# ADR 0001: Language per plane

- Status: accepted
- Date: 2026-10-10

## Decision

Code is written in the language of the plane it belongs to.

| Plane | Language | Subsystems and services |
| --- | --- | --- |
| Agent plane | Python | Agent SDK (runtime kernel, tool/function SDK, client SDK), agent runtime (agent executor, workflow executor, frames, node cache, callbacks, summarization), code sandbox runners, guardrails service, Knowledge (ingestion and retrieval), evaluation workers |
| Platform plane | Java 21, Spring Boot | Transport (WebSocket, HTTP+SSE, A2A, Kafka ingress), model gateway, tool gateway, identity provider, Deployments, Builder Canvas backend, Context, Features (CDC consumers, Icebreaker questions) |
| UI | TypeScript | Builder Canvas UI |

Change data capture is not built here. It comes from [database-change-explorer](https://github.com/b1nch3f/database-change-explorer) (Debezium on Postgres to Kafka); this repo supplies the connector configuration for its tables and owns the consumers.

## Why

- Builders write agents, tools and function nodes in Python, so the SDK and the runtime that executes them are Python too, in the same process model.
- The guardrails service runs LLM and ML classifiers, where the Python ecosystem is far ahead. The gateways call it over the network, so its language is independent of theirs.
- The platform plane is long-lived infrastructure next to Kafka, Debezium and Keycloak, all Java; strict typing, virtual threads and Spring Security's OAuth support fit it.
- The runtime is I/O-bound and keeps its state in the databases, so asyncio and horizontal scaling are enough.

## Consequences

- Data models are defined once as JSON Schema in `contracts/` and generated into pydantic (Python) and Java classes. The pydantic classes in `docs/design/` are the reference for those schemas, not code to copy.
- Every cross-plane call is a network contract (HTTP, gRPC or Kafka) defined in `contracts/`; no shared libraries across languages.
- Two toolchains in CI: uv for Python, Gradle for Java.
