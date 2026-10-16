"""S5 capability primitive tests: package safety, manifests and type contracts.

These tests need no database: they cover the pure validation stack that
publishing runs before anything is stored, including the refusal of
unsafe packages, malformed manifests and dependency cycles.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from opsmesh.capabilities.experts.schemas import ExpertConfiguration
from opsmesh.capabilities.mcp.schemas import McpConfiguration
from opsmesh.capabilities.packages import (
    MAX_PACKAGE_BYTES,
    UnsafePackageError,
    build_package_archive,
    directory_digest,
    package_digest,
    read_archive,
)
from opsmesh.capabilities.skills.schemas import parse_skill_package, validated_configuration
from opsmesh.capabilities.teams.schemas import TeamConfiguration
from opsmesh.capabilities.tools.schemas import ToolConfiguration
from opsmesh.capabilities.validation import (
    detect_dependency_cycle,
    validate_dependencies,
    validate_flow_templates,
    validate_key,
    validate_parameter_schema,
    validate_resource_manifest,
    validate_secret_slots,
    version_digest,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError


def build_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def expert_configuration() -> dict[str, object]:
    return {
        "instructions": "Implement carefully.",
        "prompt": "Be precise.",
        "guardrails": ["no-secrets"],
        "capability_slots": [{"slot": "file-tools", "kind": "tool"}],
        "parameters_schema": {"type": "object", "properties": {}},
        "secret_slots": [],
    }


class TestReadArchive:
    def test_safe_archive_is_read(self) -> None:
        files = read_archive(build_zip({"SKILL.md": b"body", "a/b.txt": b"x"}))
        assert files == {"SKILL.md": b"body", "a/b.txt": b"x"}

    def test_parent_segment_is_refused(self) -> None:
        with pytest.raises(UnsafePackageError):
            read_archive(build_zip({"../escape.txt": b"x"}))

    def test_absolute_path_is_refused(self) -> None:
        with pytest.raises(UnsafePackageError):
            read_archive(build_zip({"/etc/passwd": b"x"}))

    def test_backslash_path_is_refused(self) -> None:
        with pytest.raises(UnsafePackageError):
            read_archive(build_zip({"..\\escape.txt": b"x"}))

    def test_duplicate_member_is_refused(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("dup.txt", b"one")
            archive.writestr("dup.txt", b"two")
        with pytest.raises(UnsafePackageError):
            read_archive(buffer.getvalue())

    def test_empty_payload_is_refused(self) -> None:
        with pytest.raises(UnsafePackageError):
            read_archive(b"")

    def test_non_zip_payload_is_refused(self) -> None:
        with pytest.raises(UnsafePackageError):
            read_archive(b"not a zip archive at all")

    def test_oversized_payload_is_refused(self) -> None:
        with pytest.raises(UnsafePackageError):
            read_archive(b"\0" * (MAX_PACKAGE_BYTES + 1))


class TestManifestValidation:
    def test_resource_manifest_accepts_declared_entries(self) -> None:
        manifest = validate_resource_manifest(
            [
                {
                    "path": "AGENTS.md",
                    "digest": "a" * 64,
                    "size": 12,
                    "media_type": "text/markdown",
                }
            ]
        )
        assert manifest[0]["path"] == "AGENTS.md"

    def test_resource_manifest_refuses_traversal_and_bad_digest(self) -> None:
        with pytest.raises(InvalidArgumentError):
            validate_resource_manifest([{"path": "../x", "digest": "a" * 64, "size": 1}])
        with pytest.raises(InvalidArgumentError):
            validate_resource_manifest([{"path": "x", "digest": "short", "size": 1}])
        with pytest.raises(InvalidArgumentError):
            validate_resource_manifest(
                [
                    {"path": "x", "digest": "a" * 64, "size": 1},
                    {"path": "x", "digest": "b" * 64, "size": 1},
                ]
            )

    def test_dependencies_refuse_unknown_type_and_duplicates(self) -> None:
        with pytest.raises(InvalidArgumentError):
            validate_dependencies([{"type": "worm", "key": "x", "version": 1}])
        with pytest.raises(InvalidArgumentError):
            validate_dependencies(
                [
                    {"type": "expert", "key": "x", "version": 1},
                    {"type": "expert", "key": "x", "version": 1},
                ]
            )

    def test_flow_templates_require_json_paths(self) -> None:
        with pytest.raises(InvalidArgumentError):
            validate_flow_templates([{"path": "flows/x.yaml", "digest": "a" * 64}])

    def test_parameter_schema_rejects_invalid_json_schema(self) -> None:
        with pytest.raises(InvalidArgumentError):
            validate_parameter_schema({"type": "not-a-type"})

    def test_secret_slots_refuse_duplicates(self) -> None:
        with pytest.raises(InvalidArgumentError):
            validate_secret_slots(["key", "key"])

    def test_key_validation(self) -> None:
        assert validate_key("coding") == "coding"
        with pytest.raises(InvalidArgumentError):
            validate_key("  ")

    def test_dependency_cycle_is_refused_with_its_subject(self) -> None:
        detect_dependency_cycle({"a": ["b"], "b": []}, subject="fixed dependencies")
        with pytest.raises(ConflictError) as excinfo:
            detect_dependency_cycle({"a": ["b"], "b": ["a"]}, subject="fixed dependencies")
        assert "fixed dependencies" in str(excinfo.value)

    def test_version_digest_is_stable_and_content_bound(self) -> None:
        first = version_digest(
            capability_type="expert",
            configuration={"instructions": "x"},
            resource_manifest=[],
            dependencies=[],
            flow_templates=[],
            package_digest=None,
        )
        again = version_digest(
            capability_type="expert",
            configuration={"instructions": "x"},
            resource_manifest=[],
            dependencies=[],
            flow_templates=[],
            package_digest=None,
        )
        changed = version_digest(
            capability_type="expert",
            configuration={"instructions": "y"},
            resource_manifest=[],
            dependencies=[],
            flow_templates=[],
            package_digest=None,
        )
        assert first == again
        assert first != changed


class TestTypeContracts:
    def test_expert_configuration_requires_instructions(self) -> None:
        with pytest.raises(ValidationError):
            ExpertConfiguration.model_validate({"instructions": "  "})

    def test_expert_configuration_refuses_duplicate_slots(self) -> None:
        payload = expert_configuration()
        payload["capability_slots"] = [
            {"slot": "s", "kind": "tool"},
            {"slot": "s", "kind": "mcp"},
        ]
        with pytest.raises(ValidationError):
            ExpertConfiguration.model_validate(payload)

    def test_team_configuration_requires_reachable_entrypoint(self) -> None:
        with pytest.raises(ValidationError):
            TeamConfiguration.model_validate(
                {
                    "members": [{"slot": "a", "expert_key": "coding", "role": "lead"}],
                    "entrypoint": "missing",
                }
            )

    def test_team_configuration_refuses_handoff_cycle(self) -> None:
        with pytest.raises(ValidationError):
            TeamConfiguration.model_validate(
                {
                    "members": [
                        {"slot": "a", "expert_key": "x", "role": "one"},
                        {"slot": "b", "expert_key": "y", "role": "two"},
                    ],
                    "entrypoint": "a",
                    "handoffs": [
                        {"source": "a", "target": "b"},
                        {"source": "b", "target": "a"},
                    ],
                }
            )

    def test_team_configuration_refuses_duplicate_roles(self) -> None:
        with pytest.raises(ValidationError):
            TeamConfiguration.model_validate(
                {
                    "members": [
                        {"slot": "a", "expert_key": "x", "role": "same"},
                        {"slot": "b", "expert_key": "y", "role": "same"},
                    ],
                    "entrypoint": "a",
                }
            )

    def test_mcp_remote_requires_endpoint_and_refuses_command(self) -> None:
        with pytest.raises(ValidationError):
            McpConfiguration.model_validate({"transport": "remote"})
        with pytest.raises(ValidationError):
            McpConfiguration.model_validate(
                {"transport": "remote", "endpoint": "https://x.invalid/mcp", "command": ["x"]}
            )

    def test_mcp_local_requires_command(self) -> None:
        with pytest.raises(ValidationError):
            McpConfiguration.model_validate({"transport": "stdio"})

    def test_mcp_environment_secret_reference_must_be_declared(self) -> None:
        with pytest.raises(ValidationError):
            McpConfiguration.model_validate(
                {
                    "transport": "stdio",
                    "command": ["server"],
                    "environment": {"API_KEY": "${undeclared}"},
                }
            )

    def test_tool_direct_mode_is_refused_for_user_declarations(self) -> None:
        with pytest.raises(ValidationError):
            ToolConfiguration.model_validate(
                {
                    "execution_mode": "direct",
                    "entrypoint": "pkg.tools:run",
                    "operations": [{"name": "run"}],
                }
            )

    def test_tool_sdk_export_entrypoint_must_name_module_function(self) -> None:
        with pytest.raises(ValidationError):
            ToolConfiguration.model_validate(
                {
                    "execution_mode": "sdk_export",
                    "entrypoint": "not-an-entrypoint",
                    "operations": [{"name": "run"}],
                }
            )

    def test_tool_operations_refuse_unknown_effects_and_duplicates(self) -> None:
        with pytest.raises(ValidationError):
            ToolConfiguration.model_validate(
                {
                    "execution_mode": "sdk_export",
                    "entrypoint": "pkg.tools:run",
                    "operations": [{"name": "run", "effects": ["teleport"]}],
                }
            )
        with pytest.raises(ValidationError):
            ToolConfiguration.model_validate(
                {
                    "execution_mode": "sdk_export",
                    "entrypoint": "pkg.tools:run",
                    "operations": [{"name": "run"}, {"name": "run"}],
                }
            )

    def test_skill_package_derives_configuration_and_resources(self) -> None:
        configuration, resources = parse_skill_package(
            {
                "SKILL.md": (
                    b"---\nname: guide\ndescription: A guide.\n---\n\nBody text.\n"
                ),
                "references/extra.md": b"extra",
            }
        )
        assert configuration["instructions"] == "Body text.\n"
        assert [entry["path"] for entry in resources] == ["references/extra.md"]
        validated_configuration(configuration)

    def test_skill_package_refuses_missing_or_double_markdown(self) -> None:
        with pytest.raises(InvalidArgumentError):
            parse_skill_package({"readme.md": b"x"})
        with pytest.raises(InvalidArgumentError):
            parse_skill_package(
                {
                    "SKILL.md": b"---\nname: a\ndescription: b\n---\n\nx",
                    "nested/SKILL.md": b"---\nname: a\ndescription: b\n---\n\nx",
                }
            )

    def test_skill_package_refuses_files_outside_its_root(self) -> None:
        with pytest.raises(InvalidArgumentError):
            parse_skill_package(
                {
                    "pkg/SKILL.md": b"---\nname: a\ndescription: b\n---\n\nx",
                    "other.txt": b"y",
                }
            )


class TestDeterministicArchives:
    def test_same_content_produces_the_same_digest(self) -> None:
        files = {"b.txt": b"two", "a.txt": b"one"}
        assert build_package_archive(files) == build_package_archive(files)
        assert directory_digest(files) == directory_digest({"a.txt": b"one", "b.txt": b"two"})

    def test_changed_content_changes_the_digest(self) -> None:
        assert directory_digest({"a.txt": b"one"}) != directory_digest({"a.txt": b"two"})


class TestPublishNeverExecutes:
    def test_a_package_whose_module_would_raise_is_only_data(self) -> None:
        """Publishing validates bytes; the tool module is never imported."""
        from opsmesh.capabilities.validation import verify_declared_resources

        payload = {
            "execution_mode": "sdk_export",
            "entrypoint": "boom.tools:run",
            "operations": [{"name": "run"}],
        }
        configuration = ToolConfiguration.model_validate(payload).model_dump(mode="json")
        files = read_archive(
            build_zip(
                {
                    "boom/tools.py": (
                        b"raise RuntimeError('importing this module must never happen')"
                    ),
                }
            )
        )
        manifest = [
            {
                "path": "boom/tools.py",
                "digest": package_digest(files["boom/tools.py"]),
                "size": len(files["boom/tools.py"]),
            }
        ]
        verify_declared_resources(files, manifest)
        assert configuration["entrypoint"] == "boom.tools:run"

    def test_bundled_release_index_parses(self) -> None:
        root = Path(__file__).resolve().parents[2] / "bundled"
        index = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        from opsmesh.capabilities.provisioning import read_release_index

        release, entries = read_release_index(root)
        assert release == index["release"]
        assert len(entries) == len(index["packages"])
