# Conformance cases

Language-neutral test cases. Every implementation of a component (Python now, Java later) loads
the same files and must produce the same result, which is what keeps the implementations compatible.

## transport/

Each file drives the HTTP + SSE transport with a scripted model and states the exact events expected.

```json
{
  "name": "…",
  "description": "…",
  "agent": { },            // an agent definition (contracts/agent-definition.schema.json), without tool functions
  "tools": [               // stub tools: each call returns "result", or fails with "error"
    { "name": "…", "description": "…", "input_schema": { }, "result": "…" }
  ],
  "model_script": [ [ ] ], // one list of steps per model call: {"text"}, {"tool_call"}, {"stop_reason"}, {"error"}
  "requests": [            // sent in order
    {
      "conversation": "A",   // a label, not an ID: see below
      "body": { "message_id": "m1", "text": "…" },
      "expect": {
        "status": 201,
        "events": [ { "type": "token", "data": { "text": "…" } } ]
      }
    }
  ]
}
```

Which call a request makes:

- `"conversation": "<label>"`, first request with that label: start a conversation, `POST /conversations`. The implementation remembers the conversation ID the transport returns under that label. Expected status `201`.
- `"conversation": "<label>"`, later requests: continue it, `POST /conversations/{id}/messages` with the remembered ID. Expected status `200`, or `409` for a repeated `message_id`.
- `"conversation_id": "<literal>"`: `POST /conversations/{literal}/messages` with that exact ID, to show that an ID the transport did not issue is rejected with `404`.

An implementation must check, for every `201` and `200` response:

- the events' `type` and `data` equal the expected list, in order (`trace_id` in `turn_done` is ignored, and for an `error` event only the `type` is compared, since its message text is implementation-specific);
- each event's SSE `id` equals its `cursor`;
- `cursor` starts at 1 for a conversation and increases by 1 with every event, across requests;
- `conversation_id` on every event is the ID the transport issued for that label, and on a `201` it also equals the `X-Conversation-Id` response header;
- `message_id` matches the request;
- `invocation_id` is the same non-empty value on every event of one request, differs between requests, and `invocation_type` is `agent`.

A transport with no HTTP layer (the in-process client) maps the statuses to its own outcomes: `404` is its unknown-conversation error and `409` its duplicate-message error.
