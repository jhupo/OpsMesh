"""Save SDK selections while each terminal keeps its own channel scope."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from client import response_text

if TYPE_CHECKING:
    from client import ChatClient


class ChatConsole:
    """Save SDK selection independently of the current channel UUID."""

    def __init__(
        self,
        client: ChatClient,
        state_file: Path | None = None,
        *,
        download_dir: Path | None = None,
    ) -> None:
        """Separate saved selections by server, plugin and credentials."""
        self.client = client
        self.download_dir = download_dir
        self.session_id: str | None = None
        self._profile = hashlib.sha256(
            (
                client.base_url.rstrip("/")
                + "\0"
                + client.plugin
                + "\0"
                + client.user_id
                + "\0"
                + client.token
            ).encode()
        ).hexdigest()
        self._path = state_file or Path.home() / ".opsmesh" / "chat" / (
            self._profile + ".json"
        )

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self._path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as output:
                json.dump(
                    {"profile": self._profile, "session_id": self.session_id},
                    output,
                )
            os.replace(temporary, self._path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def restore(self) -> None:
        """Revalidate saved SDK ownership in this window's fresh scope."""
        if not self._path.exists():
            return
        try:
            state = json.loads(self._path.read_text(encoding="utf-8"))
            if (
                not isinstance(state, dict)
                or state.get("profile") != self._profile
            ):
                return
            selected = state.get("session_id")
            if selected is None:
                self.session_id = None
                return
            selected = str(UUID(selected))
        except (ValueError, TypeError, AttributeError):
            return
        try:
            self.use(selected)
        except Exception as error:
            if getattr(error, "status", None) != 404:
                raise
            self.session_id = None
            self._save()

    def _record(self, response: dict) -> str:
        for message in response["messages"]:
            data = message.get("data") or {}
            if "session_id" in data:
                self.session_id = str(UUID(data["session_id"]))
            if data.get("deleted_session_id") == self.session_id:
                self.session_id = None
        self._save()
        if self.download_dir is not None:
            for message in response["messages"]:
                for part in message["content"]:
                    if part["type"] in {"image", "file", "audio", "video"}:
                        part["saved_path"] = str(
                            self.client.download(part, self.download_dir)
                        )
        return response_text(response)

    def use(self, session_id: str) -> str:
        """Ask the server to select an existing SDK conversation."""
        return self.execute("/use " + str(UUID(session_id)))

    def new(self, name: str = "New conversation") -> str:
        """Create an empty session through the shared command handler."""
        return self.execute("/new " + name)

    def execute(self, text: str, files: tuple[Path, ...] = ()) -> str:
        """Send commands and ordinary messages through one JSON endpoint."""
        return self._record(self.client.send(text, files))

    def execute_stream(self, text: str, files: tuple[Path, ...] = ()) -> str:
        """Display SSE increments and persist selection after completion."""
        live_text = False

        def display(delta: str) -> None:
            nonlocal live_text
            live_text = True
            print(delta, end="", flush=True)

        def complete(message: dict) -> None:
            nonlocal live_text
            if live_text:
                print()
                live_text = False
            content = response_text({"messages": [message]})
            if content:
                print(content, flush=True)

        response = self.client.send_stream(
            text, files, on_delta=display, on_message=complete
        )
        self._record(response)
        saved = [
            part["saved_path"]
            for message in response["messages"]
            for part in message["content"]
            if "saved_path" in part
        ]
        return "\n".join("Saved: " + path for path in saved)
