# 5. Governance and security

## Responsibility

Decides who may do what, and enforces it where agents cannot bypass it: at ingress, at the platform gateway, and in credential issuance. Applies guardrails, budgets and audit. All policy comes from the Builder Canvas; this subsystem enforces it.

**Out of scope:** deciding policy (Builder Canvas), agent-specific business rules (builder callbacks).

## Components

| Component | Job |
| --- | --- |
| Identity provider | OIDC sign-in, session and refresh tokens; user, agent and workload identities |
| Runtime ingress | Checks the user may start the agent; binds session to tenant |
| Model gateway | Allowed models, token budgets, rate limits, guardrails on inputs and outputs, cost attribution, provider fallback; provider adapters that expose one internal message and tool-call format to the Agent SDK |
| Tool and MCP gateway | Tool authorization per agent and scope; invocation-origin rule; idempotency-key forwarding; guardrails on tool results |
| Delegation policy | Authorizes delegation and computes the permission intersection; enforced on every A2A call by the transport layer |
| Guardrail engine | PII, secrets, toxicity and prompt-injection checks, run by the gateways per policy |
| Credential service | Agent identity: short-lived, down-scoped tokens per call through token exchange, client credentials and connector grants (see Identity provider) |
| Policy cache | Entitlements, guardrail profiles and budgets synced from the Builder Canvas |
| Audit writer | Writes accountable decisions to the audit log |

The gateway is built in-platform; it is a core component, not a thin proxy.

## Data model

| Object | Fields |
| --- | --- |
| Call identity | `tenant`, user, calling agent and version, `execution_id`, delegation chain |
| Execution token | Signed by the runtime: `execution_id`, node type, `decision_id`, expiry; required for financial and irreversible tools |
| Guardrail profile | Named set of checks with actions (pass, redact, block) per tenant, team or user |
| Budget | Scope (user, team, tenant), limit, period, current spend |
| Audit entry | Actor, action, target, decision, time, `trace_id`, previous-entry hash |

## Guardrail layers

| Guardrail | Enforced at | Who can change it |
| --- | --- | --- |
| Compliance: PII, secrets, toxicity, prompt injection | Gateway | Tenant admin only |
| Model and tool access, budgets | Gateway | Tenant admin |
| Agent-specific: topic scope, business rules, output format | `before_agent` / `after_agent` callbacks | Agent builder, can enable or disable |
| Irreversible actions only from deterministic nodes with a decision record | Gateway | Nobody; platform policy |

## Key flows

**Turn ingress.** Validate token → check entitlement for the agent → bind tenant and session → start trace.

**Model call.** Runtime → model gateway: authorize model for scope → check budget → input guardrails → provider → output guardrails → meter cost.

**Financial tool call.** Workflow node → tool gateway: verify execution token (function node, decision record present) → authorize tool → issue down-scoped credential → forward idempotency key → scan result → audit.

**Delegation.** Caller → delegation policy, enforced by the transport A2A client: check caller may delegate to callee → mint token with the intersection of permissions → propagate identity and trace.

## Threats and defenses

| Threat | Defense |
| --- | --- |
| Prompt injection via tool results or documents | Guardrails scan tool messages; content marked as data; write tools behind HITL |
| Injected instructions resurfacing through conversation summaries or icebreaker questions | Summaries stored as data, never as instructions; icebreaker question text passes output guardrails; sensitive conversations excluded |
| Confused deputy through delegation | Permission intersection; down-scoped credentials; scope from identity |
| Exfiltration through tool arguments | Egress allowlists per tool; argument guardrails on write tools |
| Malicious skill scripts | Security review; sandboxed execution |
| Gateway compromise | Isolated, highly available deployment; credentials in a secret store; audited admin changes |

## Model gateway

Admins register models; builders use them from their projects. Every model call is authenticated with a platform identity issued by the identity provider, never with a provider key held by the agent.

