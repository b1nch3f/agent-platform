# 1. Builder Canvas

## Responsibility

The control plane. Anyone in a tenant builds agents, workflows and skills without code and tools with code, browsing and reusing what is already deployed from within each builder, then evaluates and publishes. Tenant admins set policy and approve write and financial capabilities. The Builder Canvas publishes versioned definitions to Deployments and is the single source of truth for entitlements, which it syncs to every enforcement point.

**Out of scope:** enforcement (governance), execution (runtime), cross-tenant sharing.

## Roles

| Role | Can do |
| --- | --- |
| Platform team | Publish verified capabilities for all tenants; run shared services |
| Tenant admin | Allowed models, budgets, guardrail profiles, thresholds; approve write and financial capabilities |
| Any member | Build, evaluate, publish and use capabilities in the tenant |

## Components

| Component | Job |
| --- | --- |
| Agent builder | Builds an agent from instructions, a model, tools (skills and MCP tools), workflows and delegate agents, plus its identity settings chosen from registered providers; sandbox chat with mock tools; agent test suite |
| Workflow builder | Builds a workflow from agent, tool, workflow and custom function nodes plus HITL steps; computes whether it is suspendable; sets reminder urgency (low, normal, high); rejects rerun\_on\_resume on non-idempotent side-effecting nodes; sandbox runs with mock tools |
| Skill builder | For authors of skill packages: lint and trigger-question (routing) tests with publish gates |
| Tool builder | Write code to build function tools; tests, security scan and publish as MCP tools |
| Entitlement service | Grants per tenant, team or user; dependency resolution; consent summary |
| Approval service | Routes approvals by risk tier: none, one admin, or two admins |
| Sync and reconciler | Pushes entitlements to ingress, gateway and runtime; repairs drift |

Mock tools are part of the agent and workflow builders' sandbox: generated from tool schemas, from fixtures, or from recorded responses, with failure injection. Write and financial tools never run for real in a sandbox.

## Browsing deployed capabilities

There is no separate store. Every builder lets you browse what is already deployed, so you can inspect and reuse it while building. Skills and MCP tools are both shown as tools, with a badge for their type. Selecting an item drills into its structure:

| Select | Shows |
| --- | --- |
| An agent | Whether it is single-agent or multi-agent. A multi-agent shows its member agents and how each is reached (invoke or handoff) |
| Any agent inside it | Its tools (skills and MCP tools), workflows and delegates, with permissions and risk tier |
| A tool | Type (skill: instructions loaded into the agent; MCP tool: an executable operation), inputs and outputs, permissions |
| A workflow | Its nodes as a graph; each node is an agent, a tool, another workflow or a function; HITL steps are marked |
| A node | Drills into that agent, tool, workflow or function |

## Composition

| Create | Built from |
| --- | --- |
| Agent | Instructions, model, tools (skills and MCP tools), workflows, and other agents as delegates |
| Workflow | Nodes (agents, tools, workflows, custom functions) connected by edges, plus HITL steps |

Custom function nodes are written in the workflow builder's code editor and run sandboxed. Each declares input and output schemas and a recovery contract (pure, idempotent key, reconcilable or at-most-once), because the runtime needs it to recover safely.

## Tool builder

The tool builder completes what can be built: agents, workflows and skills without code, and tools with code. A tool written here is published as an MCP tool, so any agent can use it and any workflow can use it as a tool node.

| Aspect | Design |
| --- | --- |
| Authoring | Code editor in the canvas; one function per tool, with typed input and output schemas generated from its signature |
| Contract | Declares risk tier (read-only, write, financial), recovery contract (pure, idempotent key, reconcilable, at-most-once), required secrets and allowed egress hosts |
| Secrets | Requested by name and injected at call time by the credential service; never written in code |
| Execution | Packaged and served by the platform as an MCP server, sandboxed, with only its declared egress; every call passes the tool gateway |
| Testing | Test cases in the canvas (input and expected output or assertion), run against mocked dependencies, then a sandbox integration run |
| Publish | Lint and security scan, tests pass, approval by risk tier, versioned like every other capability |

A function node is private to its workflow; a tool from the tool builder is published and reusable. A function node can be promoted to a tool.

## HITL placement rule

A workflow that contains a HITL step, directly or in any nested workflow node, is **suspendable**. A suspendable workflow can only be deployed at top level or used as a sub-agent. It can never be used as a tool.

**Why it matters for stack frames.** A tool call runs inside an agent's model loop, and the agent is waiting for its result. If a tool suspended, there would be no boundary the runtime owns: the agent loop would be stuck mid-call, no frame could be pushed for it, and step matching could not route the user's reply back. At top level, or as a sub-agent, the suspension happens at a capability boundary the runtime controls, so it can push a frame and resume it later.

