# 11. Open questions

| Subsystem | Question |
| --- | --- |
| Runtime | Default frame TTLs; what a handoff does to the caller's pending frames; snapshot interval; how long old versions stay deployable; summarization defaults |
| Context | Retention defaults per tenant; whether to add an explicit, user-stated preferences list later |
| Governance | Guardrail classifiers: in-house or third-party; egress allowlist ownership; v1 gateway scope |
| Builder Canvas | Tiered routing thresholds; per-tenant evaluation budget |
| Deployments | Default guard metrics and rollback thresholds; minimum eval suite per risk tier; A/B assignment by user or by conversation |
| Knowledge | First source connectors; permission-sync lag target; vector index choice (in the SQL DB or a dedicated engine); chunking defaults |
| Observability | Trace store choice; content-capture defaults; friction survey design; SLO targets |
| Transport | Replay buffer length; which authentication schemes to accept from remote A2A agents; whether WebSocket and A2A share one deployment |
| Features | Conversation-idle threshold for icebreaker refresh; recommendation model size; which features come after Icebreaker questions |
