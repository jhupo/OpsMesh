"""Verify independent client scopes and the unified JSON/SSE contract."""

import importlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def client_modules(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "examples" / "chat")
    )
    return importlib.import_module("client"), importlib.import_module(
        "client_console"
    )


@pytest.fixture
def chat_server():
    sessions = {}
    selections = {}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            content = self.rfile.read(
                int(self.headers.get("Content-Length", 0))
            )
            upload = self.path.startswith("/attachments/")
            data = content if upload else json.loads(content or b"{}")
            requests.append((self.command, self.path, data))
            if self.headers.get("Authorization") != "Bearer test-token":
                self.reply(401, {"error": "Invalid credentials"})
                return
            if upload:
                self.reply(201, {"id": uuid4().hex})
                return
            if self.path not in {
                "/plugins/example_chat/messages",
                "/plugins/example_sandbox/messages",
            }:
                self.reply(404, {"error": "Missing route"})
                return
            scope = (self.path, data["conversation_id"])
            text = data["message"]
            command, _, argument = (
                text.partition(" ") if isinstance(text, str) else ("", "", "")
            )
            selected = selections.get(scope)
            structured = None
            reply = "command complete"
            if command == "/new":
                selected = str(uuid4())
                sessions[selected] = {
                    "id": selected,
                    "name": argument or "New conversation",
                }
                selections[scope] = selected
                structured = {
                    "session_id": selected,
                    "session": sessions[selected],
                }
            elif command == "/sessions":
                limit, offset = (
                    [int(value) for value in argument.split()]
                    if argument
                    else (50, 0)
                )
                page = list(sessions.values())[offset : offset + limit]
                structured = {
                    "sessions": page,
                    "limit": limit,
                    "offset": offset,
                    "next_offset": offset + limit
                    if len(page) == limit
                    else None,
                }
                reply = json.dumps(page)
            elif command in {"/use", "/delete"}:
                if argument not in sessions:
                    structured = {
                        "error": {
                            "status": 404,
                            "message": "Conversation not found",
                        }
                    }
                elif command == "/use":
                    selections[scope] = argument
                    structured = {
                        "session_id": argument,
                        "session": sessions[argument],
                    }
                else:
                    del sessions[argument]
                    if selected == argument:
                        selections.pop(scope)
                    structured = {"deleted_session_id": argument}
            elif selected is not None and selected not in sessions:
                structured = {
                    "error": {
                        "status": 404,
                        "message": "Conversation not found",
                    }
                }
            elif command in {
                "/clear",
                "/history",
                "/session",
                "/reset_workspace",
            }:
                structured = (
                    {"session_id": selected}
                    if selected
                    else {
                        "error": {
                            "status": 409,
                            "message": "No conversation selected",
                        }
                    }
                )
                reply = "reply:" + command
            elif command.startswith("/"):
                structured = {
                    "error": {"status": 400, "message": "Command unavailable"}
                }
            else:
                if selected is None:
                    selected = str(uuid4())
                    sessions[selected] = {
                        "id": selected,
                        "name": "New conversation",
                    }
                    selections[scope] = selected
                reply = "reply:" + (
                    text if isinstance(text, str) else "attachments"
                )
            messages = []
            if structured is None:
                messages.append(
                    {
                        "content": [
                            {"type": "text", "text": "Conversation selected."}
                        ],
                        "data": {"kind": "selection", "session_id": selected},
                    }
                )
            messages.append(
                {
                    "content": [{"type": "text", "text": reply}],
                    "data": structured,
                }
            )
            envelope = {
                "conversation_id": data["conversation_id"],
                "status": "completed",
                "messages": messages,
                "error": None,
            }
            if self.headers.get("Accept") == "text/event-stream":
                events = []
                for message in messages[:-1]:
                    events.append(
                        ("message", {"message_id": uuid4().hex, **message})
                    )
                identifier = uuid4().hex
                if structured is not None:
                    events.append(
                        ("message", {"message_id": identifier, **messages[-1]})
                    )
                else:
                    events.extend(
                        [
                            ("message_start", {"message_id": identifier}),
                            (
                                "message_delta",
                                {
                                    "message_id": identifier,
                                    "sequence": 1,
                                    "delta": reply[:6],
                                },
                            ),
                            (
                                "message_delta",
                                {
                                    "message_id": identifier,
                                    "sequence": 2,
                                    "delta": reply[6:],
                                },
                            ),
                            ("message_end", {"message_id": identifier}),
                        ]
                    )
                events.append(
                    (
                        "done",
                        {
                            "conversation_id": data["conversation_id"],
                            "status": "completed",
                        },
                    )
                )
                payload = b"".join(
                    f"event: {kind}\ndata: {json.dumps(value)}\n\n".encode()
                    for kind, value in events
                )
                self.reply(200, payload)
            else:
                self.reply(200, envelope)

        def reply(self, status, value):
            content = (
                value
                if isinstance(value, bytes)
                else json.dumps(value).encode()
            )
            self.send_response(status)
            self.send_header(
                "Content-Type",
                "text/event-stream"
                if isinstance(value, bytes)
                else "application/json",
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", sessions, requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_terminal_restore_uses_new_channel_scope_and_structured_selection(
    client_modules, chat_server, tmp_path
):
    client_module, console_module = client_modules
    address, _, requests = chat_server
    client = client_module.ChatClient(address, "test-token")
    console = console_module.ChatConsole(client, tmp_path / "state.json")
    assert console.execute("/new First") == "command complete"
    first = console.session_id
    assert console.execute("hello") == "reply:hello"
    console.execute("/new Second")
    second = console.session_id
    console.use(first)
    restored = console_module.ChatConsole(
        client_module.ChatClient(address, "test-token"), tmp_path / "state.json"
    )
    restored.restore()
    assert restored.session_id == first
    assert restored.client.conversation_id != client.conversation_id
    assert "test-token" not in (tmp_path / "state.json").read_text()
    assert restored.execute("/clear") == "reply:/clear"
    assert requests[-1][2] == {
        "message": "/clear",
        "conversation_id": restored.client.conversation_id,
    }
    assert {
        item["id"] for item in json.loads(console.execute("/sessions"))
    } == {first, second}
    console.execute("/delete " + first)
    assert console.session_id is None
    with pytest.raises(client_module.ChatClientError) as error:
        restored.execute("does not silently create another conversation")
    assert error.value.status == 404
    restored.restore()
    assert restored.session_id is None


