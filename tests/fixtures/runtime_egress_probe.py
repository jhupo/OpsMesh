"""Offline Runtime probe; requires an isolated Linux container with NET_ADMIN."""

import json
import pwd
import subprocess
import sys
import time
from pathlib import Path

from opsmesh_runtime.egress_policy import FIRST_PROXY_PORT


def exercise() -> dict[str, object]:
    process = subprocess.Popen(
        [sys.executable, "-m", "opsmesh_runtime.execution_control", "--initialize"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not Path("/run/squid/squid.pid").exists():
            assert process.poll() is None, "Runtime supervisor did not start"
            assert time.monotonic() < deadline, "Runtime supervisor readiness timed out"
            time.sleep(0.1)
        subprocess.run(
            ["squid", "-k", "parse", "-f", "/run/squid/opsmesh.conf"],
            check=True,
            capture_output=True,
        )
        request = (
            "import http.client; "
            f'c=http.client.HTTPConnection("127.0.0.1", {FIRST_PROXY_PORT}, timeout=5); '
            'c.request("GET", "http://127.0.0.1/egress-canary?password=egress-canary"); '
            "r=c.getresponse(); assert r.status==403; r.read(); c.close()"
        )
        time.sleep(0.2)
        subprocess.run(
            [sys.executable, "-c", request], user=pwd.getpwnam("proxy").pw_uid, check=True
        )
        time.sleep(0.2)
    finally:
        process.terminate()
        try:
            output, errors = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, errors = process.communicate()
    assert "egress-canary" not in output + errors
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert any(
        record["event"] == "runtime.egress" and record["http_status"] == 403 for record in records
    )
    return {"egress_denial": "passed", "log_secret_canary": "passed", "external_calls": 0}


if __name__ == "__main__":
    print(json.dumps(exercise()))
