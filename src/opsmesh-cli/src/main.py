import asyncio
import json
import logging
import os
import signal
import sys

from . import command
from .config import Fault, Settings, require


def serve(cfg):
    from . import log, socket
    from .plugin import runtime
    from .plugin.manager import Manager
    from .storage.filesystem import directory, exclusive, remove

    require(
        sys.platform == "linux" and os.getuid() != 0,
        "Run the Linux daemon as an unprivileged user",
        "permission_denied",
    )
    logger = log.configure(cfg.logs)
    with exclusive(cfg.data):
        require(
            not (cfg.data / "restore.pending").exists(),
            "Resume offline restore first",
            "maintenance_required",
        )
        directory(cfg.run)
        manager = Manager(cfg)
        pending = manager.db.pending()
        staged = cfg.data / "staging"
        if staged.is_dir():
            for x in staged.iterdir():
                if not pending or pending["id"] != x.name:
                    remove(x)
        records, selected = manager.advance()
        server = socket.Server(
            manager, lambda: os.kill(os.getpid(), signal.SIGTERM)
        )
        started = False
        retry = None

        def retry_cleanup():
            if manager.lock.acquire(False):
                try:
                    row = manager.db.pending()
                    if row and row['phase'] == 'cleanup':
                        manager.cleanup(row)
                finally:
                    manager.lock.release()

        async def cleanup():
            while True:
                await asyncio.sleep(10)
                await asyncio.to_thread(retry_cleanup)

        async def ready():
            nonlocal started, retry
            await server.start()
            manager.ready({n: records[n]["version"] for n in selected})
            socket.notify("READY=1")
            started = True
            retry = asyncio.create_task(cleanup())
            logger.info(
                "Ready instance=%s plugins=%s",
                manager.instance,
                sorted(selected),
            )

        async def shutdown():
            socket.notify("STOPPING=1")
            if retry:
                retry.cancel()
            await server.close()

        try:
            runtime.run(manager, records, selected, ready, shutdown)
        except Exception:
            logger.exception("Framework initialization or execution failed")
            if started:
                raise
        finally:
            if server.server:
                cfg.sock.unlink(missing_ok=True)
        if not started:
            op = manager.db.pending()
            if op and op["phase"] == "trying":
                return 75
            if op and op["phase"] == "fallback_trying":
                manager.db.phase(
                    op["id"],
                    "maintenance_required",
                    "Previous runtime also failed readiness",
                )
            return 78
        return 75 if manager.restart else 0


def main(argv=None):
    args = command.parser().parse_args(argv)
    try:
        cfg = Settings.load(args.config)
        if args.group == "serve":
            return serve(cfg)
        if args.group == "maintenance":
            from .maintenance import maintain

            result = maintain(cfg, args)
        elif args.group == "service" and args.action == "logs":
            from .log import lines

            while lines(cfg.logs / "opsmeshd.log", args.plugin, args.follow):
                pass
            return 0
        else:
            result = asyncio.run(command.execute(args, cfg))
        command.output(result, args.json)
        return 0
    except KeyboardInterrupt:
        return 130
    except (Fault, OSError, ValueError) as exc:
        code = getattr(
            exc,
            "code",
            "invalid_request" if isinstance(exc, ValueError) else "internal",
        )
        error = {"ok": False, "error": {"code": code, "message": str(exc)}}
        print(
            json.dumps(error) if args.json else str(exc),
            file=sys.stdout if args.json else sys.stderr,
        )
        if args.group == "serve":
            logging.getLogger("opsmesh.service").error("%s", exc)
            return 78
        return {
            "invalid_request": 2,
            "not_found": 3,
            "permission_denied": 4,
            "conflict": 5,
            "busy": 5,
            "dependency_conflict": 5,
            "service_unavailable": 6,
        }.get(code, 1)
