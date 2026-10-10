# agent-platform

```
uv run agent-platform serve --agent ../agents/example/agent.yaml
```

```
curl -N -X POST localhost:8000/conversations/c1/messages   -H 'content-type: application/json'   -d '{"message_id": "m1", "text": "Why sky is blue?"}'
```
