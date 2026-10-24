import asyncio
import logging
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from agents import ModelSettings, Usage, function_tool
from agents.items import ModelResponse
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from opsmesh.bootstrap.schema import load_models
from opsmesh.catalog.deps import InvocationDependencies
from opsmesh.infra.database import Base, new_id, now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.quotas.models import UserQuota
from opsmesh.quotas.tokens import MeteredModel
from opsmesh.sessions.models import AgentSession
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.logging import Formatter
from opsmesh.tasks.interactions import checkpoint, validate_checkpoint
from opsmesh.tasks.models import Task
from opsmesh.tasks.runtime.runner import TaskRunner
from opsmesh.users.models import User


class RuntimeRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_missing_usage_preserves_reservation_and_enables_sdk_raw_usage(self):
        inner = Mock()
        inner.get_response = AsyncMock(
            return_value=ModelResponse(
                output=[], usage=Usage(requests=1), response_id=None, raw_usage=None
            )
        )
        model = MeteredModel(inner, Mock(), "user", "task", 100, Mock())
        model._reserve = Mock(return_value="reservation")
        model._settle = Mock()
        await model.get_response(model_settings=ModelSettings())
        model._settle.assert_not_called()
        settings = inner.get_response.call_args.kwargs["model_settings"]
        self.assertTrue(settings.preserve_raw_usage)
        self.assertTrue(settings.include_usage)
        inner.get_response.return_value.raw_usage = {"total_tokens": 0}
        await model.get_response(model_settings=ModelSettings())
        model._settle.assert_called_once_with("reservation", 0)

    async def test_native_tool_options_survive_aliasing(self):
        @function_tool(needs_approval=True, timeout=3)
        async def approved(value: str) -> str:
            return value

        deps = InvocationDependencies(Mock(), Mock(), Mock(), Path("."), {}, "caller", AsyncMock())
        deps.publication = Mock(return_value=SimpleNamespace(contribution="approved"))
        deps._plugin = Mock(return_value=SimpleNamespace(tools={"approved": approved}))
        native = await deps.tool("alias")
        self.assertEqual(native.name, "alias")
        self.assertTrue(native.needs_approval)
        self.assertEqual(native.timeout_seconds, 3)
        self.assertEqual(approved.name, "approved")

    async def test_timeout_cleans_execution_before_releasing_session(self):
        task = SimpleNamespace(timeout_seconds=0.01, cancel_requested=False, status="running")
        cleaned = False

        @contextmanager
        def transaction():
            yield Mock()

        sessions = Mock()
        sessions.begin = transaction
        runner = TaskRunner(sessions, Mock(), PluginRegistry(), Path("."), encryption_key="unused")
        runner.leases.active = Mock(return_value=task)

        async def invoke(*args):
            nonlocal cleaned
            try:
                await asyncio.sleep(10)
            finally:
                self.assertEqual(task.status, "running")
                await asyncio.sleep(0)
                cleaned = True

        def fail(*args):
            self.assertTrue(cleaned)
            task.status = args[2]

        runner.invoker.invoke = invoke
        runner.leases.fail = fail
        await runner.execute("task", "attempt")
        self.assertEqual(task.status, "timed_out")

    async def test_child_capacity_fails_without_waiting(self):
        runner = TaskRunner(
            Mock(),
            Mock(),
            PluginRegistry(),
            Path("."),
            encryption_key="unused",
            child_concurrency=1,
        )
        await runner.children.slots.acquire()
        with self.assertRaisesRegex(ConflictError, "capacity"):
            await runner.children.call(Mock(), "attempt", Mock(), None, "key")
        runner.children.slots.release()

    async def test_logging_keeps_stack_without_exception_secrets(self):
        try:
            raise ValueError("sensitive-example")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
            )
        rendered = Formatter().format(record)
        self.assertIn("exception_stack", rendered)
        self.assertNotIn("sensitive-example", rendered)


@unittest.skipUnless(
    os.environ.get("OPSMESH_TEST_DATABASE_URL"),
    "Requires an explicitly configured PostgreSQL test database",
)
class DatabaseRegressions(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(os.environ["OPSMESH_TEST_DATABASE_URL"])
        self.schema = "opsmesh_review_" + new_id().replace("-", "")
        with self.engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.bound = self.engine.execution_options(schema_translate_map={None: self.schema})
        load_models()
        Base.metadata.create_all(self.bound)
        self.sessions = sessionmaker(self.bound, expire_on_commit=False)
        self.folder = tempfile.TemporaryDirectory()
        self.cipher = SecretCipher(Fernet.generate_key().decode())
        self.runner = TaskRunner(
            self.sessions,
            self.cipher,
            PluginRegistry(),
            Path(self.folder.name),
            encryption_key=Fernet.generate_key().decode(),
        )

    def tearDown(self):
        with self.engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.engine.dispose()
        self.folder.cleanup()

    def user(self, db, name, concurrency=1):
        row = User(name=name, roles=["user"])
        db.add(row)
        db.flush()
        db.add(UserQuota(user_id=row.id, concurrent_tasks=concurrency))
        db.flush()
        return row

    def task(self, db, user, *, conversation=None, status="queued", created_at=None):
        if conversation is None:
            conversation = AgentSession(
                owner_id=user.id,
                caller_id=user.id,
                agent_key=new_id(),
                binding_key=new_id(),
                scope="private",
            )
            db.add(conversation)
            db.flush()
        row = Task(
            owner_id=user.id,
            session_id=conversation.id,
            snapshot={},
            encrypted_input="unused",
            encrypted_source=self.cipher.encrypt({}),
            status=status,
            created_at=created_at or now(),
        )
        db.add(row)
        db.flush()
        return row

    def test_quota_saturated_user_does_not_starve_later_tasks(self):
        with self.sessions.begin() as db:
            first = self.user(db, "first")
            second = self.user(db, "second")
            self.task(db, first, status="running")
            for offset in range(55):
                self.task(db, first, created_at=now() - timedelta(minutes=1, seconds=offset))
            eligible = self.task(db, second)
        claimed = self.runner.leases.claim()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], eligible.id)

    def test_dirty_session_does_not_advance_queued_turn(self):
        with self.sessions.begin() as db:
            user = self.user(db, "dirty")
            row = self.task(db, user)
            conversation = db.get(AgentSession, row.session_id)
            conversation.sandbox_dirty = True
            conversation.sandbox_task_id = new_id()
            conversation.revision = 7
        self.assertIsNone(self.runner.leases.claim())
        with self.sessions() as db:
            self.assertEqual(db.get(AgentSession, row.session_id).revision, 7)

    def test_checkpoint_rejects_dirty_or_changed_sandbox(self):
        with self.sessions.begin() as db:
            user = self.user(db, "checkpoint")
            row = self.task(db, user)
            _, attempt_id = self.runner.leases.start(db, row)
            saved = checkpoint(db, self.cipher, row, attempt_id, {"state": "saved"})
            validate_checkpoint(db, row, saved)
            conversation = db.get(AgentSession, row.session_id)
            conversation.sandbox_generation += 1
            with self.assertRaises(ConflictError):
                validate_checkpoint(db, row, saved)
            conversation.sandbox_dirty = True
            with self.assertRaises(ConflictError):
                checkpoint(db, self.cipher, row, attempt_id, {})


if __name__ == "__main__":
    unittest.main()
