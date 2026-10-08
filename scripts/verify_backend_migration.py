"""Compare wire/storage contracts with an immutable Git revision in fresh processes."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = r'''
import json
import sys
from contextlib import redirect_stdout
from importlib import import_module
from pathlib import Path
from uuid import UUID

with redirect_stdout(sys.stderr):
    from backend.app.main import app
    from backend.app.bootstrap.models import register_models
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable
    current = Path("backend/app/runtime/queues/contracts.py").is_file()
    jobs = import_module("backend.app.runtime.queues.contracts" if current
                         else "backend.app.runtime.workers.contracts")
    metadata = register_models()
    dialect = postgresql.dialect()
    payloads = {}
    for kind in jobs.JobType:
        job = jobs.JobPayload(
            job_id=UUID(int=1), workspace_id=UUID(int=2), resource_id=UUID(int=3),
            job_type=kind, idempotency_key="migration-compatibility",
            created_at="2026-09-30T00:00:00Z",
        )
        payloads[kind.value] = job.model_dump(mode="json")
    handlers = import_module("backend.app.bootstrap.job_handlers" if current
                             else "backend.app.runtime.workers.registry")
    context_module = import_module("backend.app.runtime.queues.context" if current
                                   else "backend.app.runtime.workers.handlers.context")
    backend_module = import_module("backend.app.runtime.backends.factory" if current
                                   else "backend.app.runtime.environment.backends.factory")
    from sqlalchemy.orm import Session
    with Session() as session:
        context = context_module.WorkerJobHandlerContext(
            session=session, runtime_backends=backend_module.build_runtime_backend_registry(None),
        )
        registered = handlers._build_handler_registry(context)
        assert set(registered) == set(jobs.JobType), "Job registry does not cover the protocol"
        registry = {kind.value: type(handler).__name__ for kind, handler in registered.items()}
    snapshot = {
        "openapi": app.openapi(),
        "tables": {
            name: {
                "ddl": str(CreateTable(table).compile(dialect=dialect)),
                "indexes": sorted(str(CreateIndex(index).compile(dialect=dialect))
                                  for index in table.indexes),
            }
            for name, table in sorted(metadata.tables.items())
        },
        "job_schema": jobs.JobPayload.model_json_schema(),
        "job_payloads": payloads,
        "job_handlers": registry,
    }
print(json.dumps(snapshot, sort_keys=True))
'''


def snapshot(directory: Path) -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(directory)
    result = subprocess.run(
        [sys.executable, "-c", SNAPSHOT],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Contract snapshot failed in {directory}:\n{result.stderr}")
    return json.loads(result.stdout)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline_ref = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{args.baseline_ref}^{{commit}}"],
        cwd=ROOT, text=True,
    ).strip()
    archive = subprocess.check_output(["git", "archive", baseline_ref, "backend"], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix="opsmesh-backend-baseline-") as temporary:
        directory = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as package:
            package.extractall(directory, filter="data")
        before = snapshot(directory)
    after = snapshot(ROOT)
    results = {
        key: {
            "identical": before[key] == after[key],
            "baseline_sha256": digest(before[key]),
            "current_sha256": digest(after[key]),
        }
        for key in before
    }
    evidence = {"baseline_commit": baseline_ref, "contracts": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0 if all(value["identical"] for value in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
