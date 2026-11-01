"""Exercise import spellings that can bypass superficial boundary checks."""

import runpy
from pathlib import Path

import pytest

CHECK_IMPORTS = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "check_repository.py")
)["find_import_violations"]


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("modules/agents/internal/service.py", "from opsmesh.modules.bots.internal import service"),
        ("modules/agents/internal/service.py", "from ...bots.internal import service"),
        ("modules/agents/internal/service.py", "from opsmesh.modules.bots import internal"),
        ("modules/agents/api.py", "from .internal import service"),
        ("framework/params.py", "from .. import modules"),
        ("framework/params.py", "from opsmesh import create_application"),
        ("modules/bots/api.py", "from opsmesh import OpsMeshApplication"),
        ("framework/message.py", "from examples.qq_community import main"),
        ("modules/bots/conversations/api.py", "from sqlalchemy import select"),
        ("modules/bots/conversations/queue.py", "import aiosqlite"),
        ("modules/agents/internal/service.py", "from agents import Agent"),
        ("bootstrap/services.py", "from opsmesh.plugins.scheduler import PersistentScheduler"),
        (
            "entrypoints/http/routes/scheduler.py",
            "from ....plugins.scheduler import PersistentSchedule",
        ),
        ("modules/bots/internal/service.py", "from opsmesh import plugins"),
        (
            "modules/bots/internal/service.py",
            "from opsmesh.infrastructure.bots.qq import QQAdapter",
        ),
        ("framework/adapters/managed.py", "from opsmesh.infrastructure.bots import qq"),
        ("infrastructure/bots/qq/adapter.py", "from opsmesh.modules.bots.api import BotAccount"),
        (
            "infrastructure/run_workers/launcher.py",
            "from opsmesh.bootstrap.settings import OpsMeshSettings",
        ),
        ("framework/drivers/base.py", "from opsmesh.infrastructure import database"),
        ("application/use_cases.py", "from opsmesh.bootstrap.services import OpsMeshServices"),
        ("application/agent_bridge.py", "from opsmesh.modules.runtime.internal import service"),
        ("application/coordinator.py", "from opsmesh import plugins"),
        ("modules/runtime/internal/service.py", "from opsmesh.application import coordinator"),
        ("modules/bots/api.py", "from ...infrastructure import bots"),
        ("modules/runtime/internal/service.py", "from opsmesh.infrastructure import run_workers"),
        (
            "entrypoints/http/routes/notifications.py",
            "from opsmesh.modules.notifications.internal import service",
        ),
    ],
)
def test_rejects_cross_boundary_imports(source: str, code: str) -> None:
    assert CHECK_IMPORTS(Path(source), code)


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("modules/agents/internal/service.py", "from opsmesh.modules.bots.api import BotAccounts"),
        ("modules/agents/internal/service.py", "from .repository import Repository"),
        ("bootstrap/services.py", "from opsmesh.modules.agents.internal import service"),
        ("modules/runtime/internal/backends/openai_agents/agent.py", "from agents import Agent"),
        ("plugins/commands.py", "from opsmesh.modules.bots.api import BotAccounts"),
        (
            "plugins/timers/__init__.py",
            "from opsmesh.modules.scheduling.api import SchedulingService",
        ),
        ("bootstrap/application.py", "from opsmesh.framework import load_plugin"),
        (
            "bootstrap/services.py",
            "from opsmesh.application.agent_bridge import AgentExecutionBridge",
        ),
        ("application/agent_bridge.py", "from opsmesh.modules.runtime.api import AgentRuntime"),
        ("bootstrap/bot_adapters.py", "from opsmesh.infrastructure.bots.qq import QQAdapter"),
        (
            "infrastructure/bots/dingtalk/adapter.py",
            "from opsmesh.framework.adapters import OpsMeshBotAdapter",
        ),
        (
            "infrastructure/run_workers/launcher.py",
            "from opsmesh.modules.runtime.api import AgentRuntime",
        ),
        (
            "infrastructure/database/availability.py",
            "from opsmesh.modules.resource_contracts import VersionAvailability",
        ),
    ],
)
def test_allows_public_apis_local_implementation_and_composition(source: str, code: str) -> None:
    assert not CHECK_IMPORTS(Path(source), code)
