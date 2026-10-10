# 4. Agent runtime

## Responsibility

Runs agents and workflows for each user turn, and guarantees that suspended work resumes correctly, exactly once, on the version it started on. It routes by fixed rules and makes no semantic decisions; workflows judge their own step input and agents choose capabilities.

**Out of scope:** what an agent decides, routing quality inside builder-designed agents, and authorization (enforced by governance).

## Components

The runtime is built on the platform's own **Agent SDK**, not a third-party agent framework, because the turn router, active slot, frame stack and interrupt semantics need to own the turn lifecycle. The two executors run capabilities; the other components handle state, resumption and hooks.

| Component | Job |
| --- | --- |
| Agent executor | Runs the Agent SDK's agent loop: skills, tools, sub-agents, composites; implements the delegation modes (invoke, handoff, spawn, await, resume) |
| Workflow executor | Runs code workflows and agent-node workflows with explicit control flow; parks at HITL steps; each waiting step runs its own `StepMatch` check |
| Stack frame manager | Holds the active slot and the frame stack per conversation, and the user-level open-frame index; push, pop, transfer and expiry |
| Workflow node cache | Caches each completed node's result; on resume returns cached results, re-runs nodes marked rerun\_on\_resume and any downstream node whose input changed; recovers nodes left `started` through each tool's recovery contract |
| Workflow interrupt/resume handler | Turns a step's `interrupt` into push, clear and fresh routing; sends resume cards with the backoff and \[Later\]; handles cross-conversation transfer |
| Conversation state and summarization | Loads the summary and recent window at turn start, keeps the per-turn working set, writes messages through, and runs the per-agent summarization job |
| Callbacks | Platform hooks at four levels, listed below |

**Callbacks**

| Level | Fires | Before | After |
| --- | --- | --- | --- |
| Runtime | Once per message | **Turn router**: deliver the message to the active workflow's waiting step, or route fresh to the root agent | Turn completion: clear finished workflows, run the resume-card check, queue summarization if enabled and over threshold, emit `turn.completed` |
| Agent / workflow | Each invocation or resume of an agent or workflow, including sub-agents and nested workflows | Input guardrails, entitlement check | Output guardrails |
| Model | Each model call | Context assembly, prompt policy | Response checks |
| Tool | Each tool call, including workflow tool nodes and `run_code` | Early authorization, HITL routing | Result scanning, audit |

Eight callbacks in total: a before and an after at each of the four levels. Each level also has an `on_error` variant of its after-callback, for failure handling. Workflow function nodes have no callbacks of their own; they run inside the workflow's pair, and any tool or model calls they make fire the tool and model callbacks.

The turn router lives in the runtime's before-callback, so it runs once per message before any agent or workflow code. Callbacks give early, in-process checks; the gateway remains the authoritative enforcement point.

## Conversation state per turn

Messages are written through to the SQL DB as they happen; the model works from an in-memory working set that exists only for one turn. The runtime owns the working set and the rolling summary; the context layer owns the stored messages.

**Storage.** The messages table is append-only, keyed by `(conversation_id, seq)`, with `message_id` unique for deduplication and a `token_count` computed at write time. When summarization is enabled, one row per conversation holds the rolling summary and the `seq` it covers.

**Lifecycle of a turn**

1. **Load once.** At the start of the turn, read the rolling summary (if any) plus the newest messages, backwards from the end until the recent-messages token budget is full (one index range scan). An optional shared cache can serve this window for active conversations.
2. **Append through.** Each new event (the user message, each model reply, each tool call and result) is inserted into the SQL DB first, then appended to the in-memory working set. Partial streaming chunks are not stored.
3. **Model calls read the working set.** Every model call in the turn builds its context from the working set; nothing is re-read from the database between calls.
4. **Discard.** When the turn ends, the working set is dropped. Only the database persists; the next turn starts again at step 1.
5. **Summarize, if enabled.** The runtime after-callback queues a background job that folds older messages into the rolling summary (see Conversation summarization). The original messages stay in the SQL DB.

**Example**

