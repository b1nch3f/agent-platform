# 6. Context

## Responsibility

The context layer stores the conversation history, the single record of what was said. Working memory and summarization belong to the runtime (see Conversation state per turn and Conversation summarization); features built from history, and the change data capture that feeds them, belong to the Features layer.

**Out of scope:** long-term facts and preferences about the user (not provided), frames and pending approvals (runtime), icebreaker questions (Features), skills (Builder Canvas), model weights (model platform).

## What it stores

| Data | Holds | Store of record | Written by |
| --- | --- | --- | --- |
| Conversation history | Every message, append-only | SQL DB | The runtime, write-through as each message happens |
| Conversation summary | Rolling summary and the `seq` it covers, when summarization is enabled for the agent | SQL DB, one row per conversation | The runtime's summarization job |

## Components

| Component | Job |
| --- | --- |
| Conversation store | Append-only message storage; serves the runtime's recent-window reads; applies retention policies and deletes messages on request, which change data capture propagates to everything derived from them |

## Key flows

**Erasure.** Deleting messages produces delete events through change data capture (Features layer); every consumer removes what it derived from them, and the runtime regenerates the conversation summary without them.

**Retention.** Messages are kept per tenant policy; derived data never outlives the messages it came from.

## Interfaces

| Interface | Shape |
| --- | --- |
| Recent-window read | Summary plus newest messages within a token budget, for the runtime at turn start |
| Append | Conditional insert on `(conversation_id, seq)`, by the runtime |
| Erasure | Delete messages for a user or conversation |

## Guarantees

- Every message is stored before it is used, and nothing modifies stored messages except erasure.
- Deleting messages removes everything derived from them, across all consumers.
