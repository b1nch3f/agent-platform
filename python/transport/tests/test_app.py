from conftest import events_of, make_client


async def test_turn_is_streamed_as_server_sent_events(post):
    client, _, _ = make_client([[{"text": "Hel"}, {"text": "lo"}]])

    response = await post(client, "c1", "m1", "Hi")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    first = response.text.split("\n\n")[0].split("\n")
    assert first[0] == "id: 1"
    assert first[1] == "event: token"
    assert first[2].startswith('data: {"conversation_id":"c1","message_id":"m1"')
    events = events_of(response)
    assert [(e.cursor, e.type) for e in events] == [(1, "token"), (2, "token"), (3, "turn_done")]
    assert events[0].data == {"text": "Hel"}


async def test_transport_mints_one_invocation_id_per_message(post):
    client, _, model = make_client([[{"text": "a"}], [{"text": "b"}]])

    first = events_of(await post(client, "c1", "m1", "one"))
    second = events_of(await post(client, "c1", "m2", "two"))

    assert len({e.invocation_id for e in first}) == 1
    assert first[0].invocation_id and first[0].invocation_id != second[0].invocation_id
    # The gateway call is stamped with the same IDs the client sees.
    context = model.requests[0].context
    assert (context.conversation_id, context.message_id) == ("c1", "m1")
    assert context.invocation_id == first[0].invocation_id
    assert (context.tenant, context.user_id) == ("dev", "dev-user")


async def test_cursor_is_per_conversation(post):
    client, _, _ = make_client([[{"text": "a"}], [{"text": "b"}], [{"text": "c"}]])

    await post(client, "c1", "m1", "one")
    c1_second = events_of(await post(client, "c1", "m2", "two"))
    c2_first = events_of(await post(client, "c2", "m1", "one"))

    assert [e.cursor for e in c1_second] == [3, 4]
    assert [e.cursor for e in c2_first] == [1, 2]


async def test_repeated_message_id_is_rejected(post):
    client, _, model = make_client([[{"text": "a"}]])

    await post(client, "c1", "m1", "one")
    repeat = await post(client, "c1", "m1", "one")

    assert repeat.status_code == 409
    assert len(model.requests) == 1


async def test_invalid_body_is_rejected(post):
    client, _, model = make_client([])

    assert (await client.post("/conversations/c1/messages", json={"text": "no id"})).status_code == 422
    assert (await post(client, "c1", "m1", "")).status_code == 422
    assert model.requests == []


async def test_missed_events_can_be_replayed_from_a_cursor(post):
    client, _, _ = make_client([[{"text": "a"}, {"text": "b"}]])
    await post(client, "c1", "m1", "one")

    replay = await client.get("/conversations/c1/events", headers={"Last-Event-ID": "1"})
    everything = await client.get("/conversations/c1/events")

    assert [(e.cursor, e.type) for e in events_of(replay)] == [(2, "token"), (3, "turn_done")]
    assert len(events_of(everything)) == 3


async def test_create_conversation_and_health():
    client, _, _ = make_client([])

    created = await client.post("/conversations")
    health = await client.get("/healthz")

    assert created.status_code == 201 and len(created.json()["conversation_id"]) == 32
    assert health.json() == {"status": "ok", "agent": "test-agent", "version": "1.0.0"}


def test_served_over_a_real_socket():
    """The app under uvicorn, reached the way curl or a frontend would reach it."""
    import threading

    import httpx
    import uvicorn

    from ap_contracts import AgentDefinition
    from ap_sdk import Agent
    from ap_sdk.testing import scripted_runtime
    from ap_transport.app import create_app

    agent = Agent(definition=AgentDefinition(name="a", version="1", model="fake", instructions="i"))
    runtime, _ = scripted_runtime(agent, [[{"text": "pong"}]])
    server = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=0, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            pass
        port = server.servers[0].sockets[0].getsockname()[1]

        response = httpx.post(
            f"http://127.0.0.1:{port}/conversations/c1/messages", json={"message_id": "m1", "text": "ping"}
        )

        assert response.headers["content-type"].startswith("text/event-stream")
        assert [(e.type, e.data) for e in events_of(response)] == [
            ("token", {"text": "pong"}),
            ("turn_done", {"stop_reason": "end_turn"}),
        ]
    finally:
        server.should_exit = True
        thread.join(timeout=5)