def test_selection_does_not_change_when_use_fails(
    client_modules, chat_server, tmp_path
):
    client_module, console_module = client_modules
    address, _, _ = chat_server
    console = console_module.ChatConsole(
        client_module.ChatClient(address, "test-token"), tmp_path / "state.json"
    )
    console.new()
    original = console.session_id
    with pytest.raises(client_module.ChatClientError):
        console.use(str(uuid4()))
    assert console.session_id == original


def test_new_client_windows_do_not_switch_each_other(
    client_modules, chat_server, tmp_path
):
    module, consoles = client_modules
    address, _, _ = chat_server
    first = consoles.ChatConsole(
        module.ChatClient(address, "test-token"), tmp_path / "first.json"
    )
    second = consoles.ChatConsole(
        module.ChatClient(address, "test-token"), tmp_path / "second.json"
    )
    first.new("first")
    second.new("second")
    first.execute("first window")
    second.execute("second window")
    assert first.session_id != second.session_id


@pytest.mark.parametrize(
    "overrides", [{"token": "different-token"}, {"plugin": "example_sandbox"}]
)
def test_profiles_do_not_restore_other_identity_or_plugin(
    client_modules, chat_server, tmp_path, overrides
):
    module, consoles = client_modules
    address, _, _ = chat_server
    state = tmp_path / "state.json"
    first = consoles.ChatConsole(
        module.ChatClient(address, "test-token"), state
    )
    first.new()
    options = {"token": "test-token", **overrides}
    second = consoles.ChatConsole(module.ChatClient(address, **options), state)
    second.restore()
    assert second.session_id is None


