"""S5 capability primitive tests: package safety, manifests and type contracts.

These tests need no database: they cover the pure validation stack that
publishing runs before anything is stored, including the refusal of
unsafe packages, malformed manifests and dependency cycles.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from pydantic import ValidationError

from opsmesh.capabilities.mcp.schemas import McpConfiguration
from opsmesh.capabilities.packages import (
    MAX_PACKAGE_BYTES,
    UnsafePackageError,
    build_package_archive,
    package_digest,
    read_archive,
)
from opsmesh.capabilities.schemas import (
    ChannelConfiguration,
    ExtensionConfiguration,
    validated_channel_configuration,
    validated_extension_configuration,
)
from opsmesh.capabilities.skills.schemas import parse_skill_package, validated_configuration
from opsmesh.capabilities.tools.schemas import ToolConfiguration
from opsmesh.capabilities.validation import (
    channel_intake_package,
    detect_dependency_cycle,
    extension_intake_package,
    validate_dependencies,
    validate_flow_templates,
    validate_key,
    validate_parameter_schema,
    validate_resource_manifest,
    version_digest,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError


def build_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


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
                    {"type": "tool", "key": "x", "version": 1},
                    {"type": "tool", "key": "x", "version": 1},
                ]
            )

    def test_flow_templates_require_json_paths(self) -> None:
        with pytest.raises(InvalidArgumentError):
            validate_flow_templates([{"path": "flows/x.yaml", "digest": "a" * 64}])

    def test_parameter_schema_rejects_invalid_json_schema(self) -> None:
        with pytest.raises(InvalidArgumentError):
            validate_parameter_schema({"type": "not-a-type"})

    def test_parameter_schemas_refuse_remote_references(self) -> None:
        with pytest.raises(InvalidArgumentError):
            validate_parameter_schema({"$ref": "https://example.com/schema.json"})
        with pytest.raises(InvalidArgumentError):
            validate_parameter_schema({"properties": {"a": {"$dynamicRef": "schema.json"}}})

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
            capability_type="tool",
            configuration={"instructions": "x"},
            resource_manifest=[],
            dependencies=[],
            flow_templates=[],
            package_digest=None,
        )
        again = version_digest(
            capability_type="tool",
            configuration={"instructions": "x"},
            resource_manifest=[],
            dependencies=[],
            flow_templates=[],
            package_digest=None,
        )
        changed = version_digest(
            capability_type="tool",
            configuration={"instructions": "y"},
            resource_manifest=[],
            dependencies=[],
            flow_templates=[],
            package_digest=None,
        )
        assert first == again
        assert first != changed


class TestTypeContracts:
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

    def test_mcp_secret_slots_are_not_supported(self) -> None:
        with pytest.raises(ValidationError):
            McpConfiguration.model_validate(
                {
                    "transport": "stdio",
                    "command": ["server"],
                    "secret_slots": ["key"],
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
                "SKILL.md": (b"---\nname: guide\ndescription: A guide.\n---\n\nBody text.\n"),
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


def channel_configuration() -> dict[str, object]:
    return {
        "protocol": "wecom",
        "operations": [
            {"name": "receive", "entrypoint": "wecom.adapter:receive"},
            {"name": "send", "entrypoint": "wecom.adapter:send"},
        ],
    }


def extension_configuration() -> dict[str, object]:
    return {
        "operations": [
            {
                "export_id": "summarize",
                "description": "Summarize one run.",
                "effects": ["read"],
                "permissions": ["platform.audit.append"],
            }
        ],
        "hooks": [
            {
                "export_id": "guard-output",
                "description": "Guard model output.",
                "stages": ["output_guardrail"],
                "failure_policy": "block",
            }
        ],
        "events": [
            {
                "export_id": "on-run-finished",
                "description": "Consume finished runs.",
                "source_types": ["run"],
                "source_versions": ["1"],
                "operation_export_id": "summarize",
                "idempotency_key_fields": ["run_id"],
            }
        ],
        "permissions": ["platform.audit.append"],
    }


class TestChannelContracts:
    def test_configuration_accepts_receive_and_send(self) -> None:
        validated = validated_channel_configuration(channel_configuration())
        assert validated["protocol"] == "wecom"
        assert validated["signature_algorithm"] == "hmac-sha256"
        assert [operation["name"] for operation in validated["operations"]] == [
            "receive",
            "send",
        ]

    def test_configuration_requires_receive_and_send(self) -> None:
        payload = channel_configuration()
        payload["operations"] = [{"name": "receive", "entrypoint": "wecom.adapter:receive"}]
        with pytest.raises(ValidationError):
            ChannelConfiguration.model_validate(payload)

    def test_configuration_refuses_unknown_and_duplicate_operations(self) -> None:
        payload = channel_configuration()
        payload["operations"] = [
            {"name": "receive", "entrypoint": "wecom.adapter:receive"},
            {"name": "publish", "entrypoint": "wecom.adapter:publish"},
            {"name": "send", "entrypoint": "wecom.adapter:send"},
        ]
        with pytest.raises(ValidationError):
            ChannelConfiguration.model_validate(payload)
        payload["operations"] = [
            {"name": "receive", "entrypoint": "wecom.adapter:receive"},
            {"name": "receive", "entrypoint": "wecom.adapter:again"},
            {"name": "send", "entrypoint": "wecom.adapter:send"},
        ]
        with pytest.raises(ValidationError):
            ChannelConfiguration.model_validate(payload)

    def test_protocol_is_a_lowercase_vendor_identifier(self) -> None:
        payload = channel_configuration()
        payload["protocol"] = "WeCom"
        with pytest.raises(ValidationError):
            ChannelConfiguration.model_validate(payload)
        payload["protocol"] = "w"
        with pytest.raises(ValidationError):
            ChannelConfiguration.model_validate(payload)

    def test_entrypoint_must_name_module_function(self) -> None:
        payload = channel_configuration()
        payload["operations"][0]["entrypoint"] = "not-an-entrypoint"
        with pytest.raises(ValidationError):
            ChannelConfiguration.model_validate(payload)

    def test_intake_accepts_a_package_that_declares_its_entrypoint_module(self) -> None:
        adapter = b"# the adapter stays data at publish\n"
        manifest = [
            {"path": "wecom/adapter.py", "digest": package_digest(adapter), "size": len(adapter)}
        ]
        intake = channel_intake_package(
            {"wecom/adapter.py": adapter},
            configuration=channel_configuration(),
            resource_manifest=manifest,
            flow_templates=[],
        )
        assert intake.configuration["protocol"] == "wecom"

    def test_intake_refuses_an_entrypoint_the_package_does_not_declare(self) -> None:
        adapter = b"# the adapter stays data at publish\n"
        manifest = [
            {"path": "wecom/adapter.py", "digest": package_digest(adapter), "size": len(adapter)}
        ]
        configuration = channel_configuration()
        configuration["operations"][0]["entrypoint"] = "other.adapter:receive"
        with pytest.raises(InvalidArgumentError):
            channel_intake_package(
                {"wecom/adapter.py": adapter},
                configuration=configuration,
                resource_manifest=manifest,
                flow_templates=[],
            )

    def test_intake_refuses_a_declared_resource_the_package_does_not_carry(self) -> None:
        adapter = b"# the adapter stays data at publish\n"
        manifest = [
            {"path": "wecom/adapter.py", "digest": package_digest(adapter), "size": len(adapter)}
        ]
        with pytest.raises(InvalidArgumentError):
            channel_intake_package(
                {"wecom/other.py": adapter},
                configuration=channel_configuration(),
                resource_manifest=manifest,
                flow_templates=[],
            )


class TestExtensionContracts:
    def test_configuration_accepts_operations_hooks_and_events(self) -> None:
        validated = validated_extension_configuration(extension_configuration())
        assert [operation["export_id"] for operation in validated["operations"]] == ["summarize"]
        assert [hook["export_id"] for hook in validated["hooks"]] == ["guard-output"]

    def test_configuration_refuses_duplicate_export_ids(self) -> None:
        payload = extension_configuration()
        payload["hooks"][0]["export_id"] = "summarize"
        with pytest.raises(ValidationError):
            ExtensionConfiguration.model_validate(payload)

    def test_event_must_consume_a_declared_operation(self) -> None:
        payload = extension_configuration()
        payload["events"][0]["operation_export_id"] = "missing"
        with pytest.raises(ValidationError):
            ExtensionConfiguration.model_validate(payload)

    def test_operation_permissions_must_be_declared(self) -> None:
        payload = extension_configuration()
        payload["permissions"] = []
        with pytest.raises(ValidationError):
            ExtensionConfiguration.model_validate(payload)

    def test_unknown_effects_stages_and_failure_policies_are_refused(self) -> None:
        payload = extension_configuration()
        payload["operations"][0]["effects"] = ["teleport"]
        with pytest.raises(ValidationError):
            ExtensionConfiguration.model_validate(payload)
        payload = extension_configuration()
        payload["hooks"][0]["stages"] = ["whenever"]
        with pytest.raises(ValidationError):
            ExtensionConfiguration.model_validate(payload)
        payload = extension_configuration()
        payload["hooks"][0]["failure_policy"] = "ignore"
        with pytest.raises(ValidationError):
            ExtensionConfiguration.model_validate(payload)

    def test_intake_verifies_the_declared_resources(self) -> None:
        code = b"# extension code stays data at publish\n"
        manifest = [{"path": "ext/main.py", "digest": package_digest(code), "size": len(code)}]
        intake = extension_intake_package(
            {"ext/main.py": code},
            configuration=extension_configuration(),
            resource_manifest=manifest,
            flow_templates=[],
        )
        assert intake.configuration["permissions"] == ["platform.audit.append"]
        with pytest.raises(InvalidArgumentError):
            extension_intake_package(
                {"ext/main.py": b"changed"},
                configuration=extension_configuration(),
                resource_manifest=manifest,
                flow_templates=[],
            )


class TestDeterministicArchives:
    def test_same_content_produces_the_same_digest(self) -> None:
        files = {"b.txt": b"two", "a.txt": b"one"}
        assert build_package_archive(files) == build_package_archive(files)
        assert build_package_archive(files) == build_package_archive(
            {"a.txt": b"one", "b.txt": b"two"}
        )

    def test_changed_content_changes_the_digest(self) -> None:
        assert build_package_archive({"a.txt": b"one"}) != build_package_archive({"a.txt": b"two"})


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
