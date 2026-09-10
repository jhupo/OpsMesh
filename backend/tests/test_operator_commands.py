import sys

import pytest
from opsmesh_operator.commands import host_environment


@pytest.mark.parametrize("original", [None, "/host/libraries"])
def test_frozen_cli_restores_host_library_search_for_external_commands(monkeypatch, original):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    env = {"LD_LIBRARY_PATH": "/cli/_internal", "KEEP": "value"}
    if original is not None:
        env["LD_LIBRARY_PATH_ORIG"] = original
    result = host_environment(env)
    assert result.get("LD_LIBRARY_PATH") == original
    assert "LD_LIBRARY_PATH_ORIG" not in result
    assert result["KEEP"] == "value"
    assert env["LD_LIBRARY_PATH"] == "/cli/_internal"


def test_unfrozen_updater_preserves_its_host_environment(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    env = {"LD_LIBRARY_PATH": "/host/libraries"}
    assert host_environment(env) == env
    assert host_environment(env) is not env


@pytest.mark.parametrize("permanent_failure", [False, True])
def test_attestation_retries_are_bounded_and_never_relax_identity(
    monkeypatch, tmp_path, permanent_failure
):
    from opsmesh_operator import releases

    calls = []

    def verify(argv, **kwargs):
        calls.append(argv)
        if permanent_failure or len(calls) < 3:
            raise RuntimeError("Verification unavailable or rejected")
        return ""

    monkeypatch.setattr(releases, "run_command", verify)
    monkeypatch.setattr(releases.ReleaseSource.verify.retry, "sleep", lambda _: None)
    source = releases.ReleaseSource("jhupo/OpsMesh")
    if permanent_failure:
        with pytest.raises(RuntimeError):
            source.verify(tmp_path / "archive", "v0.1.0rc7", commit="a" * 40)
    else:
        source.verify(tmp_path / "archive", "v0.1.0rc7", commit="a" * 40)
    assert len(calls) == 3
    assert calls[0] == calls[1] == calls[2]
    assert "--source-digest" in calls[0]
    assert "--deny-self-hosted-runners" in calls[0]
