"""Verify terminal selection persistence and the standalone HTTP client."""

import importlib
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
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def do_DELETE(self):
            self.respond()

        def respond(self):
            content = self.rfile.read(
                int(self.headers.get("Content-Length", 0))
            )
            data = (
                content
                if self.path.startswith("/attachments/")
                else json.loads(content or b"{}")
            )
            requests.append((self.command, self.path, data))
            path = self.path.removeprefix("/plugins/example_chat")
            path = path.removeprefix("/plugins/example_sandbox")
            status = 200
            if self.headers.get("Authorization") != "Bearer test-token":
                status, result = 401, {"error": "Invalid credentials"}
            elif self.path.startswith("/attachments/"):
                status, result = 201, {"id": uuid4().hex}
            elif path == "/sessions" and self.command == "POST":
                key = str(uuid4())
                result = {"id": key, "name": data["name"]}
                sessions[key] = result
            elif path.startswith("/sessions?"):
                result = list(sessions.values())
            elif path.startswith("/sessions/"):
                key = path.rsplit("/", 1)[-1]
                if key not in sessions:
                    status, result = 404, {"error": "Conversation not found"}
                elif self.command == "DELETE":
                    del sessions[key]
                    status, result = 204, None
                else:
                    result = sessions[key]
            elif path == "/chat":
                key = data["session_id"]
                if key not in sessions:
                    status, result = 404, {"error": "Conversation not found"}
                else:
                    result = {
                        "session_id": key,
                        "text": "reply:"
                        + (
                            data["message"]
                            if isinstance(data["message"], str)
                            else "attachments"
                        ),
                    }
            else:
                status, result = 404, {"error": "Missing route"}
            content = b"" if status == 204 else json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
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


def test_terminal_switch_restore_and_commands(
    client_modules, chat_server, tmp_path
):
    client_module, console_module = client_modules
    address, _, requests = chat_server
    client = client_module.ChatClient(address, "test-token")
    console = console_module.ChatConsole(client, tmp_path / "state.json")
    console.execute("/new First")
    first = console.session_id
    assert console.execute("hello") == "reply:hello"
    console.execute("/new Second")
    second = console.session_id
    assert second != first
    console.execute("/use " + first)
    restored = console_module.ChatConsole(client, tmp_path / "state.json")
    restored.restore()
    assert restored.session_id == first
    assert "test-token" not in (tmp_path / "state.json").read_text()
    assert restored.execute("/clear") == "reply:/clear"
    assert requests[-1][2] == {"message": "/clear", "session_id": first}
    assert {
        item["id"] for item in json.loads(console.execute("/sessions"))
    } == {first, second}
    console.execute("/delete " + first)
    assert console.session_id is None
    assert restored.session_id == first
    with pytest.raises(client_module.ChatClientError) as error:
        restored.execute("does not silently create another conversation")
    assert error.value.status == 404
    restored.restore()
    assert restored.session_id is None


def test_selection_cannot_switch_to_an_unowned_session(
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


def test_client_profiles_do_not_share_saved_selections(
    client_modules, chat_server, tmp_path
):
    client_module, console_module = client_modules
    address, _, _ = chat_server
    state = tmp_path / "state.json"
    first = console_module.ChatConsole(
        client_module.ChatClient(address, "test-token"), state
    )
    first.new()
    second = console_module.ChatConsole(
        client_module.ChatClient(address, "different-token"), state
    )
    second.restore()
    assert second.session_id is None


def test_session_ids_cannot_change_request_paths(client_modules, chat_server):
    client_module, _ = client_modules
    address, _, requests = chat_server
    client = client_module.ChatClient(address, "test-token")
    with pytest.raises(ValueError):
        client.delete_session("../other-plugin")
    assert requests == []


def test_sandbox_client_routes_and_selection_are_separate(
    client_modules, chat_server, tmp_path
):
    client_module, console_module = client_modules
    address, _, requests = chat_server
    state = tmp_path / "state.json"
    chat = console_module.ChatConsole(
        client_module.ChatClient(address, "test-token"), state
    )
    chat.new()
    sandbox = console_module.ChatConsole(
        client_module.ChatClient(
            address, "test-token", plugin="example_sandbox"
        ),
        state,
    )
    sandbox.restore()
    assert sandbox.session_id is None
    sandbox.new()
    assert sandbox.execute("/reset_workspace") == "reply:/reset_workspace"
    assert requests[-1][1] == "/plugins/example_sandbox/chat"
    assert sandbox.session_id != chat.session_id


@pytest.mark.parametrize("plugin", ["../other", "name/route", "", "name?x=y"])
def test_plugin_names_cannot_change_request_paths(client_modules, plugin):
    client_module, _ = client_modules
    with pytest.raises(ValueError):
        client_module.ChatClient("http://localhost", "token", plugin=plugin)


def test_client_uploads_files_before_sending_ordered_message(
    client_modules, chat_server, tmp_path
):
    module, _ = client_modules
    address, _, requests = chat_server
    client = module.ChatClient(address, "test-token", plugin="example_sandbox")
    selected = client.create_session()["id"]
    document = tmp_path / "a b.txt"
    document.write_bytes(b"document content")
    assert (
        client.ask("Read this", selected, files=(document,))
        == "reply:attachments"
    )
    assert requests[-2][1] == "/attachments/example_sandbox?name=a+b.txt"
    assert requests[-2][2] == b"document content"
    message = requests[-1][2]["message"]
    assert message[0] == {"type": "text", "text": "Read this"}
    assert message[1]["type"] == "file"
    assert len(message[1]["attachment_id"]) == 32
    assert requests[-1][2]["session_id"] == selected