| Turn | Step | SQL DB | Working set |
| --- | --- | --- | --- |
| 1 | Load (new conversation) | Create conversation row | empty |
| 1 | User message M1 | Insert M1 | M1 |
| 1 | Model call 1 → tool call M2 | Insert M2 | M1, M2 |
| 1 | Tool result M3 | Insert M3 | M1–M3 |
| 1 | Model call 2 (context from working set) → answer M4 | Insert M4 | M1–M4 |
| 1 | Turn ends | M1–M4 stored | dropped |
| 2 | Load: summary + recent window | Read M1–M4 | M1–M4 |
| 2 | User message M5, model answer M6 | Insert M5, M6 | M1–M6 |
| 2 | Turn ends | M1–M6 stored | dropped |

**Concurrency.** Appends assign the next `seq` with a conditional insert on `(conversation_id, seq)`. If another pod or device appended in between, the insert conflicts and the turn reloads instead of writing a stale history. Per-invocation variables live only in the working set; anything needed in later turns is written to conversation state.

## Conversation summarization

Summarization is a per-agent setting in the agent builder. When enabled, older messages are folded into a rolling summary so long conversations stay within the context budget.

```yaml
summarization:
  enabled: true
  trigger: {token_threshold: 24000}     # or {every_turns: 10}
  keep_recent_tokens: 8000              # always kept verbatim
  model: <tenant-allowed small model>
```

**Where it runs.** In the **runtime after-callback**, not the agent after-callback. The agent/workflow after-callback fires for every invocation, including sub-agents and nested workflows, while summarization is about the conversation as a whole, so it belongs at turn completion, once per message. The callback only checks the condition (stored token counts against the threshold) and queues a summarization job; the job runs in the background after the reply has been delivered, so the user never waits for it and the next message is not delayed.

**The job** reads the current summary and the messages it does not cover beyond the recent window, asks the summarization model for a new summary, and writes it to the conversation row with the `seq` it covers, using a conditional update so two jobs cannot overwrite each other. The next turn's load picks it up.

**When it is disabled,** the working set is just the newest messages that fit the budget; older ones are dropped from context but stay in the SQL DB.

## Agent SDK

The platform builds its own SDK in three parts, split by audience. It implements the agent loop and platform semantics itself, and uses the official libraries for every open protocol.

| Part | Audience | Contains |
| --- | --- | --- |
| Runtime kernel | Platform internal | Agent loop (model call, tool calls including parallel ones, stop conditions), workflow executor, callbacks at four levels, stack frame manager, node cache |
| Tool and function SDK | Builders who write code (tool builder, custom function nodes) | A decorator that turns a typed function into an MCP tool, with schemas from the signature and a declared contract: risk tier, recovery contract, secrets, egress |
| Client SDK | Apps and integrations | Transport protocols, `TurnEnvelope` and `OutboundEvent`, reconnect with cursor, resume cards and buttons |

**Adopted standards rather than rebuilt:** the MCP Python SDK for tools, the A2A SDK for remote agents, Pydantic for schemas, and OpenTelemetry with the GenAI semantic conventions for traces.

**Model providers are handled in the gateway, not the SDK.** The model gateway holds one adapter per provider and exposes a single internal message and tool-call format, covering streaming, reasoning output, structured output, multimodal input and prompt caching. Provider API changes are fixed in one place, and the SDK never calls a provider directly.

**Other frameworks.** Agents built with other frameworks join as remote agents over A2A, so interoperability does not depend on the SDK.

## Code execution sandbox

Agents can run code for analysis, data transformation and calculation, and skills can ship scripts. Model-generated code is untrusted input, so it runs in an isolated sandbox that holds no credentials and reaches nothing by default.

**Should code execution be allowed?** Yes, but off by default and enabled per agent. Code execution gives real capability that tools cannot match (analysing an uploaded spreadsheet, charting, ad hoc transformations), and many agent tasks depend on it. It is also the largest attack surface: a prompt injection can turn it into data exfiltration, resource abuse or an escape attempt. Prefer a purpose-built tool where one exists. Enable code execution only for agents that need it, with tenant-admin approval, under the controls below.

