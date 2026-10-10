# 2. Deployments

## Responsibility

Decides which version of each agent, workflow, skill and tool serves each request, and lets no version reach prod without passing evaluation. It stores the immutable definitions the Builder Canvas publishes, promotes them from dev to prod, runs canary and A/B releases, rolls back, and publishes agent cards so other agents can find what is deployed.

**Out of scope:** building or editing definitions (Builder Canvas), enforcing entitlements (governance), executing (runtime), collecting live signals (observability; Deployments reads them).

## Components

| Component | Job |
| --- | --- |
| Definition storage | Immutable, versioned definitions of every deployed agent, workflow, skill, tool and function node; read by the runtime and the gateway |
| Release manager | Environments (dev, prod), promotion, canary and A/B traffic splits, automatic rollback when a guard metric is breached |
| Evaluation service | Runs eval suites on a candidate version against the live one: agent test suites, skill routing tests, regression sets built from production; blocks promotion when a gate fails |
| Agent discovery | Lists deployed agents with owner, version and capabilities; serves A2A agent cards; resolves delegate and handoff targets to a version |

## Data model

```python
class Release(BaseModel):
    release_id: str
    tenant: str
    artifact: str                       # agent, workflow, skill or tool name
    kind: Literal["agent", "workflow", "skill", "tool"]
    version: str                        # immutable definition version
    environment: Literal["dev", "prod"]
    strategy: Literal["full", "canary", "ab"]
    traffic: dict[str, int]             # version -> percent, sums to 100
    assign_by: Literal["user", "conversation"]
    guard_metrics: list[str]            # error rate, friction score, cost per turn
    eval_run_id: str
    status: Literal["pending_eval", "rolling_out", "live", "rolled_back", "retired"]
    created_by: str

class EvalRun(BaseModel):
    eval_run_id: str
    artifact: str
    candidate_version: str
    baseline_version: str | None
    suites: list[str]
    scores: dict[str, float]
    passed: bool
```

Releases and eval runs are stored in the SQL DB; definitions in the SQL DB, with large attachments in object storage.

## Key flows

**Publish and release.** The Builder Canvas publishes a version → definition storage keeps it immutably → the evaluation service runs the suites against the candidate and the live version → on pass, the release manager rolls it out in dev, then in prod after promotion; on fail, it stays unreleased and the builder sees the failing cases.

**Canary and A/B.** The release manager pushes a traffic split to the transport, which assigns each new conversation (or user) to a version and stamps it on the `TurnEnvelope`. A conversation keeps its version until it ends, and a running execution finishes on the version it started with, so frames and the node cache never mix versions. Guard metrics are compared per version; a breach rolls back automatically.

**Rollback.** Traffic returns to the previous version for new conversations at the next config sync; running executions are not moved.

**Regression sets.** Episodes flagged by friction scoring and turns users marked as wrong become candidate eval cases. A builder reviews them before they join a suite, and content is used only where tenant capture policy allows it.

**Discovery.** On a prod release, agent discovery regenerates the agent's A2A card. Delegate and handoff references resolve to the live version unless the definition pins one.

## Interfaces

| Interface | Shape |
| --- | --- |
| Publish version | `POST /definitions/{artifact}/versions`, from the Builder Canvas |
| Release | `POST /releases` with strategy and traffic; `POST /releases/{id}/promote`; `POST /releases/{id}/rollback` |
| Evaluation | `POST /eval-runs`; `GET /eval-runs/{id}` |
| Discovery | A2A agent card per prod agent; `GET /agents?capability=` for internal lookups |
| Events emitted | `version.released`, `version.rolled_back` → transport, runtime, observability |

## Guarantees

- A version identifier always means the same definition.
- No version reaches prod without a passing evaluation run.
- A conversation and a running execution stay on one version.
- A rollback applies to new conversations within one config sync.
- Agent cards list only agents released to prod and visible to the caller's tenant.
