# agent-platform

Run from `python/`. Put your model keys in `python/.env` (see `.env.example`).

## Run an agent locally

```
uv run agent-platform chat --agent ../agents/example/agent.yaml
```

## Serve an agent

```
uv run agent-platform serve --agent ../agents/example/agent.yaml
```

Start a conversation with its first message. The server creates the conversation and returns its ID in the `X-Conversation-Id` header (`-i` shows headers) and on every event:

```
curl -N -i -X POST localhost:8000/conversations \
  -H 'content-type: application/json' \
  -d '{"message_id": "m1", "text": "Why is the sky blue?"}'
```

Continue it with that ID. An ID the server did not issue returns 404:

```
curl -N -X POST localhost:8000/conversations/<conversation_id>/messages \
  -H 'content-type: application/json' \
  -d '{"message_id": "m2", "text": "And at sunset?"}'
```

## Test

```
uv run pytest
```
