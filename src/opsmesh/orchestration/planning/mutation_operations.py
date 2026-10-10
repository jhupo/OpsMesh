"""Pure graph operations shared by future-plan mutation orchestration."""

from typing import NoReturn


def raw_packages(plan: dict[str, object]) -> list[dict[str, object]]:
    raw = plan.get("work_packages")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Project plan must include work packages")
    packages = [package for package in raw if isinstance(package, dict)]
    if len(packages) != len(raw):
        raise ValueError("Work package must be an object")
    return packages


def package_map(packages: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(package["package_id"]): package for package in packages}


def ordered_unique(values: list[str] | tuple[str, ...] | object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def is_cancelled(package: dict[str, object]) -> bool:
    mutation = package.get("mutation")
    return isinstance(mutation, dict) and mutation.get("state") == "cancelled"


def is_platform_package(package: dict[str, object]) -> bool:
    review_policy = package.get("review_policy")
    return isinstance(review_policy, dict) and review_policy.get("mode") == "agent_planning"


def reject(code: str, message: str) -> NoReturn:
    raise ValueError(f"{code}: {message}")
