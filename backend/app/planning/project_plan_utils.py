from __future__ import annotations

from uuid import UUID


def execution_dependencies(
    *,
    lead_dependency_id: str | None,
    manager_package_id: str | None,
    executive_alignment_ids: list[str],
) -> tuple[str, ...]:
    if lead_dependency_id is not None:
        return (lead_dependency_id,)
    if manager_package_id is not None:
        return (manager_package_id,)
    return tuple(executive_alignment_ids)


def merge_dependencies(
    explicit_dependencies: tuple[str, ...],
    generated_dependencies: tuple[str, ...],
) -> tuple[str, ...]:
    merged: list[str] = []
    for dependency in generated_dependencies + explicit_dependencies:
        if dependency not in merged:
            merged.append(dependency)
    return tuple(merged)


def unique_package_id(base: str, existing_ids: set[str]) -> str:
    candidate = (
        base if base and all(_valid_package_id_character(char) for char in base) else slug(base)
    )
    if candidate not in existing_ids:
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in existing_ids:
        suffix += 1
    return f"{candidate}-{suffix}"


def slug(value: str) -> str:
    slug_value = "".join(character.lower() if character.isalnum() else "-" for character in value)
    slug_value = "-".join(part for part in slug_value.split("-") if part)
    return slug_value or "package"


def same_label(left: str, right: str) -> bool:
    return slug(left) == slug(right)


def skill_names(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [str(key) for key in value if isinstance(key, str)]


def expected_artifacts_for_role(role: str) -> list[str]:
    normalized = role.lower()
    if "design" in normalized or "ui" in normalized:
        return ["design_artifact"]
    if "developer" in normalized or "engineer" in normalized:
        return ["implementation_artifact"]
    if "qa" in normalized or "test" in normalized:
        return ["test_report"]
    return ["work_summary"]


def requested_work_packages(task_input: object) -> list[dict[str, object]]:
    if not isinstance(task_input, dict):
        return []
    raw_packages = task_input.get("work_packages")
    if raw_packages is None:
        raw_packages = task_input.get("required_work_packages")
    if not isinstance(raw_packages, list):
        return []
    return [item for item in raw_packages if isinstance(item, dict)]


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def string_tuple(value: object) -> tuple[str, ...]:
    return tuple(string_list(value))


def string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def dict_or_default(value: object, default: dict[str, object]) -> dict[str, object]:
    return value if isinstance(value, dict) else default


def uuid_or_none(value: object | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def int_or_default(value: object, default: int) -> int:
    return value if isinstance(value, int) else default


def _valid_package_id_character(character: str) -> bool:
    return character.isalnum() or character in {"-", "_", "."}