| Where | Check |
| --- | --- |
| Workflow builder | Marks a workflow suspendable automatically; offers "use as tool" only for workflows that are not |
| Publish validation | Rejects any definition that uses a suspendable workflow as a tool, including through nesting |
| Runtime | Rejects a suspend request from an execution running in tool mode and fails that call with a clear error |

A nested workflow node with HITL makes its parent suspendable, so the same rule then applies to the parent.

## Data model

```python
class Entitlement(BaseModel):
    entitlement_id: str
    capability: str            # "agent:customer_support"
    version_policy: str        # "2.3.0" or "2.x"
    scope: Literal["tenant", "team", "user"]
    scope_id: str
    config: dict
    guardrail_policy: str
    granted_permissions: list[str]
    approved_by: list[str]
    status: Literal["pending_approval", "active", "suspended", "revoked"]
```

```yaml
agent:
  name: customer_support
  version: 2.3.0
  kind: multi_agent            # computed: single_agent or multi_agent
  model: <tenant-allowed model>
  tools:                       # skills and MCP tools alike
    - skill/troubleshooting@1.4
    - skill/documentation_guidance@2.0
    - mcp/search_documents@1.2
    - mcp/create_ticket@3.1
  workflows:
    - ref: purchase_approval@1.0
      mode: sub_agent          # suspendable, so never a tool
  delegates:
    - agent: billing_specialist@2.1
      mode: handoff
  summarization: {enabled: true, trigger: {token_threshold: 24000}}
  icebreaker_questions: {enabled: true, max: 3}
  governance_profile: enterprise_standard
  budget: {per_conversation_usd: 0.50, monthly_usd: 2000}
```

```yaml
workflow:
  name: purchase_approval
  version: 1.0.0
  suspendable: true            # computed: contains a HITL step
  usable_as: [top_level, sub_agent]
  urgency: high                # resume-card backoff
  nodes:
    - id: validate
      type: function
      ref: fn/validate_purchase@1.2
      recovery: pure
    - id: check_budget
      type: tool
      ref: mcp/finance.get_budget@2.0
      rerun_on_resume: true    # fresh balance after the approval wait
    - id: confirm
      type: hitl
      prompt: "Purchase {qty} licences for {amount}?"
      irreversible: true
    - id: create_po
      type: function
      ref: fn/create_po@1.0
      recovery: idempotent_key_supported
  edges:
    - validate -> check_budget
    - check_budget -> confirm
    - confirm.approve -> create_po
```

Skill test cases carry a type (positive, negative, near miss, multi-skill), the message, and the expected skill or sibling.

## Key flows

**Build and deploy.** Compose → validate (dependencies approved, model supports needs, routing-overlap lint, write tools only through HITL workflows, no suspendable workflow used as a tool) → sandbox chat with mocks → agent test suite → approval if required → canary → general.

**Enable.** Select capability and scope → resolve dependencies → consent summary → approval by risk tier → entitlement active → sync.

**Revoke.** Entitlement revoked → sync immediately → in-flight executions fail closed at the next gateway check → frames cancelled with notice.

**Version update.** Patch and minor auto-apply under range policies after evaluations pass; major or new permissions require re-consent; model changes re-run agent tests and skill routing tests.

## Skill builder

| Stage | Checks |
| --- | --- |
| Lint (every save) | Frontmatter, description length and quality, referenced files, body size, overlap with sibling skills, instruction-like text, secrets, script safety |
| Routing tests (on demand, at submission, on dependency change) | Trigger recall, false trigger rate, sibling accuracy, stability; 5 samples per case; confusion matrix; version comparison |
| Publish gate | No lint errors; thresholds by skill tier (informational, drives writes, compliance) with minimum case counts |

## Interfaces

| Interface | Shape |
| --- | --- |
| Canvas UI | Agent, workflow, skill and tool builders, each with drill-down into deployed agents, tools and workflow nodes; enable flow; approvals inbox; rollout; access overview; audit log |
| Definitions API | Read definitions by name and version; used by the runtime and the gateway |
| Events out | `entitlement.changed`, `capability.published` |
| Depends on | Runtime (sandbox runs, function nodes), gateway (evaluation model calls), audit log |

## Guarantees

- Enforcement points never diverge from the Builder Canvas for longer than one reconcile cycle.
- Nothing is published without passing its evaluation gate.
- No suspendable workflow is ever deployed or used as a tool.
- A new permission always requires re-consent.
- Rollback switches new conversations immediately; in-flight executions keep their pinned version.
