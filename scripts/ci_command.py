"""Expose a bounded failure annotation when public Actions log download needs authentication."""
from __future__ import annotations

import subprocess
import sys

from backend.app.security.redaction import redact_sensitive_text


def main() -> int:
    if len(sys.argv) < 2:
        raise ValueError("A CI command is required")
    result = subprocess.run(sys.argv[1:], capture_output=True, text=True, check=False)
    print(redact_sensitive_text(result.stdout))
    print(redact_sensitive_text(result.stderr), file=sys.stderr)
    if result.returncode:
        # Keep public failure summaries bounded and redact before truncating sensitive values.
        detail = redact_sensitive_text(result.stderr or result.stdout)[-4000:]
        detail = detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=CI validation failed::{detail}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
