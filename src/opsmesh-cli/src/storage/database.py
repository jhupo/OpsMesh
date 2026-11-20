import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from ..config import require


class Database:
    def __init__(self, root: Path):
        self.path = root / "state.sqlite3"
        existed = self.path.exists()
        require(
            existed or not (root / "plugins").exists(),
            "Database missing from existing installation",
            "maintenance_required",
        )
        with self.tx() as db:
            if not existed:
                db.executescript("""
                    BEGIN;
                    CREATE TABLE plugins (id TEXT PRIMARY KEY, record TEXT NOT NULL);
                    CREATE TABLE operations (id TEXT PRIMARY KEY, uid INTEGER NOT NULL, request TEXT NOT NULL,
                        digest TEXT NOT NULL, phase TEXT NOT NULL, plan TEXT NOT NULL, error TEXT,
                        UNIQUE(uid,request));
                    CREATE TABLE state (revision INTEGER NOT NULL);
                    INSERT INTO state VALUES(0);
                    PRAGMA user_version=2;
                    COMMIT;
                """)
            require(
                db.execute("PRAGMA user_version").fetchone()[0] == 2,
                "Unsupported database layout; use the previous service for backup before offline migration",
                "maintenance_required",
            )

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def state(self):
        with self.tx() as db:
            db.execute("BEGIN")
            return db.execute("SELECT revision FROM state").fetchone()[0], {
                k: json.loads(v)
                for k, v in db.execute("SELECT id,record FROM plugins")
            }

    def lookup(self, where, args=()):
        with self.tx() as db:
            row = db.execute(
                "SELECT * FROM operations WHERE " + where, args
            ).fetchone()
            return (
                dict(row) | {"plan": json.loads(row["plan"])} if row else None
            )

    def pending(self):
        return self.lookup("phase NOT IN ('succeeded','failed')")

    def insert(self, op, uid, request, digest, plan):
        with self.tx() as db:
            db.execute(
                "INSERT INTO operations VALUES(?,?,?,?,'prepared',?,NULL)",
                (op, uid, request, digest, json.dumps(plan)),
            )

    def phase(self, op, phase, error=None):
        with self.tx() as db:
            db.execute(
                "UPDATE operations SET phase=?,error=COALESCE(?,error) WHERE id=?",
                (phase, error, op),
            )

    def commit(self, op, plan):
        with self.tx() as db:
            if plan["record"] is None:
                db.execute("DELETE FROM plugins WHERE id=?", (plan["id"],))
            else:
                db.execute(
                    "INSERT OR REPLACE INTO plugins VALUES(?,?)",
                    (plan["id"], json.dumps(plan["record"])),
                )
            db.execute("UPDATE state SET revision=revision+1")
            db.execute(
                "UPDATE operations SET phase='cleanup' WHERE id=?", (op,)
            )
