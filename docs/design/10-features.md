# 10. Features

## Responsibility

User-facing capabilities built on top of platform data, as a separate vertical. Each feature reads from other layers (conversation history through CDC, runtime state through events), is configured per agent in the agent builder, and can be turned off without affecting how agents run. The first feature is Icebreaker questions.

**Out of scope:** running agents (runtime), storing conversations (context), enforcement (governance).

## Icebreaker questions

When a user opens an agent, it shows two to four suggestions based on their own history, so they can continue instead of starting from scratch.

> **Pick up where you left off** \[Approve the laptop licence purchase\] \[Check if order 123 arrived\] \[Compare annual plans\]

| Source | Comes from | On click |
| --- | --- | --- |
| Unfinished workflows | The runtime's open-frame index | Shows that workflow's resume card |
| Follow-ups | Recent conversations left unresolved: read from the conversation summary when summarization is enabled, otherwise from the conversation's last messages | Prefills the message, for the user to send or edit |
| Recommendations | A small model reading the same recent conversations and proposing a likely next question | Prefills the message |

**Components**

| Component | Job |
| --- | --- |
| Change data capture | Reads the SQL DB's transaction log and publishes committed message changes to Kafka; the dependency every history-based feature builds on |
| Icebreaker generator | Builds and refreshes a user's icebreaker questions for an agent; ranks unfinished workflows first (by urgency, then age), then follow-ups, then recommendations |
| Icebreaker service | Serves icebreaker questions when a conversation opens, filtered against live frame state; records shown, clicked and dismissed |

**Change data capture.** Every committed change to conversation messages is published to Kafka, keyed by `conversation_id`, so each conversation's changes arrive in order. There are no database triggers and no dual-write gap: an event exists exactly when a write committed.

| Aspect | Design |
| --- | --- |
| Capture | A log-based CDC connector (for example Debezium on Kafka Connect) reads the write-ahead log |
| Consumers | Icebreaker questions, and observability's friction scoring; each derives its own triggers, such as "conversation idle" |
| Delivery | At least once; consumers are idempotent by `message_id` |
| Erasure | Deleted messages produce delete events, which every consumer applies |
| Operations | Alert on connector lag and on database log retained for the connector (for example a Postgres replication slot), which can fill the disk if the connector stops |
| Security | Topics carry conversation content: encrypted, restricted ACLs, short retention |

**Triggers.** The generator consumes CDC message changes and turns them into a "conversation idle" trigger per conversation (a session window). It also refreshes on frame events from the runtime (pushed, resumed, expired) and when an icebreaker question expires. Refreshes are idempotent by `{user}/{agent}/{conversation}/{last seq}`. Opening a conversation never waits for a model call.

**Configuration in the agent builder**

```yaml
icebreaker_questions:
  enabled: true
  max: 3
  sources: [unfinished_workflows, follow_ups, recommendations]
  lookback_days: 30
```

**User control and privacy**

- Icebreaker questions are per user and per agent; they never draw on another user's history or another agent's conversations.
- Users can dismiss an icebreaker question (it is not shown again) or clear their history for the agent.
- Conversations flagged sensitive by guardrails are never used for follow-ups or recommendations.
- Recommendation text is generated from the user's own conversations and passes the agent's output guardrails.

**Data model**

```python
class IcebreakerQuestion(BaseModel):
    question_id: str
    tenant: str
    user_id: str
    agent: str
    source: Literal["unfinished_workflow", "follow_up", "recommendation"]
    text: str                          # what the button says
    action: Literal["resume_frame", "prefill"]
    execution_id: str | None           # for unfinished workflows
    prefill: str | None                # message text for follow-ups and recommendations
    source_conversation_ids: list[str]
    rank: int
    expires_at: datetime
    dismissed: bool
```

Icebreaker questions are stored in the SQL DB and cached when served.

**Erasure and retention.** A delete event for a message removes icebreaker questions built from its conversation. Icebreaker questions use only conversations within the lookback window and expire when their source conversation leaves it or their frame closes.

**Interfaces**

| Interface | Shape |
| --- | --- |
| Get icebreaker questions | `GET /agents/{agent}/icebreaker-questions`, for the calling user, at conversation open |
| Dismiss | `POST /icebreaker-questions/{id}/dismiss` |
| Clear history | `DELETE /agents/{agent}/history`, for the calling user |
| Events in | CDC message changes; frame events from the runtime |
| Depends on | Context layer (conversation history and summaries); runtime open-frame index; model gateway for recommendations; identity for scope |

**Measuring value.** Shown, clicked and dismissed counts per agent and source go to observability, so builders can see whether icebreaker questions help and turn sources off when they don't.

## Guarantees

- Features never change how an agent runs; disabling a feature has no effect on conversations.
- Icebreaker questions draw only on the requesting user's own history with that agent.
- An unfinished-workflow icebreaker question is never shown for a workflow that has already been resumed, completed or expired.
- Opening a conversation never waits for a model call.
