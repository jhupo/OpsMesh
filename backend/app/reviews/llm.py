from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from backend.app.model_providers.service_models import ResolvedModelProvider
from backend.app.security.redaction import redact_sensitive_payload


@dataclass(frozen=True)
class LlmReviewResult:
    required: bool
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]


class LlmResourceReviewer:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds

    def review(
        self,
        *,
        provider: ResolvedModelProvider,
        resource_type: str,
        resource: dict[str, object],
        static_signals: dict[str, object],
        timeout_seconds: float | None = None,
    ) -> LlmReviewResult:
        if not provider.api_key:
            raise ValueError("Review model provider is missing api_key")
        payload = {
            "model": provider.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "resource_type": resource_type,
                            "resource": redact_sensitive_payload(resource),
                            "static_signals": redact_sensitive_payload(static_signals),
                        },
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                },
            ],
        }
        try:
            response = httpx.post(
                _chat_completions_url(provider.base_url),
                headers={
                    "Authorization": f"Bearer {provider.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout_seconds or self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = _message_content(body)
            data = json.loads(content)
            return _parse_review_result(data)
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Review model request failed") from exc


_SYSTEM_PROMPT = """
You are a senior security and product reviewer for a multi-agent workspace platform.
Review newly created employees, skills, MCP servers, and MCP tool permissions before activation.

Return ONLY a JSON object with:
{
  "verdict": "approve" | "needs_admin_review" | "reject",
  "risk_level": "low" | "medium" | "high" | "critical",
  "reasons": ["short_machine_readable_reason"],
  "findings": [
    {
      "severity": "low" | "medium" | "high" | "critical",
      "category": "security|privacy|execution|permissions|quality|operations",
      "message": "concise human-readable finding"
    }
  ],
  "recommendation": "concise operator recommendation"
}

Require admin review for dangerous execution, broad filesystem/network access,
deletion/destructive tools, credential exfiltration risk, approval bypass,
production deployment control, or unclear high-impact authority.
Approve normal scoped tools, authenticated remote MCP servers, and runtime tool
permissions that already require per-run approval, unless the resource grants
broad dangerous capability.
Do not reject unless the resource is clearly malicious or impossible to govern safely.
""".strip()


def _chat_completions_url(base_url: str | None) -> str:
    root = (base_url or "https://api.openai.com/v1").rstrip("/")
    if root.endswith("/chat/completions"):
        return root
    return f"{root}/chat/completions"


def _message_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Review provider returned no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("Review provider returned invalid choice")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("Review provider returned invalid message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Review provider returned empty content")
    return content


def _parse_review_result(data: object) -> LlmReviewResult:
    if not isinstance(data, dict):
        raise ValueError("Review provider returned non-object JSON")
    risk_level = _risk_level(data.get("risk_level"))
    verdict = str(data.get("verdict") or "").lower().strip()
    reasons = _string_list(data.get("reasons"))
    findings = _findings(data.get("findings"))
    recommendation = data.get("recommendation")
    if verdict not in {"approve", "needs_admin_review", "reject"}:
        verdict = "needs_admin_review"
        if "llm_review.unknown_verdict_requires_admin" not in reasons:
            reasons.insert(0, "llm_review.unknown_verdict_requires_admin")
        risk_level = _max_risk(risk_level, "high")
    signals: dict[str, object] = {
        "reviewer": "llm",
        "verdict": verdict,
        "findings": findings,
    }
    if isinstance(recommendation, str) and recommendation:
        signals["recommendation"] = recommendation[:1_000]
    required = verdict in {"needs_admin_review", "reject"} or risk_level in {"high", "critical"}
    if not reasons:
        reasons = ["llm_review.requires_admin_review" if required else "llm_review.approved"]
    return LlmReviewResult(
        required=required,
        risk_level=risk_level,
        reasons=reasons[:10],
        signals=signals,
    )


def _max_risk(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    left_risk = _risk_level(left)
    right_risk = _risk_level(right)
    return left_risk if order[left_risk] >= order[right_risk] else right_risk


def _risk_level(value: object) -> str:
    risk = str(value or "medium").lower().strip()
    return risk if risk in {"low", "medium", "high", "critical"} else "medium"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:160] for item in value if isinstance(item, str) and item][:10]


def _findings(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    findings: list[dict[str, str]] = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "severity": _risk_level(item.get("severity")),
                "category": str(item.get("category") or "security")[:80],
                "message": str(item.get("message") or "")[:500],
            }
        )
    return findings
