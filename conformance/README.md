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
  "requests": [            // sent in order to POST /conversations/{conversation_id}/messages
    {
      "conversation_id": "c1",
      "body": { "message_id": "m1", "text": "…" },
      "expect": {
        "status": 200,
        "events": [ { "type": "token", "data": { "text": "…" } } ]
      }
    }
  ]
}
```

An implementation must check, for every 200 response:

- the events' `type` and `data` equal the expected list, in order (`trace_id` in `turn_done` is ignored, and for an `error` event only the `type` is compared, since its message text is implementation-specific);
- each event's SSE `id` equals its `cursor`;
- `cursor` starts at 1 for a conversation and increases by 1 with every event, across requests;
- `conversation_id` and `message_id` match the request, and `invocation_id` is the same non-empty value on every event of one request.