| Aspect | Design |
| --- | --- |
| Model registry | Tenant admins register models: provider, endpoint, provider credentials (stored in the vault), price per token, capabilities (tool calling, structured output, context length, modalities), and which environments may use them |
| Hierarchy | Org → team → project → environment (dev, prod). Builders create projects and environments within their team's allocation |
| Environments | Dev: small budget, prototyping models (for example hosted GPT and Anthropic models). Prod: larger budget, only models approved for production. Deployed agents bill to their project's prod environment |
| Budgets | Enforced in currency, shown in tokens and calls. Reserve an estimate before each call (prompt tokens plus `max_tokens` × price), settle the real cost after. Alert at 80%, stop at 100%; builders request increases from their team admin |
| Views | Same pages for everyone, filtered by role: admins manage models, org and team budgets; builders manage their projects |
| Authentication | The agent's token from the identity provider carries tenant, project and environment; the gateway authorizes the model and charges the right budget from it |
| Bring your own key | Allowed per project if admins permit: the builder stores the provider key as a secret reference in the vault, and the gateway uses it for that project's calls |
| Provider adapters | One adapter per provider, exposing one internal message and tool-call format to the Agent SDK |

## Tool gateway

The proxy in front of every MCP server. Agents never call MCP servers directly; to the agent, the gateway looks like one MCP server exposing the tools it may use.

| Job | How |
| --- | --- |
| Authorize | The agent may call this tool, and the user may use this action, from Builder Canvas entitlements |
| Attach identity | Attaches the token the agent's identity configuration calls for: the user-scoped exchanged token, a connector token, or the agent's own identity. MCP servers never see secrets |
| Enforce platform rules | Invocation-origin rule for financial and irreversible tools; idempotency-key forwarding |
| Guard | Calls the guardrails service on arguments and results |
| Meter and audit | Rate limits, budgets for paid tools, traces and audit entries |

## Guardrails service

Guardrails are a configurable service, called by the model gateway, the tool gateway and the agent-level callbacks, and authenticated with platform identities like everything else.

| Aspect | Design |
| --- | --- |
| Guardrail providers | Registered by admins, like models: built-in checks (PII, secrets, toxicity, prompt injection), open-source engines, cloud guardrail services, or a custom endpoint |
| Checks | Each check declares where it runs (model input, model output, tool arguments, tool results, user input, agent output), its action (pass, redact, block, require review) and whether it fails open or closed |
| Profiles | Named sets of checks, attached at tenant, team, project or agent level. Lower levels can add checks but never remove a compliance check set above them |
| Builder configuration | In the agent and workflow builders, builders pick a profile and add agent-specific checks (topic scope, business rules, output format) |
| Performance | Checks in one profile run in parallel; each has a latency budget; results are cached per content hash where safe |
| Audit | Every block and redaction is recorded on the trace; policy changes go to the audit log |

## Configuration and secrets in the builders

Builders define configuration for agents, workflows and function tools per environment.

| Kind | Example | Stored | Visible to |
| --- | --- | --- | --- |
| Variables | `TICKET_QUEUE=support-l2`, `REGION=apac` | Definition, per environment | Builders; the running code |
| Secret references | `SALESFORCE_API_KEY=secret://proj_support/salesforce_key` | Value in the vault; only the reference in the definition | Nobody after entry; resolved at call time by the credential service |

Secrets are never plain environment variables. A reference resolves only inside the gateway, or inside a reviewed function tool that declared it. Generated code in the sandbox never receives secrets. Model, tool and guardrail access never needs a secret at all: the identity provider authenticates the agent to all three.

## Identity provider

Agent identity is not a new protocol: it is a profile of existing OAuth, OpenID and workload-identity standards, the same pattern identity providers use for on-behalf-of access. The platform chooses which flow applies in which situation and which claims the gateways read. Every call carries a short-lived token that says whose authority is used. By default a user-facing agent acts with the user's own authority, narrowed to the agent's scopes, so the data source enforces permissions and the agent can never do more than the user. Agents never hold secrets or refresh tokens, and the model itself never sees a token.

