"""Call the deployed chat plugin without model credentials or extra packages."""

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
from uuid import UUID


class ChatClientError(RuntimeError):
    """A request failed without an automatic retry."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Retain a safe HTTP status for recovery decisions."""
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    """Keep credentials and POST requests on the configured endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ChatClient:
    """Send authenticated questions to the OpsMesh chat plugin.

    Attributes:
        base_url: OpsMesh HTTP address, not the model provider address.
        token: API identity token configured by the channels plugin.
        timeout: Maximum seconds to wait for one complete answer.
    """

    base_url: str
    token: str = field(repr=False)
    timeout: float = 300
    plugin: str = "example_chat"

    def __post_init__(self) -> None:
        """Validate the destination and authentication options."""
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

    def request(self, method: str, path: str, payload=None):
        """Call one plugin endpoint without redirects or automatic replay."""
        return self._request(
            method,
            "/plugins/" + self.plugin + path,
            json.dumps(payload).encode("utf-8")
            if payload is not None
            else None,
        )

    def _request(
        self,
        method: str,
        path: str,
        content: bytes | None,
        media_type: str = "application/json",
    ):
        request = Request(
            self.base_url.rstrip("/") + path,
            data=content,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": media_type,
                "Accept": "application/json",
            },
            method=method,
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return None if response.status == 204 else json.load(response)
        except HTTPError as error:
            status = error.code
            try:
                details = json.load(error)
                message = details.get("error", "Request failed")
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
        except (ValueError, UnicodeError):
            raise ChatClientError("Server returned invalid JSON") from None

    def upload(self, path: Path) -> dict[str, str]:
        """Upload a file and return a message reference under this plugin."""
        media_type = (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        with path.open("rb") as source:
            content = source.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("The example client accepts files up to 8 MiB")
        result = self._request(
            "POST",
            "/attachments/"
            + self.plugin
            + "?"
            + urlencode({"name": path.name}),
            content,
            media_type,
        )
        if not isinstance(result, dict) or not re.fullmatch(
            r"[a-f0-9]{32}", str(result.get("id", ""))
        ):
            raise ChatClientError("Server returned an invalid attachment")
        category = media_type.split("/", 1)[0]
        kind = category if category in {"image", "audio", "video"} else "file"
        return {"type": kind, "attachment_id": result["id"]}

    def ask(
        self, message: str, session_id: str, *, files: tuple[Path, ...] = ()
    ) -> str:
        """Send one turn or server command to the selected conversation."""
        message = message.strip()
        if len(message) > 8000 or not (message or files):
            raise ValueError(
                "Provide a question up to 8000 characters or files"
            )
        if len(files) > 8:
            raise ValueError("A message accepts at most 8 files")
        session_id = str(UUID(session_id))
        parts = [{"type": "text", "text": message}] if message else []
        parts.extend(self.upload(path) for path in files)
        payload = self.request(
            "POST",
            "/chat",
            {
                "message": parts if files else message,
                "session_id": session_id,
            },
        )
        if not isinstance(payload, dict) or not isinstance(
            payload.get("text"), str
        ):
            raise ChatClientError("Server response is missing a text answer")
        if payload.get("session_id") != session_id:
            raise ChatClientError("Server returned a different conversation")
        return payload["text"]

    def create_session(self, name: str = "New conversation") -> dict:
        """Create an empty conversation, even before its first model call."""
        result = self.request("POST", "/sessions", {"name": name})
        if not isinstance(result, dict) or not isinstance(
            result.get("id"), str
        ):
            raise ChatClientError("Server returned an invalid conversation")
        UUID(result["id"])
        return result

    def get_session(self, session_id: str) -> dict:
        """Verify access before changing the client's selection."""
        result = self.request("GET", "/sessions/" + str(UUID(session_id)))
        if not isinstance(result, dict) or result.get("id") != str(
            UUID(session_id)
        ):
            raise ChatClientError("Server returned an invalid conversation")
        if result.get("deleting"):
            raise ChatClientError("Conversation is being deleted", 409)
        return result

    def list_sessions(self) -> list[dict]:
        """Read all pages of the current caller's conversation indexes."""
        items = []
        while True:
            page = self.request(
                "GET", f"/sessions?limit=200&offset={len(items)}"
            )
            if not isinstance(page, list):
                raise ChatClientError(
                    "Server returned an invalid conversation list"
                )
            items.extend(page)
            if len(page) < 200:
                return items

    def delete_session(self, session_id: str) -> None:
        """Delete a conversation and its native history."""
        self.request("DELETE", "/sessions/" + str(UUID(session_id)))


def main() -> int:
    """Run a single question or an interactive terminal conversation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPSMESH_URL", "http://192.168.232.129:8080"),
    )
    parser.add_argument("--plugin", default="example_chat")
    parser.add_argument("--message", help="Send one question and exit")
    parser.add_argument(
        "--file",
        type=Path,
        action="append",
        default=[],
        help="Upload a file for one turn; may be repeated",
    )
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--session", help="Select an existing conversation")
    parser.add_argument(
        "--new", action="store_true", help="Create a new conversation"
    )
    parser.add_argument(
        "--state-file", type=Path, help="Local selection file; no credentials"
    )
    args = parser.parse_args()
    try:
        token = os.environ.get("OPSMESH_API_TOKEN") or getpass.getpass(
            "OpsMesh API token: "
        )
        client = ChatClient(args.base_url, token, args.timeout, args.plugin)
        from client_console import ChatConsole

        console = ChatConsole(client, args.state_file)
        if args.new and args.session:
            raise ValueError("Use either --new or --session")
        if args.session:
            console.use(args.session)
        elif args.new:
            console.new()
        else:
            console.restore()
        if args.message is not None or args.file:
            if args.file:
                if console.session_id is None:
                    console.new()
                print(
                    client.ask(
                        args.message or "",
                        console.session_id,
                        files=tuple(args.file),
                    )
                )
            else:
                print(console.execute(args.message))
            return 0
        print(
            "Persistent conversations. "
            "Use /help, /new, /sessions, /use <id>, /exit."
        )
        while True:
            message = input(f"[{console.session_id or 'new'}] You> ").strip()
            if message == "/exit":
                return 0
            if not message:
                continue
            try:
                print(console.execute(message))
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
