from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectDiffEntry:
    path: str
    operation: str
    before: object | None
    after: object | None


def diff_json(before: object, after: object, *, path: str = "") -> list[ProjectDiffEntry]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        entries: list[ProjectDiffEntry] = []
        for key in sorted(set(before) | set(after), key=str):
            child_path = f"{path}/{_escape_pointer_token(str(key))}"
            if key not in before:
                entries.append(ProjectDiffEntry(child_path, "added", None, after[key]))
            elif key not in after:
                entries.append(ProjectDiffEntry(child_path, "removed", before[key], None))
            else:
                entries.extend(diff_json(before[key], after[key], path=child_path))
        return entries
    return [ProjectDiffEntry(path or "/", "changed", before, after)]


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
