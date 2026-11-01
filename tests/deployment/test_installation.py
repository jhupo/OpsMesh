"""Exercise installer boundaries without changing the host's users or services."""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bash() -> str:
    if os.name == "nt":
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
        pytest.skip("Installer checks need Git Bash on Windows")
    executable = shutil.which("bash")
    if executable is None:
        pytest.skip("Installer checks need Bash")
    return executable


def invoke(bash: str, script: str, *arguments: str, environment=None, input_text=None):
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", script, "installer-test", *arguments],
        cwd=ROOT,
        env={**os.environ, "OPSMESH_TEST_REPO": ROOT.as_posix(), **(environment or {})},
        # Preserve the stdin bytes; Windows text mode would turn LF into CRLF.
        input=None if input_text is None else input_text.encode("utf-8"),
        capture_output=True,
        timeout=15,
        check=False,
    )
    return subprocess.CompletedProcess(
        result.args, result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8")
    )


def test_help_does_not_require_root_systemd_or_docker(bash):
    result = invoke(bash, 'bash "$OPSMESH_TEST_REPO/deploy/install.sh" --help')
    assert result.returncode == 0, result.stderr
    assert "--mode" in result.stdout
    assert "local" in result.stdout and "docker" in result.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ["--mode", "kubernetes"],
        ["--instance", "../../escape"],
        ["--instance", "opsmesh;id"],
        ["--port", "0"],
        ["--port", "65536"],
        ["--port", "not-a-port"],
        ["--mode"],
        ["--unknown-option"],
        ["--admin-password-stdin", "--admin-password-file", "/unused/password"],
    ],
)
def test_invalid_arguments_are_rejected_before_host_mutations(bash, tmp_path, arguments):
    guard = tmp_path / "guard.sh"
    marker = tmp_path / "mutation-attempted"
    guard.write_text(
        'deny_mutation() { printf attempted > "$OPSMESH_TEST_MUTATIONS"; return 97; }\n'
        "for name in mkdir install useradd groupadd usermod chown chmod cp mv rm ln touch "
        "systemctl systemd-run docker curl uv; do\n"
        '  eval "$name() { deny_mutation; }"\n'
        "done\n",
        encoding="utf-8",
    )
    result = invoke(
        bash,
        'bash "$OPSMESH_TEST_REPO/deploy/install.sh" "$@"',
        *arguments,
        environment={"BASH_ENV": guard.as_posix(), "OPSMESH_TEST_MUTATIONS": marker.as_posix()},
    )
    assert result.returncode != 0
    assert not marker.exists(), result.stderr


def test_all_installation_shell_sources_parse(bash):
    sources = [ROOT / "deploy/install.sh", *(ROOT / "deploy/install").glob("*.sh")]
    for source in sources:
        result = invoke(bash, 'bash -n "$1"', source.as_posix())
        assert result.returncode == 0, f"{source.name}: {result.stderr}"


@pytest.fixture
def installation(tmp_path):
    environment = {"OPSMESH_TEST_DIRECTORY": tmp_path.as_posix()}
    setup = """
set -Eeuo pipefail
source "$OPSMESH_TEST_REPO/deploy/install.sh"
parse_arguments
configure_installation_paths
INSTALL_DIRECTORY=$OPSMESH_TEST_DIRECTORY/install
CONFIG_DIRECTORY=$OPSMESH_TEST_DIRECTORY/config
DATA_DIRECTORY=$OPSMESH_TEST_DIRECTORY/data
CONFIG_FILE=$CONFIG_DIRECTORY/opsmesh.env
MODE_FILE=$CONFIG_DIRECTORY/install-mode
INITIALIZED_FILE=$CONFIG_DIRECTORY/initialized
DIGEST_FILE=$CONFIG_DIRECTORY/source.sha256
CURRENT_RELEASE=$INSTALL_DIRECTORY/current
RELEASE_DIRECTORY=$INSTALL_DIRECTORY/releases/candidate
SERVICE_FILE=$OPSMESH_TEST_DIRECTORY/opsmesh.service
mkdir -p "$CONFIG_DIRECTORY" "$DATA_DIRECTORY" "$RELEASE_DIRECTORY"
"""
    return setup, environment


