"""Expose a bounded failure annotation when public Actions log download needs authentication."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from backend.app.security.redaction import redact_text_fragments


def main() -> int:
    if len(sys.argv) < 2:
        raise ValueError("A CI command is required")
    result = subprocess.run(
        sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False
    )
    output = redact_text_fragments(result.stdout)
    print(output)
    if report_path := os.environ.get("OPSMESH_CI_REPORT_PATH"):
        report = Path(report_path)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(output, encoding="utf-8")
    if result.returncode:
        # Keep public failure summaries bounded and redact before truncating sensitive values.
        detail = output[-4000:]
        detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=CI validation failed::{detail}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
