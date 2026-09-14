import ast
import subprocess
import sys
from pathlib import Path

from backend.app.bootstrap.models import register_models

ROOT = Path(__file__).resolve().parents[2]


def test_model_registration_is_idempotent() -> None:
    metadata = register_models()
    tables = dict(metadata.tables)
    assert register_models() is metadata
    assert dict(metadata.tables) == tables


def test_database_import_does_not_load_business_models() -> None:
    _fresh_process(
        "import sys\n"
        "import backend.app.core.db.session\n"
        "assert not any(name.startswith(('backend.app.domains.', "
        "'backend.app.runtime.', 'backend.app.api.')) for name in sys.modules)\n"
    )


def test_explicit_registration_covers_every_declared_table() -> None:
    expected = set()
    for path in (ROOT / "backend/app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "__tablename__"
                    for target in item.targets
                ):
                    expected.add(ast.literal_eval(item.value))
    assert expected
    _fresh_process(
        "from backend.app.bootstrap.models import register_models\n"
        "metadata = register_models()\n"
        f"expected = {sorted(expected)!r}\n"
        "assert set(metadata.tables) == set(expected), "
        "set(expected).symmetric_difference(metadata.tables)\n"
    )


def _fresh_process(code: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