def test_default_mode_is_local_without_container_or_cluster_selection(bash):
    result = invoke(
        bash,
        'source "$OPSMESH_TEST_REPO/deploy/install.sh"\n'
        'parse_arguments\nprintf "%s %s %s\\n" "$MODE" "$INSTANCE" "$PORT"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "local opsmesh 8000"


def test_release_payload_excludes_checkout_credentials_and_runtime_files(
    bash, tmp_path, release_bundle
):
    files = {
        "src/opsmesh/__init__.py": '"""Package."""',
        "src/opsmesh/__pycache__/ignored.pyc": "bytecode",
        "src/private.env": "private source-adjacent configuration",
        "deploy/.env": "private deployment configuration",
        "deploy/.env.local": "private deployment override",
        "pyproject.toml": "[project]\n",
        "uv.lock": "version = 1\n",
        ".env": "private configuration",
        ".git/config": "private remote",
        ".venv/runtime": "unrelated environment",
        ".opsmesh/opsmesh.db": "runtime database",
        "python/bin/python3": "interpreter must be installed online",
    }
    for name, content in files.items():
        destination = release_bundle / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    archive = tmp_path / "payload.tar"
    result = invoke(
        bash,
        'source "$OPSMESH_TEST_REPO/deploy/install.sh"\n'
        'BUNDLE_DIRECTORY=$1\nvalidate_release_bundle\nrelease_payload > "$2"',
        release_bundle.as_posix(),
        archive.as_posix(),
    )
    assert result.returncode == 0, result.stderr
    with tarfile.open(archive) as package:
        names = set(package.getnames())
    assert "packages/opsmesh-0.3.0-py3-none-any.whl" in names
    assert "requirements.lock" in names
    assert "deploy/install.sh" in names
    assert not set(files) & names
    assert not any(
        name.startswith(("src/", "python/", ".env", ".git", ".venv", ".opsmesh"))
        or "__pycache__" in name
        for name in names
    )


@pytest.mark.parametrize(
    "changed_file",
    ["requirements.lock", "packages/opsmesh-0.3.0-py3-none-any.whl", "deploy/install/local.sh"],
)
def test_installer_rejects_tampered_bundle_before_installation(bash, release_bundle, changed_file):
    with (release_bundle / changed_file).open("ab") as changed:
        changed.write(b"\nmodified after publication\n")
    result = invoke(
        bash,
        'source "$OPSMESH_TEST_REPO/deploy/install.sh"\n'
        "BUNDLE_DIRECTORY=$1\nvalidate_release_bundle",
        release_bundle.as_posix(),
    )
    assert result.returncode != 0
    assert "checksum validation failed" in result.stderr


def test_installer_requires_checksum_coverage_of_every_release_file(bash, release_bundle):
    checksums = release_bundle / "SHA256SUMS"
    lines = checksums.read_text(encoding="utf-8").splitlines()
    checksums.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8", newline="\n")
    result = invoke(
        bash,
        'source "$OPSMESH_TEST_REPO/deploy/install.sh"\n'
        "BUNDLE_DIRECTORY=$1\nvalidate_release_bundle",
        release_bundle.as_posix(),
    )
    assert result.returncode != 0
    assert "does not cover all release files" in result.stderr


def test_source_checkout_is_not_accepted_as_an_installation_bundle(bash):
    result = invoke(
        bash,
        'source "$OPSMESH_TEST_REPO/deploy/install.sh"\n'
        "BUNDLE_DIRECTORY=$OPSMESH_TEST_REPO\nvalidate_release_bundle",
    )
    assert result.returncode != 0
    assert "release bundle is required" in result.stderr
    assert "build_linux_release.py" in result.stderr


def test_usable_system_python_is_reused_without_downloading_a_runtime(bash, tmp_path, installation):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
is_service_python() { [[ $1 == /usr/bin/python3 ]]; }
readlink() { printf '%s\\n' "${@: -1}"; }
run_uv() { printf unexpected > "$OPSMESH_TEST_DIRECTORY/download-attempted"; return 97; }
choose_local_python
[[ $PYTHON_EXECUTABLE == /usr/bin/python3 ]]
""",
        environment=environment,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "download-attempted").exists()


def test_missing_usable_python_downloads_managed_runtime_after_local_search(
    bash, tmp_path, installation
):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
is_service_python() { [[ $1 == /opt/opsmesh/python/managed/bin/python3 ]]; }
run_uv() {
    printf '%s\\n' "$*" >> "$OPSMESH_TEST_DIRECTORY/python-actions"
    if [[ " $* " == *" --system "* ]]; then return 1; fi
    if [[ " $* " == *" --managed-python "* ]]; then
        printf /opt/opsmesh/python/managed/bin/python3
    fi
}
choose_local_python
[[ $PYTHON_EXECUTABLE == /opt/opsmesh/python/managed/bin/python3 ]]
""",
        environment=environment,
    )
    assert result.returncode == 0, result.stderr
    actions = (tmp_path / "python-actions").read_text(encoding="utf-8").splitlines()
    assert actions[0].startswith("python find --system ")
    assert "--no-python-downloads" in actions[0]
    assert actions[1] == "python install --no-bin 3.13"
    assert actions[2].startswith("python find --managed-python ")