**Two tiers**

| Tier | Runs | Trust | Isolation |
| --- | --- | --- | --- |
| Reviewed code | Function tools and custom function nodes from the tool and workflow builders; skill scripts that passed review | Reviewed at publish, versioned | Container sandbox (gVisor), declared egress only |
| Generated code | Code the model writes during a turn | Untrusted | MicroVM per execution (Firecracker or Kata), no network by default |

**Controls for generated code**

| Control | Default |
| --- | --- |
| Lifetime | One sandbox per invocation; destroyed when the invocation ends |
| Network | None. An agent can be given an egress allowlist; anything else goes through the tool gateway |
| Credentials | Never inside the sandbox; no tokens, keys or environment secrets |
| Files in | Only files the user attached or the turn produced, copied in read-only |
| Files out | Written to an output folder, scanned (secrets, PII, size, type), then stored as artifacts with user-scoped access |
| Resources | CPU, memory, disk and wall-clock limits per execution; per-tenant concurrency limit |
| Packages | A preinstalled, vetted image; installing packages only through an internal mirror, if allowed at all |
| Tenancy | No sandbox is reused across users or tenants |
| Audit | The code, its inputs, outputs and exit status are recorded on the trace, under content-capture policy |
| HITL | Required before running code that has network egress or writes outside the output folder |

**Interface.** The sandbox is exposed to agents as a tool (`run_code(language, code, files)`) through the tool gateway, so authorization, budgets, guardrails and audit apply as for any other tool. Results return as text, tables or file artifacts.

Only durable workflows (those with HITL, which are always top level or sub-agents) can be active or stacked. Workflows without HITL finish inside one message and never create a frame.

## Capabilities it executes

| Capability | Own context | Can suspend | Progress tracked by |
| --- | --- | --- | --- |
| Skill | No | No | Current agent |
| Tool | No | No | Node cache |
| Code workflow | No | Yes, at HITL steps | Node cache |
| Agent-node workflow | Per agent node | Yes | Node cache |
| Sub-agent | Yes | Yes | Frame plus its own context |
| Stateless composite (sequential, parallel, fan-out, fan-in) | Per member | No | The LLM, in one invocation |

**HITL placement.** A workflow with HITL runs only at top level or as a sub-agent, never as a tool, so frames are always pushed at a boundary the runtime owns. The Builder Canvas enforces this at publish; the runtime rejects any suspend from tool mode.

## Data model

```python
class StepPrompt(BaseModel):
    """What a waiting step asks. Produced by the workflow, never by the runtime."""
    text: str                    # "Approve ₹4,80,000 from cost centre CC-114?"
    options: list[str]           # from the step definition: ["Approve", "Reject"]
    irreversible: bool

class Waiting(BaseModel):
    """A durable workflow parked at a step that needs the user's input."""
    execution_id: str
    workflow: str
    workflow_version: str        # pinned at start
    title: str                   # short label set at start: "Purchase 20 laptop licences"
    step_id: str
    prompt: StepPrompt
    waiting_since: datetime

class Frame(Waiting):
    """An interrupted workflow on the stack."""
    interrupted_at: datetime
    urgency: Literal["low", "normal", "high"]   # from the workflow definition
    card_shown: bool             # resume card already sent for this interruption
    cards_sent: int              # for the backoff and the maximum
    messages_since_card: int     # backoff is counted in user messages
    snoozed_until: datetime | None   # set by [Later]
    expires_at: datetime

class ConversationState(BaseModel):
    """One NoSQL document per conversation, so every transition is one atomic update."""
    conversation_id: str
    active: Waiting | None       # the workflow waiting for the next message
    stack: list[Frame]           # interrupted workflows; last element is the top
    version: int                 # conditional updates

class OpenFrameIndex(BaseModel):
    """User-level index, so any conversation can find the user's open frames."""
    user_id: str
    execution_id: str
    conversation_id: str         # current owner; changes on transfer
    title: str
    urgency: Literal["low", "normal", "high"]
    expires_at: datetime

class NodeResult(BaseModel):
    key: str                     # {execution_id}/{node_path}#{iteration}
    input_hash: str              # cache hit only if the input is unchanged
    idempotency_key: str         # derived from key; sent to external systems
    status: Literal["started", "completed", "failed"]
    output: dict | None          # or pointer to object storage
    workflow_version: str

class StepMatch(BaseModel):
    matches_current_step: bool
    intent: Literal["approve", "reject", "modify", "provide_input", "unrelated"]
    extracted_input: dict | None
    ambiguous: bool
    confidence: float
```

