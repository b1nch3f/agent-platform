# 7. Knowledge

## Responsibility

Lets agents answer from company documents without widening anyone's access. It ingests documents from registered sources, keeps them in sync, indexes them with each document's access list, and serves retrieval to agents as a platform tool through the tool gateway. Every query is filtered to what the calling user, or a background agent's own identity, may read at the source.

**Out of scope:** conversation history (context), long-term facts about the user (not provided), composing answers (the agent), deciding source permissions (the source stays authoritative; Knowledge mirrors them).

## Components

| Component | Job |
| --- | --- |
| Source connectors | Pull documents and their permissions from registered sources (wikis, drives, ticketing, databases, uploads); incremental sync by webhook or schedule; delete what the source deleted |
| Ingestion pipeline | Parse, split into chunks and embed with a registered embedding model through the model gateway; keep the raw document in object storage |
| Index | Hybrid (vector and keyword) index of chunks, each with tenant, source, document version and access list |
| Retrieval service | Query rewrite, hybrid search, permission filter, rerank; returns chunks with citations; exposed as the `search_knowledge` tool |
| Permission sync | Keeps each chunk's access list current from source ACLs and SCIM group memberships |

## Data model

```python
class KnowledgeSource(BaseModel):
    source_id: str
    tenant: str
    kind: Literal["wiki", "drive", "ticketing", "database", "upload"]
    connection: str                     # secret reference in the vault
    sync: Literal["webhook", "schedule"]
    schedule: str | None
    owners: list[str]

class Chunk(BaseModel):
    chunk_id: str
    tenant: str
    source_id: str
    document_id: str
    document_version: str
    text: str
    embedding_model: str
    allowed_principals: list[str]       # users and groups from the source ACL
    uri: str                            # citation link to the source document
    updated_at: datetime
```

Builders attach sources in the agent builder; entitlements decide which sources an agent may use.

```yaml
knowledge:
  sources: [hr_policies, eng_wiki]
  top_k: 8
  citations: required
```

## Key flows

**Ingest.** A connector sees a change → fetches the document and its ACL → parse and chunk → embed through the model gateway → write the new version's chunks, then remove the old version's.

**Retrieve.** The agent calls `search_knowledge` → the tool gateway checks the agent's entitlement to each source and attaches the caller's identity → retrieval filters by `allowed_principals` before ranking → chunks return with citations, and output guardrails apply as to any tool result. Retrieved text is data, never instructions.

**Permission change.** A user leaves a group → permission sync updates the affected chunks → the next query no longer returns them.

**Delete.** A document deleted at the source, or erased on request, loses its chunks and raw copy at the next sync.

**Re-embed.** A new embedding model builds a new index alongside the old one; retrieval switches when it is complete.

## Interfaces

| Interface | Shape |
| --- | --- |
| Agent tool | `search_knowledge(query, sources?, top_k?)`, through the tool gateway |
| Sources | `POST /knowledge/sources`; `POST /knowledge/sources/{id}/sync` |
| Uploads | `POST /knowledge/sources/{id}/documents` |
| Events emitted | `document.indexed`, `document.deleted` → observability |

## Guarantees

- Retrieval never returns a chunk the caller could not open at the source, as of the last permission sync.
- Every returned chunk carries a citation to its source document.
- A document deleted at the source leaves the index at the next sync.
- Connector credentials are vault references; agents never see them.