def test_docker_configuration_is_preserved_without_executing_its_contents(
    bash, tmp_path, installation
):
    setup, environment = installation
    configuration = tmp_path / "config/opsmesh.env"
    configuration.parent.mkdir()
    marker = tmp_path / "unexpected-command"
    original = f'OPSMESH_PORT=19876\nCUSTOM_VALUE=$(touch "{marker.as_posix()}")\n'
    configuration.write_text(original, encoding="utf-8")
    result = invoke(bash, setup + "prepare_docker_configuration\n", environment=environment)
    assert result.returncode == 0, result.stderr
    assert configuration.read_text(encoding="utf-8") == original
    assert not marker.exists()


def test_administrator_password_is_transient_and_absent_from_process_arguments(
    bash, tmp_path, installation
):
    setup, environment = installation
    password = secrets.token_urlsafe(32)
    password_file = tmp_path / "initial-password"
    password_file.write_text(password, encoding="utf-8")
    result = invoke(
        bash,
        setup
        + """
prepare_docker_configuration
PASSWORD_FILE=$OPSMESH_TEST_DIRECTORY/initial-password
unset OPSMESH_ADMIN_PASSWORD
read_administrator_password
bash -c '[[ ! ${ADMINISTRATOR_PASSWORD+x} && ! ${OPSMESH_ADMIN_PASSWORD+x} ]]'
systemd-run() {
    [[ $OPSMESH_ADMIN_PASSWORD == "$(<"$PASSWORD_FILE")" ]]
    printf '%s\\n' "$@" > "$OPSMESH_TEST_DIRECTORY/arguments"
}
run_local_command init --admin admin
mark_initialized
[[ ! ${OPSMESH_ADMIN_PASSWORD+x} && ! ${ADMINISTRATOR_PASSWORD+x} ]]
""",
        environment=environment,
    )
    assert result.returncode == 0, result.stderr
    arguments = (tmp_path / "arguments").read_text(encoding="utf-8").splitlines()
    assert "--setenv=OPSMESH_ADMIN_PASSWORD" in arguments
    configuration = (tmp_path / "config/opsmesh.env").read_text(encoding="utf-8")
    assert "OPSMESH_ADMIN_PASSWORD" not in configuration
    assert password not in configuration + "\n".join(arguments) + result.stdout + result.stderr
    assert (tmp_path / "config/initialized").read_text(encoding="utf-8").strip() == "initialized"


