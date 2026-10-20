"""Resolve every S1 reference to the final tree and detect stale final inventories.

This verifies file destinations and content identities, not semantic correctness.
External business packages remain explicitly unimplemented in this repository.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "docs/s14-source-map.csv"
TARGETS = ROOT / "docs/s14-target-inventory.csv"
FOLDED = {
    "governance/__init__.py": "shared/audit.py",
    "governance/access.py": "platform/audit/service.py;identity/auth/dependencies.py",
    "main.py": "bootstrap/api.py",
    "bootstrap": "bootstrap/api.py;bootstrap/platform.py;bootstrap/resources.py",
    "bootstrap/events.py": "bootstrap/event_sources.py",
    "bootstrap/sandbox.py": "execution/runtime/sandbox.py;bootstrap/worker.py",
    "bootstrap/schedules.py": "platform/maintenance/activities.py",
    "bootstrap/storage.py": "bootstrap/resources.py;resources/storage/filesystem.py",
    "capabilities/channels/README.md": "capabilities/schemas.py;channels/models.py",
    "capabilities": "capabilities/lifecycle.py;capabilities/models.py",
    "capabilities/skills": (
        "orchestration/definitions/capabilities/service.py;capabilities/schemas.py"
    ),
    "governance/reviews": "platform/approvals/service.py;capabilities/publication.py",
    "orchestration/triggers": "execution/runs/schemas.py;orchestration/definitions/compilation.py",
    "workspaces/capabilities": "orchestration/definitions/capabilities/service.py",
    "execution (owner import callback)": "bootstrap/execution.py",
    "execution/runs/approvals.py": "execution/runs/activities.py;execution/runs/workflow.py",
    "execution/runtime/mcp_stdio.py": "execution/runtime/mcp.py",
    "orchestration/runs/contracts.py": "orchestration/contracts.py",
    "orchestration/runs/state.py": "orchestration/runs/models.py;orchestration/runs/steps.py",
    "platform/backups/archive.py": "platform/backups/service.py",
    "platform/backups/export.py": "platform/backups/activities.py;extensions/archives.py",
    "platform/backups/importers.py": "platform/backups/activities.py;extensions/archives.py",
    "platform/backups/restore.py": "platform/backups/activities.py;extensions/archives.py",
    "platform/backups/workflow.py": "platform/operations/workflow.py",
    "platform/credentials/activities.py": "bootstrap/platform.py;identity/auth/maintenance.py",
    "platform/credentials/service.py": "platform/credentials/routes.py;bootstrap/platform.py",
    "platform/credentials/workflow.py": "platform/operations/workflow.py",
    "platform/health/probes.py": "bootstrap/health.py",
    "platform/operations/resources.py": "platform/operations/admission.py;bootstrap/platform.py",
    "resources (owner import callback)": "bootstrap/resources.py",
    "workspaces (owner import callback)": "bootstrap/platform.py;identity/users/ownership.py",
    "workspaces/projects (owner import callback)": "bootstrap/compositions.py",
    "workspaces/__init__.py": "orchestration/definitions/__init__.py",
    "workspaces/models.py": "orchestration/configurations/models.py",
    "workspaces/routes.py": "orchestration/definitions/configuration_routes.py",
    "workspaces/schemas.py": "orchestration/definitions/schemas.py",
    "workspaces/service.py": "orchestration/definitions/configurations.py",
    "workspaces/projects/__init__.py": "orchestration/definitions/__init__.py",
    "workspaces/projects/activities.py": "orchestration/definitions/closing.py",
    "workspaces/projects/configuration.py": "orchestration/definitions/compilation.py",
    "workspaces/projects/execution.py": "orchestration/definitions/execution.py",
    "workspaces/projects/files.py": "orchestration/definitions/files.py",
    "workspaces/projects/models.py": "orchestration/configurations/models.py",
    "workspaces/projects/routes.py": "orchestration/definitions/configuration_routes.py",
    "workspaces/projects/schemas.py": "orchestration/definitions/schemas.py",
    "workspaces/projects/service.py": "orchestration/definitions/configurations.py",
    "workspaces/projects/versions.py": "orchestration/definitions/configurations.py",
    "workspaces/projects/workflow.py": "orchestration/definitions/close_workflow.py",
}
USER_OWNERS = {
    "governance/costs/": "identity/costs/",
    "governance/audit/": "platform/audit/",
    "governance/approvals/": "platform/approvals/",
    "governance/policies/": "platform/policies/",
    "workspaces/capabilities/": "orchestration/definitions/capabilities/",
    "workspaces/capacity/": "execution/capacity/",
    "workspaces/model_providers/": "execution/model_connections/",
    "workspaces/notifications/": "identity/notifications/",
}
CONFIGURATION_MEMBERS = (
    "orchestration/configurations/models.py;orchestration/configurations/service.py;"
    "orchestration/configurations/routes.py;orchestration/configurations/schemas.py"
)
CURRENT_FILES = {
    "bootstrap/event_sources.py": "bootstrap/source_notifications.py",
    "bootstrap/resources.py": "bootstrap/connections.py",
    "bootstrap/execution.py": "bootstrap/orchestration.py;bootstrap/worker.py",
    "bootstrap/compositions.py": "bootstrap/orchestration.py",
    "execution/__init__.py": "orchestration/__init__.py",
    "execution/contracts.py": "orchestration/contracts.py",
    "execution/runs/agents.py": "orchestration/execution/agents.py",
    "execution/runs/guardrails.py": "orchestration/execution/guardrails.py",
    "execution/runs/tools.py": "orchestration/execution/tools.py",
    "execution/runs/inference.py": "orchestration/runs/model_activities.py",
    "execution/runs/workflow.py": "orchestration/runs/agent_workflow.py",
    "execution/sessions/temporal.py": "orchestration/execution/session.py",
    "orchestration/runs/workflow.py": "orchestration/runs/flow_workflow.py",
    "orchestration/definitions/schema.py": "orchestration/definitions/schema_validation.py",
    "orchestration/definitions/configurations.py": "orchestration/configurations/service.py",
    "orchestration/definitions/configuration_routes.py": "orchestration/configurations/routes.py",
    "orchestration/definitions/execution.py": "orchestration/configurations/compilation.py",
    "shared/security/egress.py": "shared/security/url_validation.py",
    "shared/security/secrets.py": "shared/security/encryption.py",
}
CURRENT_PREFIXES = {
    "execution/runtime/": "orchestration/runtime/",
    "execution/sessions/": "orchestration/sessions/",
    "execution/runs/": "orchestration/runs/",
    "execution/model_connections/": "platform/model_connections/",
    "orchestration/definitions/capabilities/": "orchestration/configurations/bindings/",
}


def current_targets(target: str) -> list[str]:
    """Resolve approved structural moves without guessing from existing filenames."""
    if not target.startswith("src/opsmesh/"):
        return [target]
    relative = target.removeprefix("src/opsmesh/")
    if relative.startswith("execution/capacity/"):
        return ["src/opsmesh/identity/quotas/" + Path(relative).name]
    if relative.startswith("platform/backups/") or relative == "extensions/archives.py":
        return [
            "operator/opsmesh_operator/backups.py",
            "operator/opsmesh_operator/temporal_backup.py",
        ]
    if relative.startswith(("platform/credentials/", "platform/maintenance/")):
        return [
            "src/opsmesh/platform/operations/activities.py",
            "src/opsmesh/bootstrap/platform.py",
        ]
    if relative.startswith("platform/observability/"):
        return ["src/opsmesh/bootstrap/metrics.py", "src/opsmesh/platform/health/routes.py"]
    if relative.startswith("platform/settings/"):
        return ["src/opsmesh/config.py", "src/opsmesh/platform/policies/routes.py"]
    if relative.startswith("platform/audit/") and Path(relative).name in {
        "activities.py",
        "hashing.py",
        "workflow.py",
    }:
        return ["src/opsmesh/platform/audit/service.py"]
    if (
        relative.startswith("capabilities/")
        and relative.split("/")[-1] == "routes.py"
        and relative.split("/")[1] in {"mcp", "tools", "skills"}
    ):
        return ["src/opsmesh/capabilities/routes.py"]
    if (
        relative.startswith("capabilities/")
        and relative.split("/")[-1] == "service.py"
        and relative.split("/")[1] in {"mcp", "tools", "skills"}
    ):
        return ["src/opsmesh/" + relative.removesuffix("service.py") + "validation.py"]
    replacement = CURRENT_FILES.get(relative)
    if replacement:
        return ["src/opsmesh/" + item for item in replacement.split(";")]
    if relative.startswith("orchestration/definitions/") and Path(relative).name in {
        "closing.py",
        "close_workflow.py",
        "compilation.py",
        "files.py",
        "file_routes.py",
    }:
        return ["src/opsmesh/orchestration/configurations/" + Path(relative).name]
    for old, new in CURRENT_PREFIXES.items():
        if relative.startswith(old):
            result = new + relative.removeprefix(old)
            if result.endswith("/bindings/configuration.py"):
                result = result.removesuffix("configuration.py") + "validation.py"
            return ["src/opsmesh/" + result]
    return [target]


def digest(path: Path) -> str:
    # Normalize checkout line endings; GitHub Linux and Windows must agree.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def acceptance(target: str) -> str:
    if target in {
        "src/opsmesh/capabilities/publication.py",
        "src/opsmesh/platform/approvals/models.py",
    }:
        return "tests/platform;tests/capabilities/test_publication_review_routes.py"
    if target.startswith("tests/"):
        return target
    if target.startswith("src/opsmesh/"):
        feature = target.split("/")[2]
        if feature == "orchestration":
            return "tests/orchestration;tests/execution"
        candidate = f"tests/{feature}"
        return candidate if (ROOT / candidate).is_dir() else "tests/bootstrap;tests/engineering"
    if target.startswith("sdk/"):
        return "sdk/python/tests"
    if target.startswith("operator/"):
        return "operator/tests"
    return "tests/engineering;operator/tests;.github/workflows/v4-validation.yml"


def reference_rows() -> list[dict[str, str]]:
    with (ROOT / "docs/s1-reference-file-mapping.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    seen: set[tuple[str, str]] = set()
    result = []
    for row in rows:
        key = row["source_tree"], row["source_path"]
        if key in seen:
            raise ValueError(f"Duplicate reference entry: {key}")
        seen.add(key)
        source = ROOT / key[0] / "opsmesh" / key[1]
        if not source.is_file():
            raise ValueError(f"Reference file is missing: {source}")
        planned = row["v4_target"]
        targets, external = "", ""
        decision, reason = row["decision"], row["reason"]
        if key[1].startswith("platform/backups/") and planned != "delete":
            decision = "external_unimplemented"
            external = "extension-center:data-portability"
            reason = (
                "Workspace/business archive import and export are removed from the core; "
                "future portable business data belongs to an extension. Operator PostgreSQL "
                "and Temporal backups have a separate host-level scope and do not replace it"
            )
        elif planned.startswith("src/opsmesh/governance/costs/"):
            decision = "delete_currency_cost_control"
            reason = (
                "V4 0.8 removes monetary pricing and budgets; administrator model grants and "
                "identity quantity quotas replace admission, without retaining a cost ledger"
            )
        elif (
            planned.startswith(
                (
                    "src/opsmesh/platform/mail/",
                    "src/opsmesh/workspaces/notifications/",
                    "src/opsmesh/identity/notifications/",
                )
            )
            or planned == "src/opsmesh/shared/mail.py"
        ):
            decision = "external_unimplemented"
            external = "extension-center:notification-delivery"
            reason = (
                "V4 0.8 removes mail and notification business from the core; implement delivery "
                "in an independently installed extension; historical source is read-only"
            )
        elif planned.startswith("bundled/"):
            decision = "external_unimplemented"
            external = "extension-center:" + planned.removeprefix("bundled/")
            reason = (
                "User assigned business packages to a future separate extension-center repository; "
                "source retained read-only; no core implementation claimed"
            )
        elif planned == "delete":
            decision = "delete"
        else:
            relative = planned.removeprefix("src/opsmesh/")
            replacements = None
            if relative.startswith("governance/security_events/"):
                replacements = (
                    "platform/audit/models.py;platform/audit/service.py;platform/audit/routes.py"
                )
                decision = "unify_security_occurrences"
                reason = "Security events share the platform event chain; no second audit table"
            elif relative == "governance/approvals/targets.py":
                replacements = "shared/approvals.py"
                decision = "neutral_approval_contract"
                reason = (
                    "Immutable targets and snapshots are injected; no product imports platform ORM"
                )
            elif relative.startswith(("governance/reviews/", "governance/alerts/")):
                replacements = (
                    "shared/audit.py;platform/approvals/models.py;capabilities/publication.py"
                )
                decision = "replace_generic_governance_business"
                reason = (
                    "Generic core review/alert state removed; automatic business policy belongs to "
                    "the external extension center. Capability publication uses universal approvals"
                )
            elif relative.startswith(("capabilities/experts/", "capabilities/teams/")):
                replacements = (
                    "orchestration/definitions/models.py;orchestration/definitions/schema.py;"
                    "orchestration/definitions/graph.py;orchestration/definitions/service.py"
                )
                decision = "replace_with_user_composition"
                reason = (
                    "Dedicated expert/team catalogs removed by the 2026-10-19 contract; "
                    "users publish agent_graph/flow definitions and immutable revisions; "
                    "no legacy API, catalog or business strategy retained"
                )
            elif relative.startswith(("workspaces/members/", "workspaces/projects/members/")):
                replacements = CONFIGURATION_MEMBERS
                decision = "replace_with_configuration_invitation"
                reason = (
                    "Tenant/project membership removed; invitations grant configuration use "
                    "and explicit resource access, never another user's Session or history"
                )
            elif relative in FOLDED:
                replacements = FOLDED[relative]
                decision = "fold_into_owner"
                reason = (
                    "Final owner consolidates the planned helper in the listed implementation; "
                    "no compatibility wrapper; " + reason
                )
            else:
                for old, new in USER_OWNERS.items():
                    if relative.startswith(old):
                        replacements = new + relative.removeprefix(old)
                        decision = "reassign_to_user_owner"
                        reason = (
                            "2026-10-19 user ownership replaces workspace scope; explicit "
                            "configuration delegation preserves the actual execution principal; "
                            + reason
                        )
                        break
            targets = (
                ";".join("src/opsmesh/" + item for item in replacements.split(";"))
                if replacements
                else planned
            )
            previous = targets
            targets = ";".join(
                dict.fromkeys(
                    current for target in targets.split(";") for current in current_targets(target)
                )
            )
            if targets != previous:
                decision = "reassign_to_current_owner"
                reason = (
                    "V4 0.8 ownership and source tree supersede the earlier destination; " + reason
                )
            if relative.startswith("platform/observability/"):
                decision = "reduce_to_metrics_and_health"
                reason = (
                    "Tenant observability query APIs removed; core retains aggregate metrics "
                    "and health probes only, without another business query API"
                )
            elif relative.startswith("platform/settings/"):
                decision = "remove_mutable_settings_api"
                reason = (
                    "Global mutable settings API removed; deployment configuration and "
                    "operation policies retain separate explicit owners"
                )
            for target in targets.split(";"):
                if not (ROOT / target).is_file():
                    raise ValueError(f"Unresolved final destination: {source} -> {target}")
        result.append(
            {
                "source_tree": key[0],
                "source_path": key[1],
                "source_sha256": digest(source),
                "function": row["function"],
                "planned_target": planned,
                "final_targets": targets,
                "disposition": decision,
                "external_reference": external,
                "reason": reason,
                "acceptance": acceptance(targets.split(";")[0])
                if targets
                else "not applicable: deleted or externally deferred",
            }
        )
    actual = {
        (tree, path.relative_to(ROOT / tree / "opsmesh").as_posix())
        for tree in ("backup", "backup_2")
        for path in (ROOT / tree / "opsmesh").rglob("*")
        if path.is_file() and path.suffix in {".py", ".md"}
    }
    if seen != actual:
        raise ValueError(
            f"Reference inventory mismatch: missing={actual - seen}, stale={seen - actual}"
        )
    return result


def target_rows(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    origins: dict[str, list[str]] = defaultdict(list)
    for row in sources:
        for target in filter(None, row["final_targets"].split(";")):
            origins[target].append(row["source_tree"] + "/opsmesh/" + row["source_path"])
    files: set[Path] = set()
    for directory in (
        "src/opsmesh",
        "sdk/python/src",
        "sdk/python/tests",
        "operator/opsmesh_operator",
        "operator/tests",
        "migrations",
        "scripts",
        "deploy",
        ".github",
        "tests",
        "docs/api",
    ):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and not any(
                part in {"__pycache__", ".mypy_cache", ".ruff_cache", "build"}
                or part.endswith(".egg-info")
                for part in path.parts
            ):
                files.add(path)
    files.update(
        ROOT / name
        for name in (
            "README.md",
            "AGENTS.md",
            "pyproject.toml",
            "uv.lock",
            "alembic.ini",
            "release-policy.json",
            ".dockerignore",
            "docs/openapi.json",
            "docs/source-refactor-plan-v4.md",
            "docs/agent-orchestration-target-tree.md",
            "docs/agent-orchestration-delivery.md",
            "docs/expert-creation-workflow.md",
            "docs/capability-publication-guide.md",
            "docs/s14-managed-execution.md",
            "sdk/python/pyproject.toml",
            "sdk/python/codegen.toml",
            "sdk/python/README.md",
            "operator/pyproject.toml",
        )
    )
    result = []
    for path in sorted(files):
        target = path.relative_to(ROOT).as_posix()
        result.append(
            {
                "target": target,
                "sha256": digest(path),
                "sources": ";".join(origins[target])
                or "new V4 contract or generated artifact; no historical implementation imported",
                "acceptance": acceptance(target),
            }
        )
    return result


def render(rows: list[dict[str, str]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    sources = reference_rows()
    targets = target_rows(sources)
    for path, rows in ((SOURCES, sources), (TARGETS, targets)):
        content = render(rows)
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8-sig") != content:
                raise SystemExit(f"Stale manifest: {path.relative_to(ROOT)}; regenerate and review")
        else:
            path.write_text(content, encoding="utf-8")
    external = sum(row["disposition"] == "external_unimplemented" for row in sources)
    print(
        f"Final destinations: {len(sources)} references, {len(targets)} target files, "
        f"{external} externally deferred references"
    )


if __name__ == "__main__":
    main()