Executions carry `parent_exec_id`, `root_exec_id`, status and pinned version. Decision records carry `action_id`, user, the exact text or button, and time.

## Key flows

**Turn routing.** The runtime applies fixed rules; the waiting step and the root agent make every judgment.

| Situation | Runtime action |
| --- | --- |
| An active workflow is waiting | Deliver the message to its waiting step. On a match, the workflow continues |
| The step reports no match | Interrupt: push the active workflow as a frame, clear the active slot, route the message fresh |
| Nothing active, resume card showing | Offer the message to the top frame's step; a match pops the frame into the active slot. No match routes fresh and leaves the frame stacked |
| Nothing waiting | Route fresh: the root agent picks a skill, tool, sub-agent or workflow |
| A capability parks at a HITL step | It becomes the active workflow |

**Interrupt.** The waiting step runs its `StepMatch` check. On no match it emits `interrupt` up to the runtime, which in one atomic update pushes a frame (execution, step, prompt, title) and clears the active slot. The message is then routed as if it were the first one.

**Resume card.** When a turn ends (a final answer, or another workflow completes) with nothing active and frames stacked, the runtime re-asks the top frame's own step. It never asks "would you like to continue?" first:

> **Back to your earlier request:** purchase 20 laptop licences Approve ₹4,80,000 from cost centre CC-114? \[Approve\] \[Reject\] \[Later\]

| Card part | Source |
| --- | --- |
| Heading | The frame's title, set when the workflow started |
| Question | The step's prompt, refreshed first by replaying `rerun_on_resume` nodes so values are current |
| Step buttons | The step definition's options; answered by the workflow |
| \[Later\] | Added by the runtime to every card; the only button it handles itself |

The card is sent once per interruption, as a card distinct from the agent's answer, with a count when more frames are waiting. Answering the step pops the frame and delivers the answer to the workflow. \[Later\] snoozes the frame and leaves its step untouched. Ignoring the card leaves the frame stacked; it is shown again only after the user is idle, on "what's pending?", or before TTL expiry. On text-only channels the card becomes one sentence; over A2A it is the task's `input-required` state.

**Approvals.** A step answer on an irreversible step needs a button carrying the `action_id`, or an explicit echo-confirm for typed or ambiguous replies. Resume is a conditional update on the conversation document's `version`, and the decision record is written before the workflow continues, so a double-sent approval resumes once.

**Reminder policy.** Resume cards back off exponentially, counted in user messages since the last card rather than in time, so the user has room to ask other things. Urgency is a setting on each workflow, chosen in the workflow builder; sales workflows default to high.

| Reminder | Normal | High urgency | Low urgency |
| --- | --- | --- | --- |
| 1st | When the interrupting request finishes | Same | Same |
| Then | After 2, then 4 more messages | After 1, then 2 more messages | After 4 more messages |
| Maximum cards | 3 | 4 | 2 |

After the maximum, the frame is shown only on "what's pending?", after the user goes idle, or before TTL expiry. Answering the step or clicking \[Later\] resets the backoff for the next interruption, and a snoozed frame is never shown, whatever the backoff says.

