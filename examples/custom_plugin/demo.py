"""Run with: uv run python examples/custom_plugin/demo.py. No model/network calls."""

import asyncio
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.dependencies import get_agent_factories
from opsmesh.framework.adapters import MessageEvent, OpsMeshMessage
from opsmesh.framework.events import DispatchContext


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    with TemporaryDirectory(prefix="opsmesh-plugin-demo-") as directory:
        application = OpsMeshApplication(
            OpsMeshSettings(
                data_directory=Path(directory),
                worker_enabled=False,
                plugins=("opsmesh_example_plugin",),
            )
        )
        try:
            await application.initialize()
            await application.start()
            context = DispatchContext(
                MessageEvent(
                    id="example-event",
                    message=OpsMeshMessage("/hello Ada"),
                    user_id="local-demo",
                    session_id="demo",
                )
            )
            result = await application.dispatch(context)
            print(context.values["example_reply"])
            print(f"Matched handlers: {result.matched}")
            specification = await get_agent_factories().build(
                "example.assistant@1",
                {"agent_id": "demo-assistant", "model_profile_ref": "example-model@1"},
                {},
            )
            print(f"Factory produced: {specification.ref} (not published or executed)")
        finally:
            await application.close()


if __name__ == "__main__":
    asyncio.run(main())
