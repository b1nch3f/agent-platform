# 9. Observability

## Responsibility

Shows what happened in every turn, whether the platform is healthy and affordable, and whether users are getting what they need. Keeps an audit trail separate from telemetry.

**Out of scope:** offline evaluations and A/B releases (Deployments).

## Components

| Component | Job |
| --- | --- |
| Instrumentation | OpenTelemetry in runtime, gateway, context and feature services and Builder Canvas; GenAI conventions for model and tool spans |
| Collector | Redaction, tail sampling, routing per signal and tenant |
| Trace, metric, log stores | Per-turn traces, labeled metrics, structured logs |
| Content store | Prompts and tool payloads, only by tenant policy, encrypted, short retention |
| A/B testing | Scores each episode for friction from user behavior, and compares versions in canary and A/B releases on friction and the other guard metrics |
| Audit log | Append-only, hash-chained record of accountable decisions |
| Dashboards and alerts | Views for builders, tenant admins and on-call |
| Topology view | Live graph of which agents call which agents, tools and knowledge sources, built from traces |

## Data model

**Span hierarchy per message:** `turn` → `frame.step_match` (active step), `memory.preload`, `agent.invoke` → `model.call`, `skill.load`, `tool.call` → `gateway.authorize`, `agent.delegate`, `workflow.step`; and `frame.push` when a capability suspends. Every span carries `tenant`, user hash, `conversation_id`, `message_id`, `invocation_id`, `session_id`, agent and version, `execution_id`.

```python
class EpisodeFriction(BaseModel):
    episode_id: str
    agent: str
    agent_version: str
    intent: str
    outcome: Literal["resolved", "abandoned", "failed", "pending"]
    rephrases: int
    corrections: int
    escalated: bool
    turns: int
    tone_score: float | None
    friction_score: float
    band: Literal["smooth", "friction", "failed"]
```

## Key flows

**One trace per turn.** The transport layer starts the trace; `traceparent` is propagated on every model, MCP and A2A call.

**Suspend and resume across days.** The frame stores the suspending span's context; the resume span links back to it. `execution_id` joins all traces of one execution.

**Friction scoring.** The A/B testing job segments each idle conversation into episodes (one per user goal), then counts rephrases, corrections, escalations, unresolved outcomes, effort growth and excess turns, then applies weights fitted against delayed labels (ratings, return within 14 days, repeated requests, sampled human review).

**Tail sampling.** Keep every error, guardrail block, denial, HITL and recovery event, slow or costly turn, high-friction episode, and new version's traffic; sample the rest.

## Metrics

| Group | Examples |
| --- | --- |
| Agent and turn | Turns by outcome, turn latency, time to first token, calls per turn, tokens, cost, guardrail decisions |
| Gateway and tools | Tool latency and errors, authorization denials, budget hits, provider fallbacks |
| Workflows and frames | Pending frames, suspend-to-resume time, frame outcomes, step-match results, HITL decisions, recovery actions |
| Context and features | CDC lag, summarization jobs, icebreaker question refresh lag, icebreaker questions shown, clicked and dismissed |
| Quality | Friction band mix and resolution rate per agent and version |

Labels are limited to tenant, agent, version, model, tool, provider and outcome; never user, conversation, message, invocation or execution IDs.

## Interfaces

| Interface | Shape |
| --- | --- |
| OTLP | All services export traces, metrics and logs |
| Trace view | Span tree per turn with links to the resumed turn, the conversation and the friction record |
| Audit export | Per tenant, for tenant admins |
| Depends on | CDC message changes for friction; identity for pseudonymous user hashes |

## Guarantees

- Nothing is stored before redaction.
- Prompts and payloads are captured only where tenant policy allows, and every view is audited.
- Friction is reported per agent, version and intent; never per person.
- Audit entries are never sampled, and tampering is detectable.
