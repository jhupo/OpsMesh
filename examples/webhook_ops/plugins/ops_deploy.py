"""Interactive deploy command plugin with SUPERUSER permission and Depends."""

from collections.abc import AsyncGenerator

from pydantic import BaseModel

from opsmesh import get_plugin_config, on_command
from opsmesh.adapters import Event, Gateway, Message
from opsmesh.log import logger
from opsmesh.params import ArgPlainText, CommandArg, Depends
from opsmesh.permission import SUPERUSER
from opsmesh.plugin import PluginMetadata
from opsmesh.rule import to_me


class DeployConfig(BaseModel):
    deploy_env: str = "staging"


__plugin_meta__ = PluginMetadata(
    name="服务发布助手",
    description="演示权限控制、配置读取、依赖注入与多轮参数确认",
    usage="/deploy [service]",
    type="application",
    config=DeployConfig,
)

cfg = get_plugin_config(DeployConfig)
deploy = on_command("deploy", rule=to_me(), permission=SUPERUSER)


async def audit_context(event: Event) -> AsyncGenerator[str, None]:
    trace_id = f"trace:{event.get_session_id()}"
    logger.debug(f"Entering audit context {trace_id}")
    try:
        yield trace_id
    finally:
        logger.debug(f"Leaving audit context {trace_id}")


@deploy.handle()
async def capture_initial_arg(args: Message = CommandArg()) -> None:
    if args.extract_plain_text().strip():
        deploy.set_arg("service", args)


@deploy.got("service", prompt="请输入待发布服务名（api / worker）：")
async def run_deploy(
    gateway: Gateway,
    service: str = ArgPlainText(),
    trace_id: str = Depends(audit_context),
) -> None:
    target = service.strip()
    if target not in {"api", "worker"}:
        await deploy.reject("仅支持 api 或 worker，请重新输入：")
    res = await gateway.invoke(
        "trigger_deploy",
        service=target,
        env=cfg.deploy_env,
        trace_id=trace_id,
    )
    await deploy.finish(f"已触发 {target} 发布至 {cfg.deploy_env}（{res['ok']}）")
