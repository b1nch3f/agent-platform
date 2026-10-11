"""agent-platform: run an agent locally in the terminal, or serve it over HTTP + SSE.

agent-platform chat  --agent agent.yaml    the agent runs in this process; you talk to it directly
agent-platform serve --agent agent.yaml    the agent runs as a server; use curl, Postman or a frontend
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterator
from typing import Protocol, TextIO

from ap_contracts import OutboundEvent
from ap_sdk import Runtime

# Fields every LogRecord has; anything else was passed as `extra` and is part of the log line.
_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "color_message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **{k: v for k, v in record.__dict__.items() if k not in _STANDARD},
        }
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(handler: logging.Handler) -> None:
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_runtime(agent_path: str, models_path: str) -> Runtime:
    """Wire an agent to the registered models. Both commands run the agent through this."""
    from dotenv import find_dotenv, load_dotenv

    from ap_gateway import ModelGateway, ModelRegistry, ToolGateway
    from ap_sdk import InMemoryConversationStore, load_agent

    # Nearest .env from the current directory upwards. It wins over variables set in the shell.
    load_dotenv(find_dotenv(usecwd=True), override=True)

    return Runtime(
        agent=load_agent(agent_path),
        model_gateway=ModelGateway(ModelRegistry.from_file(models_path)),
        tool_gateway=ToolGateway(),
        store=InMemoryConversationStore(),
    )


def serve(args: argparse.Namespace) -> None:
    import uvicorn

    from ap_transport.app import create_app

    configure_logging(logging.StreamHandler())
    uvicorn.run(
        create_app(build_runtime(args.agent, args.models)), host=args.host, port=args.port, log_config=None
    )


class ChatClient(Protocol):
    def start(self, text: str) -> tuple[str, Iterator[OutboundEvent]]: ...
    def send(self, conversation_id: str, text: str) -> Iterator[OutboundEvent]: ...


def run_chat(client: ChatClient, *, read: Callable[[str], str] = input, out: TextIO = sys.stdout) -> None:
    """The terminal conversation loop: read a line, run the turn, print events as they arrive."""

    def show(text: str) -> None:
        out.write(text)
        out.flush()

    conversation_id: str | None = None  # issued with the first message
    while True:
        try:
            text = read("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            show("\n")
            return
        if not text:
            continue
        show("agent> ")
        after_note = False  # the last thing printed was a bracketed note on its own line
        if conversation_id is None:
            conversation_id, events = client.start(text)
        else:
            events = client.send(conversation_id, text)
        for event in events:
            if event.type == "token":
                show(("\n" if after_note else "") + event.data["text"])
                after_note = False
                continue
            if event.type == "tool_status":
                show(f"\n  [{event.data['tool']}: {event.data['status']}]")
            elif event.type == "error":
                show(f"\n  [error: {event.data['message']}]")
            elif event.type == "turn_done" and event.data.get("trace_id"):
                show(f"\n  [trace {event.data['trace_id']}]")
            else:
                continue
            after_note = True
        show("\n")


def chat(args: argparse.Namespace) -> None:
    from ap_transport.local import LocalClient

    # Logs would land in the middle of the conversation, so they go to a file or nowhere.
    configure_logging(logging.FileHandler(args.log_file) if args.log_file else logging.NullHandler())
    runtime = build_runtime(args.agent, args.models)
    agent = runtime.root_agent
    print(f"{agent.name} {agent.version} ({agent.definition.model}), running locally. Ctrl-D to quit.")
    client = LocalClient(runtime)
    try:
        run_chat(client)
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-platform", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(required=True)

    chat_parser = commands.add_parser("chat", help="Run an agent in this process and talk to it")
    chat_parser.add_argument("--agent", required=True, help="Path to the agent definition (YAML)")
    chat_parser.add_argument("--log-file", help="Write JSON logs to this file (default: no logs)")
    chat_parser.set_defaults(run=chat)

    serve_parser = commands.add_parser("serve", help="Serve an agent over HTTP + SSE")
    serve_parser.add_argument("--agent", required=True, help="Path to the agent definition (YAML)")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(run=serve)

    for command in (chat_parser, serve_parser):
        command.add_argument(
            "--models", default="models.yaml", help="Model registry file (default: models.yaml here)"
        )

    args = parser.parse_args()
    args.run(args)
