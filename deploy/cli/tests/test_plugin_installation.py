"""Host installation validates bytes without executing extension code."""

import hashlib
import json
import zipfile

import pytest

pytest.importorskip("fcntl", reason="Plugin host operations require Linux")

from opsmesh_operator import plugins
from opsmesh_operator.installation import Installation


def test_install_configure_and_uninstall_never_import_code(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins, "require_host_administrator", lambda: None)
    monkeypatch.setattr(plugins.os, "chown", lambda *a: None)
    installation = Installation(root=tmp_path, mode="systemd")
    package = tmp_path / "example.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "plugin.yaml",
            json.dumps(
                {
                    "id": "example",
                    "version": "0.1.0",
                    "name": "Example",
                    "kind": "tool",
                    "entry": "main.py",
                }
            ),
        )
        archive.writestr("main.py", "raise RuntimeError('installation must not import code')")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="checksum"):
        plugins.install_plugin(installation, str(package), "0" * 64)
    assert plugins.registry(tmp_path) == {}
    result = plugins.install_plugin(installation, str(package), digest)
    assert result["enabled"] is False
    assert result["reload_pending"] is True
    config = tmp_path / "private.json"
    config.write_text('{"token":"private-setting"}')
    plugins.configure_plugin(installation, "example", enabled=True, config=config)
    registry = tmp_path / "plugins/registry.json"
    assert registry.stat().st_mode & 0o777 == 0o640
    assert plugins.registry(tmp_path)["example"]["config"] == {"token": "private-setting"}
    with pytest.raises(ValueError, match="immutable"):
        plugins.install_plugin(installation, str(package), digest)
    assert plugins.registry(tmp_path)["example"]["enabled"] is True
    assert plugins.uninstall_plugin(installation, "example")["reload_pending"] is True
    assert plugins.registry(tmp_path) == {}
    assert (tmp_path / "plugins/example/0.1.0/main.py").is_file()
