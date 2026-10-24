"""Deployment-owned idle conversation policy, using existing filters/tools/push API."""

import asyncio
import hashlib
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

import httpx
from opsmesh_sdk import PluginContext, get_invocation, get_plugin_credential
from opsmesh_sdk.channels import ChannelMedia, InboundMessage
from opsmesh_sdk.identity import AuthorizationRequest
from opsmesh_sdk.messages import MessageFilterResult
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    channel_id: UUID
    conversation_id: str = Field(min_length=1, max_length=500)
    scheduler_user_id: UUID
    session_ref: str = "env:OPSMESH_ACTIVITY_SESSION"
    api_url: str = "http://127.0.0.1:8000/api/v1"
    idle_seconds: int = Field(default=1800, ge=600, le=86400)
    cooldown_seconds: int = Field(default=7200, ge=600, le=604800)
    daily_limit: int = Field(default=3, ge=1, le=10)
    timezone: str = "Asia/Shanghai"
    quiet_start_hour: int = Field(default=23, ge=0, le=23)
    quiet_end_hour: int = Field(default=9, ge=0, le=23)

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        ZoneInfo(self.timezone)
        url = httpx.URL(self.api_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
            or (url.scheme == "http" and url.host not in {"127.0.0.1", "localhost", "::1"})
        ):
            raise ValueError("The management API requires HTTPS or a loopback HTTP address")
        return self


