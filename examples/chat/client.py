"""Call a deployed conversation plugin through the unified messages API."""

import argparse
import getpass
import json
import mimetypes
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID, uuid4

from client_stream import collect_stream

MAX_FILE_BYTES = 8 * 1024 * 1024


class ChatClientError(RuntimeError):
    """Report a safe protocol or business failure without replaying it."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Retain a status for explicit client recovery decisions."""
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def response_text(envelope: dict) -> str:
    """Render complete messages while omitting chat selection notices."""
    return "\n".join(
        "".join(
            part["text"]
            for part in message["content"]
            if part.get("type") == "text"
        )
        for message in envelope["messages"]
        if not isinstance(message.get("data"), dict)
        or message["data"].get("kind") != "selection"
    )


@dataclass(frozen=True)
class ChatClient:
    """Keep a fresh channel conversation UUID for each client window."""

    base_url: str
    token: str = field(repr=False)
    timeout: float = 300
    plugin: str = "example_chat"
    conversation_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        """Validate destination, identity token and channel UUID."""
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", self.plugin):
            raise ValueError("Invalid plugin identifier")
        address = urlsplit(self.base_url)
        if (
            address.scheme not in {"http", "https"}
            or not address.netloc
            or address.username is not None
            or address.query
            or address.fragment
        ):
            raise ValueError("Use an HTTP(S) server URL without credentials")
        if not self.token.strip() or self.timeout <= 0:
            raise ValueError("A token and a positive timeout are required")
        if str(UUID(self.conversation_id)) != self.conversation_id:
            raise ValueError("Use a canonical conversation UUID")

    def _request(self, request: Request, reader=json.load):
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return reader(response)
        except HTTPError as error:
            status = error.code
            try:
                details = json.load(error)
                failure = details.get("error", {})
                message = (
                    failure.get("message", "Request failed")
                    if isinstance(failure, dict)
                    else failure
                )
                if not isinstance(message, str):
                    message = "Request failed"
            except (ValueError, OSError, AttributeError):
                message = "Request failed"
            finally:
                error.close()
            raise ChatClientError(f"HTTP {status}: {message}", status) from None
        except (URLError, OSError):
            raise ChatClientError(
                "Connection failed or timed out; the request was not retried"
            ) from None
        except (ValueError, UnicodeError, KeyError, TypeError):
            raise ChatClientError(
                "Server returned an invalid response"
            ) from None

    def _message_request(self, message, accept: str) -> Request:
        return Request(
            self.base_url.rstrip("/") + "/plugins/" + self.plugin + "/messages",
            data=json.dumps(
                {"conversation_id": self.conversation_id, "message": message}
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": accept,
            },
            method="POST",
        )

    def _validate(self, envelope) -> dict:
        if not isinstance(envelope, dict):
            raise ChatClientError("Server returned an invalid message envelope")
        failure = envelope.get("error")
        if isinstance(failure, dict):
            raise ChatClientError(str(failure.get("message", "Request failed")))
        if (
            envelope.get("conversation_id") != self.conversation_id
            or envelope.get("status") not in {"completed", "waiting"}
            or not isinstance(envelope.get("messages"), list)
        ):
            raise ChatClientError("Server returned an invalid message envelope")
        for message in envelope["messages"]:
            if not isinstance(message, dict) or not isinstance(
                message.get("content"), list
            ):
                raise ChatClientError("Server returned an invalid message")
            for part in message["content"]:
                if not isinstance(part, dict) or not isinstance(
                    part.get("type"), str
                ):
                    raise ChatClientError(
                        "Server returned an invalid message segment"
                    )
                if part["type"] == "text" and not isinstance(
                    part.get("text"), str
                ):
                    raise ChatClientError("Server returned invalid text")
            data = message.get("data")
            if data is not None and not isinstance(data, dict):
                raise ChatClientError("Server returned invalid message data")
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                failure = data["error"]
                raise ChatClientError(
                    str(failure.get("message", "Command failed")),
                    failure.get("status"),
                )
        return envelope

    def _content(self, text: str, files: tuple[Path, ...]):
        text = text.strip()
        if len(text) > 8000 or not (text or files):
            raise ValueError(
                "Provide a question up to 8000 characters or files"
            )
        if len(files) > 8:
            raise ValueError("A message accepts at most 8 files")
        if not files:
            return text
        parts = [{"type": "text", "text": text}] if text else []
        return parts + [self.upload(path) for path in files]

    def send(self, text: str, files: tuple[Path, ...] = ()) -> dict:
        """Send one unified request and validate its JSON envelope."""
        request = self._message_request(
            self._content(text, files), "application/json"
        )
        return self._validate(self._request(request))

    def send_stream(
        self, text: str, files: tuple[Path, ...] = (), *, on_delta=None
    ) -> dict:
        """Collect SSE messages while optionally displaying text deltas."""
        request = self._message_request(
            self._content(text, files), "text/event-stream"
        )
        return self._validate(
            self._request(
                request, lambda response: collect_stream(response, on_delta)
            )
        )

    def upload(self, path: Path) -> dict[str, str]:
        """Materialize one bounded file as a controlled attachment reference."""
        media_type = (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        with path.open("rb") as source:
            content = source.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("The example client accepts files up to 8 MiB")
        request = Request(
            self.base_url.rstrip("/")
            + "/attachments/"
            + self.plugin
            + "?"
            + urlencode({"name": path.name}),
            data=content,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": media_type,
                "Accept": "application/json",
            },
            method="POST",
        )
        result = self._request(request)
        if not isinstance(result, dict) or not re.fullmatch(
            r"[a-f0-9]{32}", str(result.get("id", ""))
        ):
            raise ChatClientError("Server returned an invalid attachment")
        category = media_type.split("/", 1)[0]
        return {
            "type": category
            if category in {"image", "audio", "video"}
            else "file",
            "attachment_id": result["id"],
        }


def main() -> int:
    """Run one message or an interactive conversation client."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPSMESH_URL", "http://192.168.232.129:8080"),
    )
    parser.add_argument("--plugin", default="example_chat")
    parser.add_argument("--message", help="Send one question and exit")
    parser.add_argument("--file", type=Path, action="append", default=[])
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--session", help="Select an existing SDK conversation")
    parser.add_argument("--new", action="store_true")
    parser.add_argument(
        "--stream", action="store_true", help="Display SSE text increments"
    )
    parser.add_argument(
        "--state-file", type=Path, help="Saved SDK selection; no credentials"
    )
    args = parser.parse_args()
    try:
        from client_console import ChatConsole

        token = os.environ.get("OPSMESH_API_TOKEN") or getpass.getpass(
            "OpsMesh API token: "
        )
        client = ChatClient(args.base_url, token, args.timeout, args.plugin)
        console = ChatConsole(client, args.state_file)
        if args.new and args.session:
            raise ValueError("Use either --new or --session")
        if args.session:
            console.use(args.session)
        elif args.new:
            console.new()
        else:
            console.restore()
        execute = console.execute_stream if args.stream else console.execute
        if args.message is not None or args.file:
            print(execute(args.message or "", tuple(args.file)))
            return 0
        print("Use /help, /new, /sessions, /use <id>, /exit.")
        while True:
            message = input(f"[{console.session_id or 'new'}] You> ").strip()
            if message == "/exit":
                return 0
            if message:
                try:
                    print(execute(message))
                except (ChatClientError, ValueError, OSError) as error:
                    print(str(error), file=sys.stderr)
    except (ChatClientError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
