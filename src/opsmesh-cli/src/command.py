import argparse
import asyncio
import json
import time
import uuid
from importlib.metadata import version

from .config import Fault, require
from .plugin.manager import ARGS, READ
from .socket import request


def parser():
    p = argparse.ArgumentParser(prog="opsmesh")
    p.add_argument("--config", default="/etc/opsmesh/opsmesh.toml")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--version", action="version", version=version("opsmesh-cli")
    )
    groups = p.add_subparsers(dest="group", required=True)
    groups.add_parser("serve", help="Run the foreground Linux service")
    sub = {}
    for method, spec in ARGS.items():
        group, action = method.split(".")
        if group not in sub:
            sub[group] = groups.add_parser(group).add_subparsers(
                dest="action", required=True
            )
        cmd = sub[group].add_parser(action)
        for field in spec.split():
            cmd.add_argument("--" + field, required=True) if field in (
                "sha256",
                "confirm",
            ) else cmd.add_argument(
                field, metavar=field.upper()
            )
        if method not in READ:
            cmd.add_argument("--plan", action="store_true")
            cmd.add_argument("--allow-restart", action="store_true")
            cmd.add_argument("--no-wait", action="store_true")
            cmd.add_argument("--request-id", default=None)
            cmd.add_argument("--expected-revision", type=int)
        if method in ("plugin.enable", "plugin.disable"):
            cmd.add_argument("--now", action="store_true")
    logs = sub["service"].add_parser("logs")
    logs.add_argument("--plugin")
    logs.add_argument("-f", "--follow", action="store_true")
    maint = groups.add_parser("maintenance").add_subparsers(
        dest="action", required=True
    )
    maint.add_parser("backup")
    maint.add_parser("migrate")
    maint.add_parser("check").add_argument("--snapshot")
    restore = maint.add_parser("restore")
    restore.add_argument("snapshot")
    restore.add_argument("--confirm", required=True)
    return p


async def execute(args, cfg):
    method = f"{args.group}.{args.action}"
    values = {k: getattr(args, k) for k in ARGS[method].split()}
    for k in ("plan", "allow_restart", "now"):
        if getattr(args, k, False):
            values[k] = True
    rid = getattr(args, "request_id", None) or str(uuid.uuid4())
    req = {"v": 1, "request_id": rid, "method": method, "params": values}
    if getattr(args, "expected_revision", None) is not None:
        req["expected_revision"] = args.expected_revision
    try:
        result = await request(cfg.sock, req)
    except Fault as exc:
        if method not in READ and exc.code == "service_unavailable":
            raise Fault(
                exc.code, f"{exc}; retry with --request-id {rid}"
            ) from exc
        raise
    if method in READ or args.plan or args.no_wait:
        return result
    deadline = time.monotonic() + 120
    while result["phase"] not in (
        "succeeded",
        "failed",
        "maintenance_required",
    ):
        require(
            time.monotonic() < deadline,
            f"Result unknown; operation show {result['op_id']}",
            "service_unavailable",
        )
        await asyncio.sleep(0.4)
        try:
            result = await request(
                cfg.sock,
                {
                    "v": 1,
                    "method": "operation.show",
                    "request_id": str(uuid.uuid4()),
                    "params": {"op_id": result["op_id"]},
                },
            )
        except Fault as exc:
            if exc.code not in ("service_unavailable", "busy"):
                raise
    require(
        result["phase"] == "succeeded",
        f"Operation {result['op_id']}: {result['error']}",
        "operation_failed",
    )
    return result


def output(data, machine):
    if machine:
        print(json.dumps({"ok": True, "result": data}, ensure_ascii=False))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