@pytest.mark.parametrize("line_ending", ["\n", ""])
def test_stdin_password_reaches_only_initialization_without_touching_disk(
    bash, tmp_path, installation, line_ending
):
    setup, environment = installation
    password = secrets.token_urlsafe(32)
    result = invoke(
        bash,
        setup
        + """
parse_arguments --admin-password-stdin
unset OPSMESH_ADMIN_PASSWORD
# The test harness reads its expected value from the same private pipe first.
IFS= read -r expected_password
read_administrator_password
bash -c '[[ ! ${ADMINISTRATOR_PASSWORD+x} && ! ${OPSMESH_ADMIN_PASSWORD+x} ]]'
systemd-run() {
    [[ $OPSMESH_ADMIN_PASSWORD == "$expected_password" ]]
    printf '%s\\n' "$@" > "$OPSMESH_TEST_DIRECTORY/arguments"
}
run_local_command init --admin admin
mark_initialized
[[ ! ${ADMINISTRATOR_PASSWORD+x} && ! ${OPSMESH_ADMIN_PASSWORD+x} ]]
""",
        environment=environment,
        input_text=password + "\n" + password + line_ending,
    )
    assert result.returncode == 0, result.stderr
    assert password not in result.stdout + result.stderr
    arguments = (tmp_path / "arguments").read_text(encoding="utf-8").splitlines()
    assert "--setenv=OPSMESH_ADMIN_PASSWORD" in arguments
    for written_file in tmp_path.rglob("*"):
        if written_file.is_file():
            assert password.encode() not in written_file.read_bytes()


def test_empty_stdin_password_fails_without_starting_initialization(bash, tmp_path, installation):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
parse_arguments --admin-password-stdin
read_administrator_password
printf unexpected > "$OPSMESH_TEST_DIRECTORY/initialization-started"
""",
        environment=environment,
        input_text="",
    )
    assert result.returncode != 0
    assert "No administrator password received on stdin" in result.stderr
    assert not (tmp_path / "initialization-started").exists()


def test_failed_environment_preparation_does_not_stop_an_existing_service(
    bash, tmp_path, installation
):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
UPGRADE=true
prepare_local_account() { :; }
prepare_release() { :; }
prepare_local_environment() { return 41; }
systemctl() { printf '%s\\n' "$*" >> "$OPSMESH_TEST_DIRECTORY/service-actions"; }
install_local
""",
        environment=environment,
    )
    assert result.returncode == 41, result.stderr
    assert not (tmp_path / "service-actions").exists()


def test_retry_after_initialization_does_not_recreate_the_administrator(
    bash, tmp_path, installation
):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
printf initialized > "$INITIALIZED_FILE"
prepare_local_account() { :; }
prepare_release() { :; }
prepare_local_environment() { :; }
run_local_command() { printf '%s\\n' "$*" >> "$OPSMESH_TEST_DIRECTORY/commands"; }
activate_release() { :; }
install_local_service() { :; }
check_local_readiness() { :; }
systemctl() { printf '%s\\n' "$*" >> "$OPSMESH_TEST_DIRECTORY/service-actions"; }
install_local
""",
        environment=environment,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "commands").read_text(encoding="utf-8").splitlines() == ["check"]
    assert "restart opsmesh.service" in (tmp_path / "service-actions").read_text(encoding="utf-8")


def test_mode_conflict_is_rechecked_under_the_installation_lock(bash, tmp_path, installation):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
MODE=local
printf docker > "$MODE_FILE"
release_payload() { printf unexpected > "$OPSMESH_TEST_DIRECTORY/payload-attempted"; }
inspect_installation_state
""",
        environment=environment,
    )
    assert result.returncode != 0
    assert "Installed mode differs" in result.stderr
    assert (tmp_path / "config/install-mode").read_text(encoding="utf-8") == "docker"
    assert not (tmp_path / "payload-attempted").exists()


def test_native_install_does_not_grant_secrets_to_an_existing_system_group(bash, installation):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
getent() { [[ $1 == group ]]; }
validate_new_instance_identity
""",
        environment=environment,
    )
    assert result.returncode != 0
    assert "unmanaged account or group" in result.stderr


@pytest.mark.parametrize("existing_resource", ["container", "volume", "network"])
def test_docker_install_does_not_adopt_an_unmanaged_compose_project(
    bash, installation, existing_resource
):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
MODE=docker
docker() {
    [[ " $* " == *" label=com.docker.compose.project=opsmesh "* ]]
    if [[ $1 == "$OPSMESH_TEST_EXISTING_RESOURCE" ]]; then printf existing-resource; fi
}
validate_new_instance_identity
""",
        environment={**environment, "OPSMESH_TEST_EXISTING_RESOURCE": existing_resource},
    )
    assert result.returncode != 0
    assert "Unmanaged Compose project" in result.stderr
    assert existing_resource in result.stderr


