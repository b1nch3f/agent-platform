# 3. Transport

## Responsibility

Connects clients and remote agents to the platform over several protocols, and turns every protocol into the same thing: authenticated turns in, streamed events out. It holds no business logic and makes no routing decisions; the runtime sees one turn format whatever the protocol.

**Out of scope:** model and tool calls (governance gateway), deciding delegation permissions (governance).

## Components

| Component | Job |
| --- | --- |
| WebSocket endpoint | Long-lived, bidirectional connection for chat apps: messages and approvals in; tokens, tool status, pending-item updates and approval requests out |
| HTTP + SSE endpoint | A request with a streamed response, for simple clients and integrations |
| A2A server | Implements the A2A protocol directly (no third-party library): publishes agent cards, accepts tasks from remote agents, streams task status |
| A2A client | Outbound delegation from the runtime to remote agents, after the delegation policy allows it |
| Event bus (Kafka) | Ingress and egress for background agents: consumers read input topics and hand turns to the runtime; producers write results and events to output topics |
| Session binding | Validates the identity token, resolves tenant and user, assigns the session\_id, attaches the session to a conversation, checks the entitlement to start the agent |
| Turn normalizer | Converts every protocol's message, and every Kafka event, into one turn envelope |
| Event fan-out | Delivers runtime events to every session connected to the conversation, so all devices stay in sync, with a resume cursor; for background agents, writes them to output topics |

## Two ingress modes

The transport is decoupled from the runtime: whichever path a message takes, the runtime only sees a `TurnEnvelope` in and `OutboundEvent`s out.

|  | Interactive | Background |
| --- | --- | --- |
| Who | Users in chat apps; remote agents over A2A | Event-triggered or scheduled agents: ticket triage, nightly reports, reacting to system events |
| Transport | WebSocket, HTTP + SSE, A2A | Kafka, producer and consumer style: input topics in, output topics out |
| Latency | Streaming tokens, sub-second | Seconds to minutes |
| Who answers HITL | The user, on a resume card | An approver through a notification or inbox, or an upstream system, as a decision event on an input topic |

## Event bus design (background agents)

- **Partitioning.** Topics are partitioned by `conversation_id` (or `execution_id` for one-off jobs), so all events for one conversation reach one consumer in order and never race on the same frame stack. Conditional updates on the conversation document stay as a safety net during rebalances.
- **At-least-once delivery.** The runtime deduplicates on `message_id`. The offset is committed only after the turn's results and state changes are persisted.
- **Long turns.** A turn can outlast Kafka's poll interval. The consumer hands the turn to a worker pool and pauses the partition until it finishes, or the consumer group runs with a raised `max.poll.interval.ms`.
- **Retries and poison messages.** A retry topic with backoff, then a dead-letter topic after N failures, so one bad event cannot block its partition.
- **HITL.** Unchanged in the runtime: the same frames, node cache and interrupt/resume handler. Resume cards become notifications to an approver, and the decision arrives as a decision envelope on an input topic. With no user typing, frames here mostly wait for approvals rather than being interrupted.
- **Transport, not state.** Topics can have short retention. The NoSQL DB remains the source of truth for frames and node results.

## Identifiers

The transport layer owns the identifiers that tie connections, conversations and runs together.

| ID | Identifies | Created by |
| --- | --- | --- |
| `session_id` | One connection from one device. A user on a phone and a laptop has two sessions | Transport, at connect |
| `conversation_id` | The thread of messages, shared by every device the user continues it on | Transport, on the first message, or supplied by the client to continue a thread |
| `message_id` | One user message, which starts one turn | The client, so double-sends can be deduplicated; validated by the transport |
| `invocation_id` | One run of an agent or workflow: each invoke, and each resume of a suspended one | Transport for the top-level run of a message; runtime for nested invocations and resumes |
| `execution_id` | One durable run of a workflow or sub-agent, across all its invocations until it completes | Runtime, at the first invoke |

A conversation holds messages, each message triggers one or more invocations, and a session is one device's view of the conversation. `execution_id` stays the same across invocations: a purchase started by message 1 and resumed by message 4 is one execution with two invocations. That stable ID is what the node cache and frames are keyed on, since `invocation_id` changes on every resume.

The Agent SDK uses these identifiers directly: conversation state is keyed by `conversation_id`, and every run of the agent loop or workflow executor carries an `invocation_id`.

## Data model

```python
class TurnEnvelope(BaseModel):
    message_id: str            # client-generated; deduplicates double-sends
    conversation_id: str
    session_id: str            # the device connection it arrived on
    invocation_id: str         # top-level run for this message
    protocol: Literal["websocket", "http_sse", "a2a", "kafka", "local"]
    identity: CallIdentity     # user or calling agent, tenant
    kind: Literal["message", "decision", "cancel"]
    payload: dict              # text and attachments, or action_id and decision
    a2a_task_id: str | None

class OutboundEvent(BaseModel):
    conversation_id: str
    message_id: str
    invocation_id: str | None
    cursor: int                # increases monotonically per conversation
    type: Literal["token", "tool_status", "pending_updated",
                  "approval_request", "turn_done", "error"]
    data: dict
```

**A2A task state mapping**

| Runtime state | A2A task state |
| --- | --- |
| Turn running | working |
| Frame pushed, waiting for input or approval | input-required |
| Execution completed | completed |
| Cancelled, or frame expired | canceled |
| Failed | failed |

A remote agent answers an input-required task by sending a message on the same task. The transport turns it into a turn envelope, and step matching in the turn router applies as usual.

## Key flows

**Local turn, no protocol.** The same transport core can be called in-process, with no network in between: a terminal client hands it the user's text and reads the events back directly. Identifiers, deduplication, cursors and event order are identical to a network turn; only `protocol` differs (`local`). This is how an agent runs on a user's own machine, with its tools acting there.

**Inbound turn, any protocol.** Connect → session binding (token, tenant, entitlement) → normalize into a `TurnEnvelope` → runtime → events streamed back over the same connection.

**Approval over WebSocket.** The runtime emits `approval_request` with an `action_id` → the client renders a button → a decision envelope carrying the `action_id` → the runtime resumes the frame.

**Reconnect.** The client reconnects with its last cursor → fan-out replays missed events from a short per-conversation buffer, then continues live.

**Outbound delegation.** The runtime asks to delegate → the governance delegation policy allows it and mints a down-scoped token → the A2A client sends the task → remote status updates flow back into the delegating execution.

## Interfaces

| Interface | Shape |
| --- | --- |
| WebSocket | One connection per client session; JSON messages using `TurnEnvelope` and `OutboundEvent` |
| HTTP + SSE | Post a turn; the response is an SSE stream of `OutboundEvent`s |
| A2A | Agent cards for published agents; task send, stream and status per the A2A protocol |
| Depends on | Identity provider; Builder Canvas entitlements; governance delegation policy; runtime |

## Guarantees

- Every protocol produces the same turn semantics; the runtime never branches on protocol.
- Nothing reaches the runtime before authentication and the entitlement check.
- A repeated `message_id` reaches the runtime once.
- A reconnecting client receives events in order and without gaps, within the replay buffer window.
- A HITL frame is visible to A2A callers as input-required, so remote agents can supply approvals and inputs.

## Limits

Idle-connection timeout; maximum open connections per user; replay buffer per conversation (for example, five minutes of events).
