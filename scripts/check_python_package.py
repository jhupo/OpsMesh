"""Verify the wheel contains exactly the source package and runs outside the checkout."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    wheel = parser.parse_args().wheel.resolve()
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
    expected = {
        path.relative_to(ROOT / "src").as_posix() for path in (ROOT / "src/opsmesh").rglob("*.py")
    }
    actual = {name for name in names if name.endswith(".py")}
    if expected != actual:
        raise ValueError(
            f"Wheel source mismatch: missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    if any(not (name.startswith("opsmesh/") or ".dist-info/" in name) for name in names):
        raise ValueError("Wheel contains files outside the application package and metadata")
    with ZipFile(wheel) as archive:
        for name in sorted(expected):
            if archive.read(name) != (ROOT / "src" / name).read_bytes():
                raise ValueError(f"Wheel contains stale source: {name}")
    code = """
import importlib
import os
import sys
os.environ.update(OPSMESH_ENVIRONMENT='test', OPSMESH_TRACING_ENABLED='false',
                  OPSMESH_OTEL_LOGS_ENABLED='false', OPSMESH_LOG_LEVEL='ERROR')
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from opsmesh.bootstrap.api import create_app
from opsmesh.bootstrap.models import register_models
for name in ('opsmesh.bootstrap.worker',):
    importlib.import_module(name)
app = create_app()
# No lifespan: package integrity requires no external infrastructure.
# Startup/readiness is exercised separately on real PG/Redis/Temporal.
client = TestClient(app)
assert client.get('/api/v1/health').status_code == 200
assert '/api/v1/auth/login' in app.openapi()['paths']
client.close()
assert app.openapi()['paths']
for name, module in list(sys.modules.items()):
    if name == 'opsmesh' or name.startswith('opsmesh.'):
        assert module.__file__.startswith(sys.argv[1]), (name, module.__file__)
model_count = len(register_models().tables)
assert model_count > 0
print(f'Isolated wheel import, API, Worker CLI and {model_count} models verified')
"""
    with tempfile.TemporaryDirectory(prefix="opsmesh-wheel-check-") as directory:
        subprocess.run(
            [sys.executable, "-I", "-c", code, str(wheel)], cwd=directory, check=True, timeout=90
        )
    print(f"Verified {len(expected)} source files in {wheel.name}")


if __name__ == "__main__":
    main()
