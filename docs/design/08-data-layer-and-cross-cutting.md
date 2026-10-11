# 8. Data layer and cross-cutting

## Data layer

| Store | Owns | Written by |
| --- | --- | --- |
| SQL DB | Conversation messages, conversation state, Builder Canvas definitions and entitlements | Runtime, Builder Canvas |
| NoSQL DB | Conversation state (active slot and frame stack), open-frame index per user, executions, node results, decision records | Runtime |
| Object storage | Large node outputs, sub-agent contexts, snapshots, captured telemetry content | Runtime, collector |
| Vector index | Knowledge chunks, embeddings and access lists | Knowledge ingestion, permission sync |
| Secret vault | Provider keys, client secrets, refresh tokens and connector credentials; definitions hold only references | Identity provider, model gateway registration |
| SQL DB: feature tables | Icebreaker questions | Icebreaker generator only |
| Cache (optional) | Cached pending-frame lookups; never authoritative | Runtime |
| Telemetry stores | Traces, metrics, logs | Collector |
| Audit log | Accountable decisions | All subsystems |

Removed during design: a graph database, Redis-first frame tiering, and LiteLLM. Kafka is the event bus: background-agent ingress and egress (see [Transport](03-transport.md)) and change data capture (see [Features](10-features.md)).

## Shared identifiers

| ID | Created by | Used by |
| --- | --- | --- |
| `tenant`, user hash | Identity | Everything |
| `conversation_id` | Transport only, on the first message; never supplied new by a client | Runtime (frames, pending items), context, features, observability |
| `message_id` | Client, validated by transport | Runtime (deduplication), context, features, observability |
| `invocation_id`, `invocation_type` | Runtime, for every run of an agent or workflow, top-level or nested | Runtime, gateway, observability |
| `execution_id`, `root_exec_id` | Runtime | Node cache, frames, gateway, Builder Canvas (revocation) |
| Node key, `idempotency_key` | Runtime | External systems |
| `frame_id`, `action_id` | Runtime | Approvals, audit |
| `episode_id` | A/B testing | Friction records |
| `trace_id` | Observability | Messages, records, audit |

## Events

| Event | From | To |
| --- | --- | --- |
| `turn.completed` | Runtime | Observability |
| `frame.pushed`, `frame.resumed`, `frame.expired` | Runtime | Observability, user notifications |
| `CDC message changes` | SQL DB, via change data capture | Icebreaker generator, erasure, friction scoring |
| `entitlement.changed` | Builder Canvas | Ingress, gateway, runtime |
| `capability.published` | Builder Canvas | Deployments (store, evaluate, release) |
| `version.released, version.rolled_back` | Deployments | Transport, runtime, observability |
| `turns.erased` | Erasure request | Context, features (icebreaker questions), content store, friction records |

## One turn across subsystems

1. Ingress authenticates the user and checks the entitlement (governance).
2. The runtime offers the message to pending frames, then to the root agent (runtime).
3. The runtime loads the conversation summary and recent messages from the context layer and assembles context (runtime, context).
4. Every model and tool call passes the gateway's authorization, guardrails and budgets (governance).
5. Node results are cached; a HITL step suspends as a frame (runtime).
6. The turn trace is completed and sampled (observability).
7. Later, the user's icebreaker questions are refreshed and the conversation is scored for friction (features, observability).
