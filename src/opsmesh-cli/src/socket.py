import asyncio
import json
import logging
import os
import socket
import struct
import uuid
from zipfile import BadZipFile

from .config import Fault, identifier, require
from .plugin.manager import ARGS, READ

LIMIT = 1 << 20


def frame(value):
    body = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    require(len(body) <= LIMIT, "Message too large")
    return struct.pack("!I", len(body)) + body


def unique(items):
    result = dict(items)
    require(len(result) == len(items), "Duplicate object keys")
    return result


async def receive(reader):
    length = struct.unpack("!I", await reader.readexactly(4))[0]
    require(0 < length <= LIMIT, "Invalid frame size")
    return json.loads(
        (await reader.readexactly(length)).decode(), object_pairs_hook=unique
    )


def validate(req):
    require(
        type(req) is dict
        and req.keys()
        <= {"v", "request_id", "method", "params", "expected_revision"}
        and type(req.get("v")) is int
        and req["v"] == 1,
        "Invalid protocol version or envelope",
    )
    require(
        isinstance(req.get("request_id"), str)
        and str(uuid.UUID(req["request_id"])) == req["request_id"],
        "Expected request UUID",
    )
    method, a = req.get("method"), req.get("params")
    require(
        isinstance(method, str) and method in ARGS and type(a) is dict,
        "Unknown method or invalid parameters",
    )
    fields = set(ARGS[method].split())
    extra = {"plan", "allow_restart"} if method not in READ else set()
    if method in ("plugin.enable", "plugin.disable"):
        extra.add("now")
    require(fields <= a.keys() <= fields | extra, "Invalid method parameters")
    require(
        all(type(a[k]) is str for k in fields)
        and all(type(a[k]) is bool for k in extra & a.keys()),
        "Invalid parameter type",
    )
    if "expected_revision" in req:
        require(
            type(req["expected_revision"]) is int
            and req["expected_revision"] >= 0,
            "Invalid revision",
        )
    if "id" in a:
        identifier(a["id"])
    return req


async def request(path, req):
    try:
        r, w = await asyncio.wait_for(
            asyncio.open_unix_connection(str(path)), 3
        )
        try:
            w.write(frame(req))
            await w.drain()
            reply = await asyncio.wait_for(receive(r), 120)
        finally:
            w.close()
            await w.wait_closed()
    except PermissionError as exc:
        raise Fault(
            "permission_denied", "Management socket access denied"
        ) from exc
    except (
        OSError,
        TimeoutError,
        asyncio.IncompleteReadError,
        AttributeError,
    ) as exc:
        raise Fault(
            "service_unavailable",
            "Management socket unavailable or result unknown",
        ) from exc
    if not reply["ok"]:
        raise Fault(reply["error"]["code"], reply["error"]["message"])
    return reply["result"]


class Server:
    def __init__(self, manager, stop):
        self.manager, self.stop = manager, stop
        self.server = None
        self.clients = set()
        self.stopping = False

    async def start(self):
        import grp

        cfg = self.manager.config
        path = cfg.sock
        if path.exists():
            require(path.is_socket(), "Non-socket occupies control path")
            path.unlink()
        self.server = await asyncio.start_unix_server(
            self.connection, path=str(path), start_serving=False
        )
        if cfg.group:
            os.chown(path, -1, grp.getgrnam(cfg.group).gr_gid)
        path.chmod(0o660)
        await self.server.start_serving()

    async def connection(self, r, w):
        task = asyncio.current_task()
        if self.stopping or len(self.clients) >= 32:
            w.close()
            return
        self.clients.add(task)
        try:
            try:
                peer = w.get_extra_info("socket").getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, 12
                )
                _, uid, _ = struct.unpack("3i", peer)
                req = validate(await asyncio.wait_for(receive(r), 5))
                require(not self.stopping, "Service stopping", "busy")
                result = await asyncio.to_thread(
                    self.manager.dispatch, uid, req
                )
                reply = {"v": 1, "ok": True, "result": result}
            except (
                Fault,
                ValueError,
                KeyError,
                TypeError,
                BadZipFile,
                UnicodeError,
            ) as exc:
                reply = {
                    "v": 1,
                    "ok": False,
                    "error": {
                        "code": getattr(exc, "code", "invalid_request"),
                        "message": str(exc),
                    },
                }
            except (asyncio.IncompleteReadError, TimeoutError):
                return
            except Exception:
                logging.getLogger("opsmesh.service").exception(
                    "Management operation failed"
                )
                reply = {
                    "v": 1,
                    "ok": False,
                    "error": {
                        "code": "internal",
                        "message": "Operation failed; check service logs",
                    },
                }
            w.write(frame(reply))
            await asyncio.wait_for(w.drain(), 5)
        finally:
            w.close()
            self.clients.discard(task)
            if self.manager.restart and not self.stopping and not self.clients:
                self.stopping = True
                self.stop()

    async def close(self):
        self.stopping = True
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            await asyncio.gather(*tuple(self.clients), return_exceptions=True)
            self.manager.config.sock.unlink(missing_ok=True)


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(
                message.encode(),
                "\0" + address[1:] if address.startswith("@") else address,
            )
