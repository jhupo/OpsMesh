from __future__ import annotations

import json
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.runtime.environment.contracts import (
    DockerRuntimeClient,
    RuntimeCommandInputFile,
)
from backend.app.runtime.environment.manager import RuntimeManager
from backend.app.runtime.environment.models import WorkspaceRuntime

MAX_FETCH_TIMEOUT_SECONDS = 30
_FETCH_SCRIPT = """
import json
import sys
import urllib.error
import ipaddress
import socket
import urllib.parse
import urllib.request


def assert_safe_url(value):
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("URL must use https")
    for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(item[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise ValueError("URL resolves to a private network address")


class HttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("redirected URL must use https")
        assert_safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


request_path = sys.argv[sys.argv.index("--request") + 1]
with open(request_path, encoding="utf-8") as request_file:
    request = json.load(request_file)
url = request["url"]
assert_safe_url(url)
output_path = request["output_path"]
max_bytes = request["max_bytes"]
opener = urllib.request.build_opener(HttpsRedirectHandler)
http_request = urllib.request.Request(
    url,
    headers={"User-Agent": "OpsMesh-KnowledgeFetcher/1"},
)
try:
    with opener.open(http_request, timeout=30) as response:
        content = response.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("response exceeds the configured byte limit")
    with open(output_path, "wb") as output_file:
        output_file.write(content)
except (OSError, ValueError, urllib.error.URLError) as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1) from exc
""".strip()


class RuntimeUrlFetchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeUrlFetcher:
    """Fetch an HTTPS source through an already-running, network-enabled runtime."""

    def __init__(self, session: Session, docker_client: DockerRuntimeClient) -> None:
        self._session = session
        self._docker = docker_client
        self._manager = RuntimeManager(session, docker_client)

    def fetch(
        self,
        *,
        workspace_id: UUID,
        runtime_id: UUID,
        url: str,
        max_bytes: int,
    ) -> bytes:
        runtime = self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == runtime_id,
                WorkspaceRuntime.status == "running",
            )
        )
        if runtime is None:
            raise RuntimeUrlFetchError(
                "fetch_runtime_not_running",
                "Knowledge fetch runtime is not running",
            )
        if not runtime.docker_container_id:
            raise RuntimeUrlFetchError(
                "fetch_runtime_unavailable",
                "Knowledge fetch runtime has no managed container",
            )
        self._assert_network_policy(runtime, url)
        output_path = f"/tmp/opsmesh-knowledge-fetch-{uuid4().hex}.bin"
        request_payload = json.dumps(
            {"url": url, "output_path": output_path, "max_bytes": max_bytes},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            command = self._manager.execute_command(
                workspace_id=workspace_id,
                runtime=runtime,
                command=["python", "-c", _FETCH_SCRIPT],
                input_file=RuntimeCommandInputFile(
                    content=request_payload,
                    argument_name="--request",
                ),
                working_dir="/tmp",
            )
            if command.status != "completed" or command.exit_code != 0:
                raise RuntimeUrlFetchError(
                    "url_fetch_failed",
                    "Knowledge source URL fetch failed",
                )
            content = self._docker.copy_file_from_container(
                runtime.docker_container_id,
                output_path,
                max_bytes,
                MAX_FETCH_TIMEOUT_SECONDS,
            )
            if content is None:
                raise RuntimeUrlFetchError(
                    "url_fetch_output_missing",
                    "Knowledge source URL fetch produced no output",
                )
            return content
        except RuntimeUrlFetchError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeUrlFetchError(
                "url_fetch_failed",
                "Knowledge source URL fetch failed",
            ) from exc
        finally:
            self._cleanup_output(runtime, output_path, workspace_id)

    def _assert_network_policy(self, runtime: WorkspaceRuntime, url: str) -> None:
        policy = runtime.network_policy if isinstance(runtime.network_policy, dict) else {}
        mode = policy.get("mode")
        if policy.get("disabled") is True or mode in {None, "none", "disabled", "off"}:
            raise RuntimeUrlFetchError(
                "fetch_runtime_network_disabled",
                "Knowledge fetch runtime network access is disabled",
            )
        if mode != "restricted":
            return
        host = urlsplit(url).hostname
        allowed = policy.get("allowed_domains")
        if not host or not isinstance(allowed, list) or not _domain_allowed(host, allowed):
            raise RuntimeUrlFetchError(
                "fetch_runtime_domain_denied",
                "Knowledge source domain is not allowed by the fetch runtime",
            )

    def _cleanup_output(
        self,
        runtime: WorkspaceRuntime,
        output_path: str,
        workspace_id: UUID,
    ) -> None:
        try:
            self._manager.execute_command(
                workspace_id=workspace_id,
                runtime=runtime,
                command=["rm", "-f", output_path],
                working_dir="/tmp",
            )
        except Exception:
            return


def _domain_allowed(host: str, allowed_domains: list[object]) -> bool:
    normalized_host = host.lower().rstrip(".")
    for item in allowed_domains:
        if not isinstance(item, str):
            continue
        domain = item.lower().rstrip(".")
        if normalized_host == domain or normalized_host.endswith(f".{domain}"):
            return True
    return False