class Activity:
    def __init__(self, settings: Settings, directory: Path) -> None:
        self.settings, self.directory = settings, directory
        self.key = hashlib.sha256(
            f"{settings.channel_id}:{settings.conversation_id}".encode()
        ).hexdigest()

    @contextmanager
    def database(self) -> Iterator[sqlite3.Connection]:
        # setup() registers only. Storage is created at the first actual invocation.
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        database = self.directory / "activity.sqlite"
        try:
            descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o660)
        except FileExistsError:
            pass
        else:
            try:
                # Gateway and Worker have separate UIDs in the shared service group.
                if os.name != "nt":
                    os.fchmod(descriptor, 0o660)
            finally:
                os.close(descriptor)
        connection = sqlite3.connect(database, timeout=1)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS activity (scope TEXT PRIMARY KEY, "
                    "event TEXT NOT NULL, observed REAL NOT NULL, nudged TEXT, "
                    "attempted REAL, day TEXT, attempts INTEGER NOT NULL DEFAULT 0)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS observations "
                    "(id TEXT PRIMARY KEY, seen REAL NOT NULL)"
                )
            yield connection
        finally:
            connection.close()

    def observe(self, identifier: str) -> None:
        now = time.time()
        with self.database() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO observations VALUES (?, ?)", (identifier, now)
            ).rowcount
            if not inserted:
                return
            connection.execute(
                "INSERT INTO activity (scope, event, observed) VALUES (?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET event=excluded.event, observed=excluded.observed",
                (self.key, identifier, now),
            )
            connection.execute("DELETE FROM observations WHERE seen < ?", (now - 604800,))

    def eligible(self, row: sqlite3.Row | None, now: float) -> bool:
        if row is None or row["nudged"] == row["event"]:
            return False
        local = datetime.fromtimestamp(now, ZoneInfo(self.settings.timezone))
        start, end = self.settings.quiet_start_hour, self.settings.quiet_end_hour
        quiet = (
            start <= local.hour < end
            if start < end
            else (local.hour >= start or local.hour < end)
            if start > end
            else False
        )
        return (
            not quiet
            and now - row["observed"] >= self.settings.idle_seconds
            and (
                row["attempted"] is None or now - row["attempted"] >= self.settings.cooldown_seconds
            )
            and (
                row["day"] != local.date().isoformat()
                or row["attempts"] < self.settings.daily_limit
            )
        )

    def candidate(self) -> dict[str, object]:
        with self.database() as connection:
            row = connection.execute("SELECT * FROM activity WHERE scope=?", (self.key,)).fetchone()
            if not self.eligible(row, time.time()):
                return {"ready": False}
            assert row is not None
            return {"ready": True, "nonce": row["event"]}

    def claim(self, nonce: str) -> bool:
        now = time.time()
        with self.database() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM activity WHERE scope=?", (self.key,)).fetchone()
            if not self.eligible(row, now) or row is None or row["event"] != nonce:
                return False
            day = datetime.fromtimestamp(now, ZoneInfo(self.settings.timezone)).date().isoformat()
            count = row["attempts"] + 1 if row["day"] == day else 1
            connection.execute(
                "UPDATE activity SET nudged=?, attempted=?, day=?, attempts=? WHERE scope=?",
                (nonce, now, day, count, self.key),
            )
            # Reserve before network I/O. An ambiguous POST must not cause blind retries.
            return True

    def unchanged(self, nonce: str) -> bool:
        with self.database() as connection:
            row = connection.execute(
                "SELECT event FROM activity WHERE scope=?", (self.key,)
            ).fetchone()
            return row is not None and row["event"] == nonce

    async def send(self, nonce: str, text: str) -> dict[str, object]:
        if not nonce or len(nonce) > 100 or not text.strip() or len(text) > 160:
            raise ValueError("A current nonce and a short conversation starter are required")
        if not await asyncio.to_thread(self.claim, nonce):
            return {"status": "skipped"}
        token = str(UUID(get_plugin_credential("session").get_secret_value()))
        prefix = self.settings.api_url.rstrip("/") + f"/gateway/channels/{self.settings.channel_id}"
        async with httpx.AsyncClient(
            headers={"X-Session-ID": token}, timeout=5, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.get(prefix + "/subjects")
            response.raise_for_status()
            subjects = response.json()
            if not isinstance(subjects, list):
                raise ValueError("Invalid subject response")
            targets = [
                item["receipt_id"]
                for item in subjects
                if isinstance(item, dict)
                and isinstance(item.get("conversation"), dict)
                and item["conversation"].get("external_id") == self.settings.conversation_id
                and item["conversation"].get("kind") == "group"
            ]
            if len(targets) != 1:
                raise ValueError("One verified group destination is required")
            receipt = str(UUID(targets[0]))
            if not await asyncio.to_thread(self.unchanged, nonce):
                return {"status": "superseded"}
            response = await client.post(
                prefix + "/push",
                json={
                    "request_id": str(uuid5(NAMESPACE_URL, f"group-activity:{self.key}:{nonce}")),
                    "recipients": [receipt],
                    "content": [{"type": "text", "text": text}],
                },
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or not result.get("deliveries"):
                raise ValueError("The gateway did not confirm a queued delivery")
            return {"status": "queued"}


def setup(ctx: PluginContext) -> None:
    settings = Settings.model_validate(dict(ctx.config))
    activity = Activity(settings, ctx.data_directory)
    ctx.credential("session", settings.session_ref)

    def authorize(request: AuthorizationRequest) -> bool:
        return (
            request.principal.user_id == settings.scheduler_user_id
            and "admin" in request.principal.roles
            and request.source.channel_id is None
        )

    def scheduler() -> None:
        current = get_invocation()
        if (
            current.user_id != settings.scheduler_user_id
            or not current.is_admin
            or current.source.channel_id is not None
        ):
            raise PermissionError("Only the configured scheduler may use activity tools")

    async def observe(message: InboundMessage, media: ChannelMedia) -> MessageFilterResult:
        current = get_invocation()
        if (
            current.source.channel_id == settings.channel_id
            and message.conversation.kind == "group"
            and message.conversation.external_id == settings.conversation_id
        ):
            await asyncio.to_thread(activity.observe, str(current.operation_id))
        return MessageFilterResult(action="allow")

    async def idle_candidate() -> dict[str, object]:
        """Return a nonce only when the configured group may receive one conversation starter."""
        scheduler()
        return await asyncio.to_thread(activity.candidate)

    async def send_idle(nonce: str, text: str) -> dict[str, object]:
        """Recheck eligibility and queue one short message to the configured group."""
        scheduler()
        return await activity.send(nonce, text)

    ctx.message_filter("observe", observe, timeout_seconds=3)
    for name, fn, effects in (
        ("idle_candidate", idle_candidate, ("database",)),
        ("send_idle", send_idle, ("database", "external_api")),
    ):
        ctx.tool(
            name, fn, admin_only=True, authorize=authorize, effects=effects, timeout_seconds=20
        )
