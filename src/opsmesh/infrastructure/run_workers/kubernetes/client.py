"""Bounded namespace-scoped Kubernetes REST calls; credentials are read at request time."""

from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import quote

import httpx

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.run_workers.kubernetes.configuration import (
    KubernetesWorkerConfiguration,
)


class KubernetesAPI:
    def __init__(
        self, configuration: KubernetesWorkerConfiguration, client: httpx.AsyncClient | None = None
    ) -> None:
        self.configuration = configuration
        self._client = client
        self._owns_client = client is None
        namespace = quote(configuration.namespace, safe="")
        self.jobs_path = f"/apis/batch/v1/namespaces/{namespace}/jobs"
        self.pods_path = f"/api/v1/namespaces/{namespace}/pods"

    def _connection(self) -> httpx.AsyncClient:
        if self._client is None:
            try:
                tls = ssl.create_default_context(cafile=self.configuration.ca_file)
            except (OSError, ssl.SSLError) as error:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Kubernetes CA certificate unavailable"
                ) from error
            self._client = httpx.AsyncClient(
                base_url=self.configuration.api_url.rstrip("/"),
                verify=tls,
                timeout=self.configuration.api_timeout_seconds,
                follow_redirects=False,
            )
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        try:
            token = self.configuration.token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise OpsMeshError(
                "DEPENDENCY_UNAVAILABLE", "Kubernetes service token unavailable"
            ) from error
        if not token:
            raise OpsMeshError("CONFIG_INVALID", "Kubernetes service token is empty")
        try:
            response = await self._connection().request(
                method, path, json=body, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as error:
            raise OpsMeshError(
                "KUBERNETES_UNAVAILABLE", "Kubernetes API request did not complete", retryable=True
            ) from error
        if response.status_code in {404, 409} or response.is_success:
            return response
        raise OpsMeshError(
            "KUBERNETES_FORBIDDEN"
            if response.status_code in {401, 403}
            else "KUBERNETES_API_ERROR",
            "Kubernetes API rejected the worker operation",
            retryable=response.status_code == 429 or response.status_code >= 500,
            details={"status_code": response.status_code, "operation": method},
        )

    async def get_job(self, name: str) -> dict[str, Any] | None:
        response = await self.request("GET", f"{self.jobs_path}/{quote(name, safe='')}")
        return None if response.status_code == 404 else self._object(response)

    async def create_job(self, specification: dict[str, Any]) -> dict[str, Any]:
        name = specification["metadata"]["name"]
        try:
            response = await self.request("POST", self.jobs_path, body=specification)
        except OpsMeshError as error:
            if error.retryable and (existing := await self.get_job(name)) is not None:
                return existing  # A lost create response must not cause a second logical Job.
            raise
        if response.status_code == 409:
            existing = await self.get_job(name)
            if existing is not None:
                return existing
            raise OpsMeshError("CONFLICT", "Kubernetes Job changed during creation", retryable=True)
        return self._object(response)

    async def _list(self, path: str, selector: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = ""
        while True:
            response = await self.request(
                "GET",
                path,
                params={
                    "labelSelector": selector,
                    "limit": 100,
                    "continue": cursor,
                },
            )
            value = self._object(response)
            items.extend(value.get("items", []))
            cursor = value.get("metadata", {}).get("continue", "")
            if not cursor:
                return items

    async def list_jobs(self, selector: str) -> list[dict[str, Any]]:
        return await self._list(self.jobs_path, selector)

    async def pods(self, uid: str) -> list[dict[str, Any]]:
        candidates = await self._list(self.pods_path, f"batch.kubernetes.io/controller-uid={uid}")
        return [
            pod
            for pod in candidates
            if any(
                owner.get("uid") == uid and owner.get("kind") == "Job"
                for owner in pod.get("metadata", {}).get("ownerReferences", [])
            )
        ]

    async def logs(self, pod_name: str) -> str:
        response = await self.request(
            "GET",
            f"{self.pods_path}/{quote(pod_name, safe='')}/log",
            params={
                "container": "run",
                "timestamps": "true",
                "tailLines": 10000,
                "limitBytes": self.configuration.log_limit_bytes,
            },
        )
        if response.status_code == 404:
            return "[worker pod log no longer available]\n"
        return response.text[: self.configuration.log_limit_bytes]

    async def delete_job(self, name: str, uid: str) -> None:
        response = await self.request(
            "DELETE",
            f"{self.jobs_path}/{quote(name, safe='')}",
            body={
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "propagationPolicy": "Foreground",
                "preconditions": {"uid": uid},
                "gracePeriodSeconds": self.configuration.termination_grace_seconds,
            },
        )
        if response.status_code == 409:
            raise OpsMeshError("OWNERSHIP_LOST", "Kubernetes Job UID changed before deletion")

    @staticmethod
    def _object(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError as error:
            raise OpsMeshError(
                "KUBERNETES_API_ERROR", "Kubernetes returned invalid JSON"
            ) from error
        if not isinstance(value, dict):
            raise OpsMeshError("KUBERNETES_API_ERROR", "Kubernetes returned a non-object response")
        return value

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