def test_docker_ownership_lookup_failure_does_not_claim_a_free_project(bash, installation):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
MODE=docker
docker() { return 1; }
validate_new_instance_identity
""",
        environment=environment,
    )
    assert result.returncode != 0
    assert "Could not inspect Docker" in result.stderr


def test_changed_bundle_requires_explicit_upgrade_before_replacement(bash, tmp_path, installation):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
printf local > "$MODE_FILE"
printf old-source-digest > "$DIGEST_FILE"
release_payload() { printf 'new release'; }
inspect_installation_state
""",
        environment=environment,
    )
    assert result.returncode != 0
    assert "--upgrade" in result.stderr
    assert (tmp_path / "config/source.sha256").read_text(encoding="utf-8") == "old-source-digest"


def test_online_update_rechecks_installed_version_inside_install_lock(bash, installation):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
UPGRADE=true
EXPECT_INSTALLED_VERSION=0.3.1
mkdir -p "$CURRENT_RELEASE"
printf '%s\\n' 0.4.0 > "$CURRENT_RELEASE/VERSION"
release_payload() { printf reviewed; }
# Git Bash cannot pass its dynamic descriptors to the bundled native flock.
# Exercise the installer decision after a successfully acquired lock.
flock() { :; }
acquire_installation_lock
inspect_installation_state
""",
        environment=environment,
    )
    assert result.returncode != 0
    assert "Installed version changed" in result.stderr


def test_docker_initialization_precedes_application_start_and_does_not_pass_password_in_argv(
    bash, tmp_path, installation
):
    setup, environment = installation
    result = invoke(
        bash,
        setup
        + """
MODE=docker
REUSE_RELEASE=false
ADMINISTRATOR_PASSWORD=temporary-test-password
prepare_docker_configuration() { :; }
prepare_release() { :; }
activate_release() { :; }
compose() {
    if [[ $1 == run && " $* " == *" init "* ]]; then
        [[ $OPSMESH_ADMIN_PASSWORD == "$ADMINISTRATOR_PASSWORD" ]]
    else
        [[ ! ${OPSMESH_ADMIN_PASSWORD+x} ]]
    fi
    printf '%s\\n' "$*" >> "$OPSMESH_TEST_DIRECTORY/compose-actions"
}
install_docker
[[ ! ${ADMINISTRATOR_PASSWORD+x} && ! ${OPSMESH_ADMIN_PASSWORD+x} ]]
""",
        environment=environment,
    )
    assert result.returncode == 0, result.stderr
    actions = (tmp_path / "compose-actions").read_text(encoding="utf-8").splitlines()
    init = next(index for index, action in enumerate(actions) if " api init " in action)
    check = next(index for index, action in enumerate(actions) if action.endswith(" api check"))
    dependencies = next(
        index for index, action in enumerate(actions) if action.endswith(" postgres redis")
    )
    start = next(
        index
        for index, action in enumerate(actions)
        if action.endswith(" api worker") and action.startswith("up ")
    )
    assert dependencies < init < check < start
    assert "-e OPSMESH_ADMIN_PASSWORD" in actions[init]
    assert all("temporary-test-password" not in action for action in actions)
    assert not any("down" in action or "-v" in action.split() for action in actions)


def test_native_bind_preflight_rejects_another_process_listening_on_the_configured_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = subprocess.run(
            [sys.executable, str(ROOT / "deploy/install/check_ready.py"), "--check-bind"],
            env={
                **os.environ,
                "OPSMESH_HOST": "127.0.0.1",
                "OPSMESH_PORT": str(port),
            },
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    assert result.returncode != 0
    assert str(port) in result.stdout + result.stderr
