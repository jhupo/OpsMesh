from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import uuid
from importlib import metadata

from packaging.version import Version

from ..config import require
from ..storage.database import Database
from ..storage.filesystem import Filesystem, remove
from . import package
from .dependencies import order

LOG = logging.getLogger("opsmesh.service")
ARGS = {
    "plugin.list": "",
    "plugin.status": "id",
    "service.status": "",
    "service.version": "",
    "operation.show": "op_id",
    "plugin.install": "wheel sha256",
    "plugin.upgrade": "id wheel sha256",
    "plugin.start": "id",
    "plugin.stop": "id",
    "plugin.enable": "id",
    "plugin.disable": "id",
    "plugin.uninstall": "id",
    "plugin.delete": "id confirm",
}
READ = {
    "plugin.list",
    "plugin.status",
    "service.status",
    "service.version",
    "operation.show",
}


def enabled(records):
    return {k for k, v in records.items() if v["enabled"]}


def summary(row):
    return {
        "op_id": row["id"],
        "phase": row["phase"],
        "action": row["plan"]["method"],
        "id": row["plan"]["id"],
        "error": row["error"],
        **(
            {"preserved": row["plan"]["preserved"]}
            if row["plan"].get("preserved")
            else {}
        ),
    }


class Manager:
    def __init__(self, config):
        self.config = config
        self.db, self.fs = Database(config.data), Filesystem(config.data)
        self.lock = threading.Lock()
        self.running = {}
        self.protected = set()
        self.instance = str(uuid.uuid4())
        self.restart = False

    def read(self, method, args):
        revision, records = self.db.state()
        if method == "service.version":
            return {
                "service": metadata.version("opsmesh-cli"),
                "framework": metadata.version("opsmesh"),
                "protocol": 1,
            }
        if method == "operation.show":
            row = self.db.lookup("id=?", (args["op_id"],))
            require(row, "Unknown operation", "not_found")
            return summary(row)
        if method == "service.status":
            pending = self.db.pending()
            return {
                "revision": revision,
                "instance": self.instance,
                "running": self.running,
                "pending": summary(pending) if pending else None,
            }

        def status(k):
            r = records[k]
            return {
                "id": k,
                "installed_version": r["version"],
                "distribution": r.get("distribution"),
                "running_version": self.running.get(k),
                "enabled": r["enabled"],
                "health": "unknown",
                "state": "running"
                if k in self.running
                else "stopped"
                if r["version"]
                else "retained",
            }

        if method == "plugin.list":
            return [status(k) for k in sorted(records)]
        name = args["id"]
        require(name in records, "Unknown plugin", "not_found")
        return status(name)

    def dispatch(self, uid, req):
        method, a = req["method"], req["params"]
        if method in READ:
            return self.read(method, a)
        fingerprint = hashlib.sha256(
            json.dumps(
                [method, a, req.get("expected_revision")], sort_keys=True
            ).encode()
        ).hexdigest()
        require(
            self.lock.acquire(False), "Another operation is preparing", "busy"
        )
        op = str(uuid.uuid4())
        try:
            original = self.db.lookup(
                "uid=? AND request=?", (uid, req["request_id"])
            )
            if original:
                require(
                    original["digest"] == fingerprint,
                    "Request ID reused with different parameters",
                    "conflict",
                )
                return summary(original)
            pending = self.db.pending()
            require(
                not pending,
                f"Unfinished operation: {pending and pending['id']}",
                "busy",
            )
            rev, records = self.db.state()
            require(
                req.get("expected_revision", rev) == rev,
                "Plan revision changed",
                "conflict",
            )
            plan = self.plan(method, a, records, op)
            if a.get("plan"):
                return {
                    "id": plan["id"],
                    "revision": rev,
                    "restart": plan["restart"],
                    "before": plan["before"],
                    "after": plan["after"],
                    "version": (plan["record"] or {}).get("version"),
                }
            require(
                not plan["restart"] or a.get("allow_restart"),
                "Whole-service restart requires --allow-restart",
                "conflict",
            )
            self.db.insert(op, uid, req["request_id"], fingerprint, plan)
            LOG.info(
                "Accepted %s %s op=%s",
                method,
                plan["id"],
                op,
                extra={"plugin_id": plan["id"]},
            )
            if plan["restart"]:
                self.restart = True
            else:
                self.advance()
            return summary(self.db.lookup("id=?", (op,)))
        finally:
            try:
                if not self.db.lookup("id=?", (op,)):
                    remove(self.fs.work(op))
            finally:
                self.lock.release()

    def plan(self, method, a, records, op):
        name = a.get("id")
        dry = a.get("plan", False)
        install = method in ("plugin.install", "plugin.upgrade")
        item = None
        if install:
            item = package.inspect(
                self.config.inbox,
                a["wheel"],
                a["sha256"],
                None if dry else self.fs.work(op) / "new",
                plugin_root=self.config.data / "plugins",
            )
            incoming = item["module"]
            require(name in (None, incoming), "Wheel plugin ID mismatch")
            name = incoming
        old = records.get(name)
        r = copy.deepcopy(
            old
            or {
                "version": None,
                "enabled": False,
                "files": {},
                "requires": [],
                "digest": None,
            }
        )
        previous = r["digest"]
        before = set(self.running)
        after = before.copy()
        if method != "plugin.install":
            require(
                old and (r["version"] or method == "plugin.delete"),
                "Plugin is not installed",
                "not_found",
            )
        if name in self.config.protected or name in self.protected:
            require(
                method
                not in (
                    "plugin.stop",
                    "plugin.disable",
                    "plugin.uninstall",
                    "plugin.delete",
                    "plugin.upgrade",
                ),
                "Protected plugin",
                "conflict",
            )
        replace = False
        owned = dict(r["files"])
        preserved = []
        if install:
            require(
                not r.get("distribution")
                or item["distribution"] == r["distribution"],
                "Different distribution owns this plugin ID",
                "conflict",
            )
            if method == "plugin.install" and r["version"]:
                require(
                    previous == item["digest"],
                    "Already installed; use upgrade",
                    "conflict",
                )
            if r["version"] and previous != item["digest"]:
                require(
                    Version(item["version"]) > Version(r["version"]),
                    "Version must increase",
                    "conflict",
                )
            replace = previous != item["digest"]
            if replace and name in before:
                require(
                    name in self.config.restart_safe,
                    "Stop, back up and upgrade offline-only plugin first",
                    "conflict",
                )
            r.update(item)
            self.fs.preflight(name, owned, r["files"])
        elif method in (
            "plugin.enable",
            "plugin.disable",
            "plugin.start",
            "plugin.stop",
        ):
            on = method in ("plugin.enable", "plugin.start")
            if method in ("plugin.enable", "plugin.disable"):
                r["enabled"] = on
            if method in ("plugin.start", "plugin.stop") or a.get("now"):
                (after.add if on else after.discard)(name)
        elif method == "plugin.uninstall":
            require(
                name not in before and not r["enabled"],
                "Stop and disable before uninstall",
                "conflict",
            )
            owned, preserved = self.fs.removable(name, owned)
            replace = True
            r.update(version=None, digest=None, requires=[], files={})
        elif method == "plugin.delete":
            require(
                not r["version"] and a["confirm"] == name,
                "Uninstall first and confirm the ID",
                "conflict",
            )
            r = None
        target = dict(records)
        if r is None:
            del target[name]
        else:
            target[name] = r
        order(target, {k for k, v in target.items() if v["version"]})
        order(target, enabled(target))
        order(target, after)
        restart = before != after or (name in before and replace)
        return {
            "id": name,
            "method": method,
            "record": r,
            "previous_files": owned if replace else {},
            "preserved": preserved,
            "replace": replace,
            "before": sorted(before),
            "after": sorted(after),
            "restart": bool(restart),
        }

    def cleanup(self, row):
        try:
            self.fs.cleanup(row["id"], row["plan"])
            self.db.phase(row["id"], "succeeded")
            LOG.info(
                "Completed %s",
                row["id"],
                extra={"plugin_id": row["plan"]["id"]},
            )
        except OSError:
            LOG.exception("Cleanup pending for %s", row["id"])

    def advance(self):
        """Recover on a clean boot, or apply a non-runtime change. Never import from staging."""
        _, records = self.db.state()
        row = self.db.pending()
        if not row:
            return records, enabled(records)
        phase, plan, op = row["phase"], row["plan"], row["id"]
        if phase == "cleanup":
            self.cleanup(row)
            return records, enabled(records)
        require(
            phase not in ("fallback_trying", "maintenance_required"),
            "Failed recovery; offline maintenance required",
            "maintenance_required",
        )
        if phase in ("trying", "restoring"):
            self.db.phase(op, "restoring", "Candidate failed readiness")
            if plan["replace"]:
                self.fs.revert(op, plan)
            self.db.phase(op, "fallback_trying")
            return records, set(plan["before"])
        require(
            phase == "prepared",
            "Unknown recovery phase",
            "maintenance_required",
        )
        if plan["replace"]:
            self.fs.publish(op, plan)
        if plan["restart"]:
            self.db.phase(op, "trying")
            records[plan["id"]] = plan["record"]
            return records, set(plan["after"])
        self.db.commit(op, plan)
        self.cleanup(row)
        return self.db.state()[1], enabled(self.db.state()[1])

    def ready(self, versions):
        row = self.db.pending()
        if row and row["phase"] == "trying":
            self.db.commit(row["id"], row["plan"])
            self.cleanup(row)
        elif row and row["phase"] == "fallback_trying":
            remove(self.fs.work(row["id"]))
            self.db.phase(row["id"], "failed")
        self.running = versions