**Provider registry (governance).** Tenant admins register every identity and authorization provider once, at the governance layer. Builders can only pick from what is registered.

| Registered provider | Examples | Registered by | Holds |
| --- | --- | --- | --- |
| Platform identity provider | The platform's own issuer | Platform team | Signing keys, token policies (lifetimes, DPoP), supported flows |
| Federated sign-in | Okta, Microsoft Entra ID, Google Workspace | Tenant admin | OIDC or SAML metadata, email domains, group-to-role mapping, SCIM endpoint |
| Connector providers | Google Drive, Salesforce, Slack | Tenant admin | OAuth client for the connector, allowed scopes, consent text |
| Customer authorization servers | A customer's own API gateway | Tenant admin | Issuer, audience, trust method (JWT bearer assertion or identity assertion grant), allowed scopes |
| Data APIs | The platform data API in front of a database | Tenant admin | Audience, scopes, how the verified identity reaches row-level security |

Client secrets and keys are entered once into the vault through this registry and are never shown again, to admins or builders.

**Configuration in the builders.** The agent and workflow builders have an identity step; choices are stored in the definition and approved with the rest of the enablement.

| Setting | Choices |
| --- | --- |
| Mode | User-facing (acts for the signed-in user) or background |
| Run as (background) | As itself, or as a user through a delegation grant with an expiry |
| Providers and connectors | Picked from the registry; each lists the scopes the agent requests |
| Scopes | Requested per provider; never more than the registry allows; new scopes require re-consent |
| Owner and approvers | Accountable owner and team; approvers for HITL and CIBA requests |
| Legacy fallback | Allowed only for data APIs registered as service-account-only |

```yaml
identity:
  mode: user_facing
  providers:
    - ref: data_api/tickets
      scopes: [tickets:read, tickets:write]
    - ref: connector/google_drive
      scopes: [drive.readonly]
  owner: user:U7
  approvers: [team:support_leads]
```

**Identities**

| Identity | What it is | How it authenticates |
| --- | --- | --- |
| User | A person in a tenant | Authorization code with PKCE at the identity provider; session and refresh tokens held by the client |
| Agent | A deployed agent or workflow, per tenant and environment | Registered as an OAuth client when published; authenticates with its workload identity, never a static secret |
| Workload | The pod or worker running the agent | Kubernetes service-account token or SPIFFE ID, issued by the platform |
| Connector grant | A user's consent for an agent to use a third-party system (Google Drive, Salesforce) | Authorization code run once by the platform; the refresh token is stored only in the vault |

**Flows**

| Situation | Flow | Token the call carries |
| --- | --- | --- |
| **Default:** user-facing agent calls a platform API or data source | OAuth 2.0 Token Exchange (RFC 8693) | `sub` = user, `act` = agent, audience = that one API, scopes = the user's ∩ the agent's declared scopes, lifetime minutes |
| Agent acts for a user on a third-party system | Connector grant: the credential service uses the stored refresh token | A provider access token, handed to the tool gateway, never to the agent |
| Background agent acting for a user who is offline | Stored delegation in the vault, or a CIBA approval request | User-scoped, as above; CIBA decisions are bound to the frame's `action_id` |
| Agent acts as itself (no user: scheduled jobs, system events) | Client credentials, authenticated by a signed JWT assertion from the workload identity (RFC 7523) | `sub` = agent; no refresh token, a new token is requested on expiry |
| Legacy system that only supports a service account | Client credentials plus a platform-signed `on_behalf_of` claim | `sub` = agent; user rules enforced at the tool gateway |
| Agent delegates to another agent (A2A) | Token exchange at each hop | Nested `act` claims recording the chain; scope only narrows |