**Cross-conversation pending items.** Cards also follow the user into new conversations. A user-level index of open frames lets a new conversation find them in one query. In the new conversation the frame appears as a compact pending card (title, the step's buttons, a link to the original conversation), at most once per conversation, and by default only for high-urgency workflows. Showing the card moves nothing.

**Transfer of ownership.** A workflow's state lives with its execution (node cache, step prompt, sub-agent context), not in a conversation; a conversation only holds a pointer to it in its active slot or stack. When the user answers a pending card in conversation B for a frame owned by conversation A (a button, or a typed reply its step matches):

1. In one NoSQL DB transaction: remove the frame from A's stack, make it B's active workflow (pushing B's current active workflow onto B's stack if there is one), point the open-frame index at B, and write the decision record. A conditional check on A's frame makes the transfer happen once if the user also answers in A.
2. Deliver the answer to the step; the workflow continues in B.
3. Leave a marker in A: "Continued in \[conversation B\]".

A's history stays in A; the workflow does not need it, since its inputs are in its own state. The resume span in B links to the suspend span in A through the same `execution_id`, and `frame.transferred` is written to the audit log with both conversation IDs. Transfer is for the same user only; another person approving is an approval delegation governed by policy. Irreversible steps still need a button or echo-confirm, with full details on the card. \[Later\] in B leaves the frame in A.

**Storage.** The active slot and frame stack live in the NoSQL DB, one document per conversation, plus the user-level open-frame index. Not in process memory: frames wait for days, must survive restarts and deploys, must be reachable from any pod and device, and are looked up across conversations. Each message costs one point lookup, which is small next to a model call.

**Node execution and replay.** One result is cached per completed node, keyed by node path and iteration, with a hash of the node's input. On resume, the runtime walks the workflow from the start: a node whose key has a completed result and an unchanged input returns the cached output; any other node runs. A node marked `rerun_on_resume` always runs, and if its output changes, every downstream node sees a changed input and runs again. Side-effecting nodes write `started` before calling out and pass the idempotency key.

**`rerun_on_resume`.** Meant for fresh reads before acting (balance, stock, price), permission re-checks and validations that expire. It is not allowed on side-effecting nodes that are not idempotent; the workflow builder rejects that combination at publish.

**Agent nodes** are cached as a whole. A crash in the middle of an agent node re-runs that node from its start, so tools called inside agent nodes must be idempotent.

**Recovery.** For a node left `started`, apply the tool's declared contract:

| Contract | Action |
| --- | --- |
| `idempotent_key_supported` | Retry with the same key |
| `reconcilable` | Query external state, then record it |
| `at_most_once` | Do not retry; suspend with a HITL frame |
| `pure` | Retry freely |

## Interfaces

| Interface | Shape |
| --- | --- |
| Inbound turns | `TurnEnvelope`s from the transport layer; events streamed back through it |
| Approve or reject | A decision envelope carrying the `action_id`, from a UI button or an A2A reply |
| Pending items | `GET /conversations/{id}/pending` |
| Events out | `turn.completed`, `frame.pushed`, `frame.resumed`, `frame.expired` |
| Depends on | Transport for inbound turns and outbound delegation; gateway for every model and tool call; Builder Canvas for definitions; NoSQL DB, SQL DB, object storage |

## Guarantees

- A frame is pushed only when a message interrupts a waiting step; the active slot and the stack always change in one atomic update.
- The runtime never interprets a message; waiting steps decide whether a message is theirs.
- An interrupted workflow is brought back by re-asking its own step, with exponential backoff counted in messages and a maximum number of cards.
- A frame is owned by exactly one conversation at a time; a transfer moves it atomically and happens once.
- A completed node is never re-executed on resume unless it is marked rerun\_on\_resume or its input changed.
- A change in any node's output invalidates the cached results of the nodes downstream of it.
- A side effect is retried only through the tool's idempotency or reconcile contract.
- An approval resumes exactly one frame, exactly once.
- Executions resume on the capability version they started on.

## Limits

One active workflow per conversation; at most 5 frames on the stack; resume cards limited by the urgency's backoff and maximum; at most one cross-conversation pending card per frame per conversation; frame TTL set per capability, with a reminder before expiry; \[Later\] snoozes for a fixed period (for example, 4 hours); periodic snapshots for long executions.
