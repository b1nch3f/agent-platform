"""agent-platform: serve an agent over HTTP + SSE, or chat with one from the terminal."""

from __future__ import annotations

import argparse
import json
import logging
import sys

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


def serve(args: argparse.Namespace) -> None:
    import uvicorn
    from dotenv import find_dotenv, load_dotenv

    from ap_gateway import ModelGateway, ToolGateway
    from ap_gateway.anthropic_adapter import AnthropicAdapter
    from ap_sdk import InMemoryConversationStore, Runtime, load_agent
    from ap_transport.app import create_app

    # Nearest .env from the current directory upwards. It wins over variables set in the shell.
    load_dotenv(find_dotenv(usecwd=True), override=True)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    runtime = Runtime(
        agent=load_agent(args.agent),
        model_gateway=ModelGateway(default=AnthropicAdapter()),
        tool_gateway=ToolGateway(),
        store=InMemoryConversationStore(),
    )
    uvicorn.run(create_app(runtime), host=args.host, port=args.port, log_config=None)


def chat(args: argparse.Namespace) -> None:
    import httpx

    from ap_transport.client import Client

    client = Client(args.url)
    try:
        conversation_id = args.conversation or client.create_conversation()
    except httpx.HTTPError as exc:
        sys.exit(f"Cannot reach {args.url}: {exc}")
    print(f"conversation {conversation_id}  (Ctrl-D to quit)")

    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        print("agent> ", end="", flush=True)
        for event in client.send(conversation_id, text):
            if event.type == "token":
                print(event.data["text"], end="", flush=True)
            elif event.type == "tool_status":
                print(f"\n  [{event.data['tool']}: {event.data['status']}]", end="", flush=True)
                if event.data["status"] != "started":
                    print("\n", end="", flush=True)
            elif event.type == "error":
                print(f"\n  [error: {event.data['message']}]", end="", flush=True)
            elif event.type == "turn_done" and event.data.get("trace_id"):
                print(f"\n  [trace {event.data['trace_id']}]", end="", flush=True)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-platform")
    commands = parser.add_subparsers(required=True)

    serve_parser = commands.add_parser("serve", help="Serve an agent over HTTP + SSE")
    serve_parser.add_argument("--agent", required=True, help="Path to the agent definition (YAML)")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(run=serve)

    chat_parser = commands.add_parser("chat", help="Chat with a served agent")
    chat_parser.add_argument("--url", default="http://localhost:8000")
    chat_parser.add_argument("--conversation", help="Continue an existing conversation")
    chat_parser.set_defaults(run=chat)

    args = parser.parse_args()
    args.run(args)