**Data sources enforce per user.** APIs accept the exchanged token. Databases sit behind a data API that verifies the token, takes a pooled connection, sets the verified identity for the transaction (for example `SET LOCAL app.user_id`), and lets row-level security policies decide what the user may read or write. Users live in the identity provider, not as database logins.

**What parity does not cover.** The agent can still do anything the user could, so a prompt injection could misuse the user's own rights. Agent scopes stay narrow, and write actions stay behind HITL.

**Standards used**

| Need | Standard |
| --- | --- |
| User sign-in | OAuth 2.0 authorization code with PKCE; OpenID Connect |
| Acting for a user; delegation chains | OAuth 2.0 Token Exchange (RFC 8693), `act` claim |
| Agent authentication without a static secret | JWT client assertions (RFC 7523); SPIFFE/SPIRE or Kubernetes service-account workload identity |
| Token bound to one API | Resource Indicators (RFC 8707) |
| Access-token format | JWT Profile for OAuth 2.0 Access Tokens (RFC 9068) |
| Replay protection | DPoP (RFC 9449) |
| Asynchronous user approval | OpenID CIBA |
| Pushing revocations | OpenID Shared Signals (CAEP) |
| Tools and remote agents | MCP authorization (OAuth-based); A2A agent-card auth schemes |

Agent-specific profiles are still emerging, for example the IETF Agent Authorization Profile draft. Custom claims are named so they can be mapped to such a profile later.

**How the agent uses its identity.** The model never handles credentials. The runtime attaches the right token to every outgoing call:

| What the agent reaches | Token | Enforced by |
| --- | --- | --- |
| Models | Agent identity with project and environment | Model gateway: allowed models, budgets |
| Tools (MCP) and data APIs | User-scoped exchanged token, or agent identity for background agents | Tool gateway, then the API or database |
| Icebreaker questions | User-scoped token; scope derived from it | Icebreaker service |
| Knowledge and retrieval | User-scoped token, so results respect document permissions | Retrieval tool and its index filters |
| Other agents (A2A) | Exchanged token with the `act` chain | Delegation policy, then the remote agent |
| Code sandbox | None inside the sandbox; calls out only through the tool gateway | Sandbox network policy (see Code execution sandbox) |
| Files and artifacts | User-scoped token | Object storage policies, per tenant |

**Background agents and workflows** have no user in the loop, so they use a separate identity class with explicit ownership and explicit run-as rules.