def test_invalid_selection_is_rejected_before_request(
    client_modules, chat_server, tmp_path
):
    module, consoles = client_modules
    address, _, requests = chat_server
    console = consoles.ChatConsole(
        module.ChatClient(address, "test-token"), tmp_path / "state.json"
    )
    with pytest.raises(ValueError):
        console.use("../other-plugin")
    assert requests == []


@pytest.mark.parametrize("plugin", ["../other", "name/route", "", "name?x=y"])
def test_plugin_names_cannot_change_request_paths(client_modules, plugin):
    module, _ = client_modules
    with pytest.raises(ValueError):
        module.ChatClient("http://localhost", "token", plugin=plugin)


def test_upload_precedes_unified_message_with_ordered_segments(
    client_modules, chat_server, tmp_path
):
    module, _ = client_modules
    address, _, requests = chat_server
    client = module.ChatClient(address, "test-token", plugin="example_sandbox")
    document = tmp_path / "a b.txt"
    document.write_bytes(b"document content")
    assert (
        module.response_text(client.send("Read this", (document,)))
        == "reply:attachments"
    )
    assert requests[-2][1] == "/attachments/example_sandbox?name=a+b.txt"
    assert requests[-2][2] == b"document content"
    assert requests[-1][1] == "/plugins/example_sandbox/messages"
    message = requests[-1][2]["message"]
    assert message[0] == {"type": "text", "text": "Read this"}
    assert message[1]["type"] == "file"
    assert set(requests[-1][2]) == {"message", "conversation_id"}


def test_sse_collects_deltas_and_preserves_complete_messages(
    client_modules, chat_server
):
    module, _ = client_modules
    address, _, _ = chat_server
    client = module.ChatClient(address, "test-token")
    deltas = []
    result = client.send_stream("streamed answer", on_delta=deltas.append)
    assert deltas == ["reply:", "streamed answer"]
    assert len(result["messages"]) == 2
    assert module.response_text(result) == "reply:streamed answer"


def stream_bytes(events):
    return io.BytesIO(
        b"".join(
            f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode()
            for kind, data in events
        )
    )


@pytest.mark.parametrize(
    "events",
    [
        [
            ("message_start", {"message_id": "a"}),
            (
                "message_delta",
                {"message_id": "a", "sequence": 2, "delta": "wrong"},
            ),
        ],
        [
            ("message_start", {"message_id": "a"}),
            ("done", {"conversation_id": str(uuid4()), "status": "completed"}),
        ],
        [
            ("message_start", {"message_id": "a"}),
            ("message_end", {"message_id": "a"}),
        ],
        [
            (
                "message_delta",
                {"message_id": "missing", "sequence": 1, "delta": "wrong"},
            )
        ],
    ],
)
def test_sse_rejects_out_of_order_or_incomplete_answers(client_modules, events):
    module, _ = client_modules
    with pytest.raises(ValueError):
        module.collect_stream(stream_bytes(events))


def test_sse_complete_messages_are_not_merged(client_modules):
    module, _ = client_modules
    messages = [
        (
            "message",
            {
                "message_id": key,
                "content": [{"type": "text", "text": key}],
                "data": None,
            },
        )
        for key in ["a", "b"]
    ]
    response = module.collect_stream(
        stream_bytes(
            messages
            + [("done", {"conversation_id": str(uuid4()), "status": "waiting"})]
        )
    )
    assert module.response_text(response) == "a\nb"


def test_sse_error_does_not_mark_partial_answer_complete(client_modules):
    module, _ = client_modules
    client = module.ChatClient("http://localhost", "token")
    response = module.collect_stream(
        stream_bytes(
            [
                ("message_start", {"message_id": "a"}),
                (
                    "message_delta",
                    {"message_id": "a", "sequence": 1, "delta": "partial"},
                ),
                (
                    "error",
                    {
                        "conversation_id": client.conversation_id,
                        "code": "500",
                        "message": "internal server error",
                        "message_id": "a",
                    },
                ),
            ]
        )
    )
    with pytest.raises(module.ChatClientError, match="internal server error"):
        client._validate(response)
