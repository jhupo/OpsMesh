"""Entry point for the OpsMesh Webhook Ops example."""

from pathlib import Path

from adapter import WebhookAdapter

import opsmesh


def bootstrap() -> None:
    base_dir = Path(__file__).resolve().parent
    opsmesh.init(
        driver="~fastapi",
        superusers={"webhook:ops_root"},
        deploy_env="prod",
    )
    opsmesh.register_adapter(WebhookAdapter)
    opsmesh.load_from_toml(base_dir / "pyproject.toml")


if __name__ == "__main__":
    bootstrap()
    opsmesh.run(host="127.0.0.1", port=8080)
