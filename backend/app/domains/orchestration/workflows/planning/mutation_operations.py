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


def package_dependencies(package: dict[str, object]) -> list[str]:
    value = package.get("depends_on", [])
    if not isinstance(value, list):
        raise ValueError("Package dependencies must be a list")
    return [str(item) for item in value if isinstance(item, str)]


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
    package_id = str(package.get("package_id") or "")
    review_policy = package.get("review_policy")
    mode = review_policy.get("mode") if isinstance(review_policy, dict) else None
    return (
        package_id == "manager-planning"
        or package_id.startswith("manager-summary")
        or package_id.startswith("executive-")
        or mode in {"final_acceptance", "executive_review"}
    )


def ensure_dependencies_exist(
    package: dict[str, object], packages: list[dict[str, object]]
) -> None:
    ids = {str(item.get("package_id")) for item in packages}
    if any(dependency not in ids for dependency in package_dependencies(package)):
        raise ValueError("New work depends on an unknown package")


def strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def package_index(packages: list[dict[str, object]], package_id: str) -> int:
    for index, package in enumerate(packages):
        if str(package.get("package_id")) == package_id:
            return index
    raise ValueError("Work package was not found")


def reject(code: str, message: str) -> NoReturn:
    raise ValueError(f"{code}: {message}")
