# ADR 0002: Python-first MVP in a two-language monorepo

- Status: accepted
- Date: 2026-10-10
- Amends: [ADR 0001](0001-language-split.md)

## Decision

The MVP is built in Python only. The transport and the Agent SDK will later also exist in Java, against the same contracts. Until then, several things differ from ADR 0001 and the design files:

| Area | ADR 0001 and design | MVP |
| --- | --- | --- |
| Transport | Java service | Python library and app (`python/transport`), HTTP + SSE only |
| Model and tool gateways | Java services | In-process Python module (`python/gateway`), pass-through; the runtime still makes every model and tool call through it |
| Context | Java service owning conversation history | A `ConversationStore` interface in the Agent SDK with an in-memory implementation |
| Identity | Identity provider, token exchange | A fixed dev identity (`tenant=dev`, `user_id=dev-user`) set by the transport |
| Model provider key | Vault reference | `ANTHROPIC_API_KEY` from the environment, read by the Anthropic adapter |
| Agent SDK languages | Python | Python now; Java later. Whether the Java SDK is a client and tool SDK or a full second runtime is open |

Repository layout follows from having two implementations of the same components:

- Top-level folders are named by language, `python/` and `java/`, not by plane. Modules that must behave the same have the same name on both sides (`contracts`, `transport`, `sdk`, `gateway`).
- `contracts/` holds JSON Schema as the single source for every data model and wire format: the turn envelope, outbound events, the agent definition and the gateway messages. Language models are generated from it and committed.
- `conformance/` holds language-neutral test cases (input and expected output as data). Every implementation runs the same files.

## Why

- One language gets a working end-to-end turn soonest, and that turn is what every later slice is tested against.
- Two implementations stay compatible only if neither owns the format, so the schemas and the conformance cases are neutral from the first commit.
- The gateway is kept as a separate module with its own contract even while in-process, so the rule "every model and tool call goes through the gateway" holds now and moving it behind a network call later changes no caller.

## Consequences

- Rules 1, 2 and 5 in `CLAUDE.md` (vault references, token exchange, SQL first) are not met by the MVP. Each has one known place to fix: the Anthropic adapter's client, the transport's identity, and the conversation store.
- Conversations are lost on restart, there is nothing for CDC to capture, and a suspended workflow could not survive a restart, until the SQL-backed store lands.
- The gateway message format includes a `ProviderBlock`: a provider-native content block (for example model reasoning) that the runtime stores and returns unchanged, because providers require such blocks to be replayed exactly.
- The transport sends the event cursor as the SSE `id` and accepts `Last-Event-ID` for replay, instead of a custom reconnect parameter.
- Stream event names are the design's own (`token`, `tool_status`, `turn_done`, `error`, ...), not a third-party protocol's.
