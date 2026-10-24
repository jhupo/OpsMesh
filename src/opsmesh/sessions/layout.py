"""Session paths and sandbox initialization layout."""

from pathlib import Path

from agents.sandbox import Dir, Manifest

from .models import AgentSession


def session_directory(root: Path, row: AgentSession) -> Path:
    return (
        root / "users" / row.owner_id / "agents" / row.agent_key / ".opsmesh" / "sessions" / row.id
    )


def default_manifest(root: str, supplied: Manifest | None) -> Manifest:
    entries = {
        "input": Dir(),
        "output": Dir(),
        ".opsmesh": Dir(children={"skills": Dir(), "session": Dir(), "cache": Dir()}),
    }
    if supplied is None:
        return Manifest(root=root, entries=entries)
    if supplied.root != root:
        raise ValueError("Manifest root must match the configured sandbox root")
    custom = dict(supplied.entries)
    internal = custom.get(".opsmesh")
    if internal is not None:
        if not isinstance(internal, Dir):
            raise ValueError(".opsmesh must be a directory")
        internal_children = {
            "skills": Dir(),
            "session": Dir(),
            "cache": Dir(),
            **internal.children,
        }
        custom[".opsmesh"] = internal.model_copy(update={"children": internal_children})
    for name in ("input", "output"):
        if name in custom and not isinstance(custom[name], Dir):
            raise ValueError("Reserved input and output entries must be directories")
    for key, value in entries.items():
        custom.setdefault(key, value)
    return supplied.model_copy(update={"entries": custom})