| Aspect | Design |
| --- | --- |
| Service identity | One per background agent or workflow, per tenant and environment, created at publish and bound to its workload identity. Distinct from the identity the same agent has when user-facing |
| Accountable owner | Every service identity names a human owner and an owning team. An agent whose owner leaves is flagged and keeps running only until a new owner is assigned or the team admin approves |
| Run-as mode | Declared at publish and approved at enablement: **as itself** (its own approved scopes, for system work such as triage of all tickets), or **as a user** (a delegation grant from the user who set it up, for work on that user's data) |
| Delegation grant | Stored in the vault with explicit scopes and an expiry; revocable by the user; re-checked on every run, so a disabled user or a removed scope stops the run |
| Trigger identity | The producer of the triggering event is recorded in the run's token (`trigger.topic`, `trigger.event_id`, `trigger.producer`) for audit. It never adds permissions |
| Event bus authentication | Producers and consumers authenticate to Kafka with their workload identities (mTLS or SASL OAUTHBEARER); topic ACLs decide who may publish which events. The runtime only accepts events from topics the agent subscribes to |
| Per-run token | Client credentials with the workload's JWT assertion; `sub` = the agent's service identity, plus `run_as` (or `act` for a user delegation), `trigger` and `execution_id` |
| Approvals | Frames notify the owner or a named approver; decisions arrive by CIBA or as decision events, bound to `action_id` |

**Federated login.** Users sign in with their own organization's identity provider; the platform's identity provider brokers that login and remains the only issuer of platform tokens.

| Aspect | Design |
| --- | --- |
| Federation | Per-tenant connection to the customer's identity provider over OpenID Connect or SAML (for example Okta, Microsoft Entra ID, Google Workspace). The platform IdP acts as a broker |
| Finding the right IdP | Home-realm discovery by email domain, or a tenant-specific sign-in URL |
| Provisioning | Just-in-time account creation on first sign-in; SCIM for ongoing create, update and deprovision |
| Roles | The customer's groups map to platform roles (tenant admin, builder, user) through a per-tenant mapping |
| Tokens | The upstream IdP's tokens are used only to sign the user in. Everything downstream uses platform-issued tokens, so agents and gateways see one token format whatever the customer's IdP |
| Offboarding | A SCIM deprovision or a Shared Signals (CAEP) event from the customer disables the user, ends sessions and stops all delegation grants and token exchanges for them |
| Customer-owned APIs | When a customer's API trusts only their own IdP, the platform obtains a token there with a JWT bearer assertion (RFC 7523) that their authorization server is configured to trust, or with the emerging Identity Assertion Authorization Grant (cross-app access) where supported |

**Token claims**

```json
{
  "iss": "https://id.platform/tenant_42",
  "sub": "user:U1",
  "act": { "sub": "agent:customer_support@2.3.0",
           "act": { "sub": "agent:billing_specialist@2.1.0" } },
  "aud": "tool:create_ticket",
  "scope": "tickets:write",
  "tenant": "tenant_42",
  "project": "proj_support",
  "env": "prod",
  "execution_id": "exec_8f2",
  "exp": 1791640000
}
```

**Components**

| Component | Job |
| --- | --- |
| Identity provider | Signs users in; issues all tokens; implements token exchange, client credentials with JWT assertions, and CIBA |
| Agent registry (in the identity provider) | One OAuth client per agent, tenant and environment, created when the Builder Canvas publishes the agent, disabled when it is revoked |
| Credential vault | Holds connector refresh tokens and per-tenant encryption keys; nothing else can read them |
| Credential service | Runs every exchange on the agent's behalf and returns tokens only to the gateways |
| Connector consent | The UI and flow where a user grants or revokes an agent's access to a third-party system, with the exact scopes shown |

**Lifecycle**

- **Publish:** registering the agent creates its OAuth client, bound to its workload identity, with the scopes approved at enablement. A builder never sees or types a secret.
- **Use:** each call gets a fresh token for one audience; nothing is cached by the agent.
- **Rotation:** workload identities rotate automatically; there is no shared secret to rotate.
- **Revocation:** revoking the agent disables its client, so new exchanges fail immediately and existing tokens expire within minutes. Revoking a connector grant deletes the refresh token from the vault.
- **User leaves:** disabling the user stops every exchange for them, including background agents acting under their connector grants.

**Rules**

- Refresh tokens exist only for connector grants, and only in the vault.
- Every agent token is audience-restricted to one tool, agent or model endpoint, and lives for minutes.
- A delegated token never carries more scope than any identity earlier in its `act` chain.
- Gateways accept only platform-issued tokens and check audience, scope, expiry and the `act` chain on every call.
- Every exchange is logged with the full `act` chain; consents and revocations go to the audit log.

## Interfaces

| Interface | Shape |
| --- | --- |
| Gateway endpoints | Model calls, MCP (per server and federated) |
| Required headers | Identity token, execution token where needed, `traceparent`, idempotency key |
| Events in | `entitlement.changed` from the Builder Canvas |
| Depends on | Builder Canvas for policy; identity provider; audit log |

## Guarantees

- No model, tool or A2A call reaches a backend without authorization and the scope's guardrail policy.
- Delegated calls never exceed the caller's permissions.
- Financial and irreversible tools run only from deterministic workflow nodes with an approval on record.
- Builders cannot disable compliance guardrails.
- Every approval, policy change and sensitive access is in the audit log.
