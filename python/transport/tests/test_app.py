from conftest import events_of, make_client


async def test_first_message_starts_a_conversation_and_the_server_issues_its_id(start):
    client, _, _ = make_client([[{"text": "Hel"}, {"text": "lo"}]])

    conversation_id, response = await start(client, "m1", "Hi")

    assert response.status_code == 201
    assert response.headers["content-type"].startswith("text/event-stream")
    assert len(conversation_id) == 32
    assert response.headers["location"] == f"/conversations/{conversation_id}"
    events = events_of(response)
    assert [(e.cursor, e.type) for e in events] == [(1, "token"), (2, "token"), (3, "turn_done")]
    assert {e.conversation_id for e in events} == {conversation_id}
    assert events[0].data == {"text": "Hel"}


async def test_events_are_server_sent_events_with_the_cursor_as_id(start):
    client, _, _ = make_client([[{"text": "Hi"}]])

    conversation_id, response = await start(client, "m1", "Hi")

    first = response.text.split("\n\n")[0].split("\n")
    assert first[0] == "id: 1"
    assert first[1] == "event: token"
    assert first[2].startswith(f'data: {{"conversation_id":"{conversation_id}","message_id":"m1"')


async def test_each_start_gets_a_different_conversation(start):
    client, _, model = make_client([[{"text": "a"}], [{"text": "b"}]])

    first, _ = await start(client, "m1", "one")
    second, response = await start(client, "m1", "two")

    assert first != second
    assert [e.cursor for e in events_of(response)] == [1, 2]  # its own cursor
    assert [m.content[0].text for m in model.requests[1].messages] == ["two"]  # and its own history


async def test_next_message_continues_the_conversation(start, post):
    client, _, model = make_client([[{"text": "Hi Sam."}], [{"text": "Sam."}]])
    conversation_id, _ = await start(client, "m1", "I am Sam.")

    response = await post(client, conversation_id, "m2", "Who am I?")

    assert response.status_code == 200
    assert [e.cursor for e in events_of(response)] == [3, 4]
    assert [m.content[0].text for m in model.requests[1].messages] == ["I am Sam.", "Hi Sam.", "Who am I?"]


async def test_conversation_id_the_server_did_not_issue_is_rejected(start, post):
    client, _, model = make_client([[{"text": "a"}]])
    await start(client, "m1", "one")

    response = await post(client, "c1", "m2", "two")

    assert response.status_code == 404
    assert "Start one with POST /conversations" in response.json()["detail"]
    assert len(model.requests) == 1  # nothing ran


async def test_a_message_without_a_conversation_id_has_no_route():
    """Continuing needs an ID in the URL; leaving it out cannot be mistaken for starting."""
    client, _, model = make_client([])
    body = {"message_id": "m", "text": "t"}

    assert (await client.post("/conversations//messages", json=body)).status_code == 404
    assert (await client.post("/conversations/messages", json=body)).status_code == 404
    assert model.requests == []


async def test_runtime_mints_one_invocation_per_message(start, post):
    client, _, model = make_client([[{"text": "a"}], [{"text": "b"}]])

    conversation_id, response = await start(client, "m1", "one")
    first = events_of(response)
    second = events_of(await post(client, conversation_id, "m2", "two"))

    assert len({e.invocation_id for e in first}) == 1
    assert first[0].invocation_id and first[0].invocation_id != second[0].invocation_id
    assert {e.invocation_type for e in first + second} == {"agent"}
    # The gateway call is stamped with the same IDs the client sees.
    context = model.requests[0].context
    assert (context.conversation_id, context.message_id) == (conversation_id, "m1")
    assert (context.invocation_id, context.invocation_type) == (first[0].invocation_id, "agent")
    assert (context.tenant, context.user_id) == ("dev", "dev-user")


async def test_repeated_message_id_is_rejected(start, post):
    client, _, model = make_client([[{"text": "a"}]])
    conversation_id, _ = await start(client, "m1", "one")

    repeat = await post(client, conversation_id, "m1", "one")

    assert repeat.status_code == 409
    assert len(model.requests) == 1


async def test_invalid_body_is_rejected(start, post):
    client, _, model = make_client([[{"text": "a"}]])

    assert (await client.post("/conversations", json={"text": "no id"})).status_code == 422
    assert (await client.post("/conversations", json={"message_id": "m1", "text": ""})).status_code == 422
    conversation_id, _ = await start(client, "m1", "one")
    assert (await post(client, conversation_id, "m2", "")).status_code == 422
    assert len(model.requests) == 1


async def test_missed_events_can_be_replayed_from_a_cursor(start):
    client, _, _ = make_client([[{"text": "a"}, {"text": "b"}]])
    conversation_id, _ = await start(client, "m1", "one")

    replay = await client.get(f"/conversations/{conversation_id}/events", headers={"Last-Event-ID": "1"})
    everything = await client.get(f"/conversations/{conversation_id}/events")
    unknown = await client.get("/conversations/c1/events")

    assert [(e.cursor, e.type) for e in events_of(replay)] == [(2, "token"), (3, "turn_done")]
    assert len(events_of(everything)) == 3
    assert unknown.status_code == 404


async def test_health():
    client, _, _ = make_client([])

    assert (await client.get("/healthz")).json() == {
        "status": "ok",
        "agent": "test-agent",
        "version": "1.0.0",
    }


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
    runtime, _ = scripted_runtime(agent, [[{"text": "pong"}], [{"text": "pong again"}]])
    server = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=0, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            pass
        base = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"

        first = httpx.post(f"{base}/conversations", json={"message_id": "m1", "text": "ping"})
        conversation_id = first.headers["x-conversation-id"]
        second = httpx.post(
            f"{base}/conversations/{conversation_id}/messages", json={"message_id": "m2", "text": "ping"}
        )

        assert first.status_code == 201 and second.status_code == 200
        assert [(e.type, e.data) for e in events_of(first)] == [
            ("token", {"text": "pong"}),
            ("turn_done", {"stop_reason": "end_turn"}),
        ]
        assert events_of(second)[0].data == {"text": "pong again"}
    finally:
        server.should_exit = True
        thread.join(timeout=5)
