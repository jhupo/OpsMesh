import json

from backend.app import delivery


def test_check_keeps_import_diagnostics_out_of_json_stdout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(delivery.sys, "argv", ["opsmesh-server", "--directory", ".", "check"])
    monkeypatch.setattr(delivery, "check_runtime", lambda path: print("import diagnostic"))
    monkeypatch.setattr(delivery, "version", lambda name: "0.1.0rc6")
    delivery.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["version"] == "0.1.0rc6"
    assert captured.err == "import diagnostic\n"
