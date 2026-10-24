import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opsmesh.bootstrap.initialization import InitialCredentials, save_initial_credentials


class InitialCredentialFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.credentials = InitialCredentials("local-user", "operator", "test-value-only", True)

    def test_creates_an_owner_only_credential_file(self) -> None:
        path = save_initial_credentials(self.root, self.credentials)
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"username": self.credentials.username, "password": self.credentials.password},
        )
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_file_is_never_overwritten(self) -> None:
        path = self.root / f"initial-admin-{self.credentials.user_id}.json"
        path.write_text("preserve-existing-file", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            save_initial_credentials(self.root, self.credentials)
        self.assertEqual(path.read_text(encoding="utf-8"), "preserve-existing-file")

    def test_write_failure_removes_incomplete_credentials(self) -> None:
        with patch("opsmesh.bootstrap.initialization.json.dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_initial_credentials(self.root, self.credentials)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertTrue(save_initial_credentials(self.root, self.credentials).is_file())
