"""Terminal selection and local commands; model history stays on the server."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from client import ChatClient


class ChatConsole:
    """Keep an independent conversation selection for this terminal."""

    def __init__(
        self, client: ChatClient, state_file: Path | None = None
    ) -> None:
        """Separate saved selections by server and credential fingerprint."""
        self.client = client
        self.session_id: str | None = None
        self._profile = hashlib.sha256(
            (
                client.base_url.rstrip("/")
                + "\0"
                + client.plugin
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
        """Resume a saved selection only after the server checks ownership."""
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

    def use(self, session_id: str) -> str:
        """Switch this terminal without changing another client's selection."""
        selected = self.client.get_session(str(UUID(session_id)))
        self.session_id = selected["id"]
        self._save()
        return f"Selected {self.session_id}: {selected['name']}"

    def new(self, name: str = "New conversation") -> str:
        """Create and select an empty server-side conversation."""
        selected = self.client.create_session(name)
        self.session_id = selected["id"]
        self._save()
        return f"Created {self.session_id}: {selected['name']}"

    def execute(self, text: str) -> str:
        """Navigate locally or send text to server command matchers."""
        command, _, argument = text.strip().partition(" ")
        argument = argument.strip()
        if command == "/new":
            return self.new(argument or "New conversation")
        if command == "/use":
            if not argument:
                raise ValueError("Usage: /use <session-id>")
            return self.use(argument)
        if command == "/sessions":
            if argument:
                raise ValueError("Usage: /sessions")
            return json.dumps(
                self.client.list_sessions(), ensure_ascii=False, indent=2
            )
        if command == "/delete":
            if not argument:
                raise ValueError("Usage: /delete <session-id>")
            selected = str(UUID(argument))
            self.client.delete_session(selected)
            if selected == self.session_id:
                self.session_id = None
                self._save()
            return f"Deleted {selected}"
        if self.session_id is None:
            self.new()
        return self.client.ask(text, self.session_id)
