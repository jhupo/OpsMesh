"""Package the selected Agent plugin Skill assets for sandbox installation."""

from collections.abc import Mapping

from opsmesh.agents.plugins.registry import PluginRegistry
from opsmesh.agents.skills.packages import build_skill_archive


def skill_packages(
    registry: PluginRegistry,
    *,
    archives: Mapping[tuple[str, str], bytes] | None = None,
) -> dict[str, bytes]:
    result = {}
    for context in registry.plugins.values():
        if context.skills_directory is None:
            continue
        for directory in sorted(context.skills_directory.iterdir()):
            content = (
                build_skill_archive(directory)
                if archives is None
                else archives[context.manifest.id, directory.name]
            )
            identity = context.manifest
            result[f"skills/plugins/{identity.id}/{identity.version}/{directory.name}"] = content
    return result
