"""Real pg_dump/pg_restore acceptance, confined to a disposable CI database."""

import os
import stat
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from opsmesh_operator.backups import BackupStore
from opsmesh_operator.contracts import ReleaseFile, ReleaseManifest
from opsmesh_operator.files import atomic_write
from opsmesh_operator.installation import Installation
from psycopg import sql
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.skipif(
    os.environ.get("OPSMESH_DELIVERY_BACKUP_TEST") != "1" or sys.platform != "linux",
    reason="Requires explicit disposable Linux/PostgreSQL backup acceptance",
)


def test_real_backup_restore_preserves_data_configuration_and_ownership(tmp_path: Path) -> None:
    assert os.geteuid() == 0, "Run only on a disposable Linux runner as administrator"
    assert os.environ.get("OPSMESH_ENVIRONMENT") == "test"
    source = make_url(os.environ["OPSMESH_DATABASE_URL"])
    database = "opsmesh_delivery_" + uuid4().hex
    connection_options = {
        "host": source.host, "port": source.port or 5432,
        "user": source.username, "password": source.password,
    }
    installation = Installation(root=tmp_path, mode="systemd")
    release = ReleaseManifest(
        tag="v0.1.0rc1", repository="jhupo/OpsMesh", commit="a" * 40,
        backend_digest="sha256:" + "b" * 64, runtime_digest="sha256:" + "c" * 64,
        database_revision="backup_probe", upgrade_from_revisions=["backup_probe"],
        rollback_database_revisions=["backup_probe"], connector_protocol=2,
        platforms=["linux/amd64"],
        files=[ReleaseFile(name="probe.tar.gz", size=1, sha256="d" * 64)],
    )
    directory = installation.release_dir(release.tag)
    directory.mkdir(parents=True)
    atomic_write(directory / "release-manifest.json", release.model_dump_json())
    (tmp_path / "current").symlink_to(directory, target_is_directory=True)
    config = tmp_path / ".env"
    database_url = source.set(database=database).render_as_string(hide_password=False)
    configuration = f"OPSMESH_DATABASE_URL={database_url}\nBACKUP_CANARY=before\n"
    atomic_write(config, configuration, mode=0o640)
    os.chown(config, 0, 10001)
    storage = tmp_path / "data/storage"
    storage.mkdir(parents=True)
    artifact = storage / "workspace/artifact.txt"
    artifact.parent.mkdir()
    artifact.write_text("original artifact", encoding="utf-8")

    with psycopg.connect(**connection_options, dbname="postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            with psycopg.connect(**connection_options, dbname=database) as connection:
                connection.execute("CREATE TABLE canary (value text NOT NULL)")
                connection.execute("INSERT INTO canary VALUES ('before')")
            backups = BackupStore(installation)
            backup_id = backups.create(database_revision="backup_probe")
            assert (backups.path(backup_id) / "verified").is_file()
            with psycopg.connect(**connection_options, dbname=database) as connection:
                connection.execute("UPDATE canary SET value = 'after'")
            artifact.write_text("post-backup artifact", encoding="utf-8")
            config.write_text(configuration.replace("CANARY=before", "CANARY=after"))
            with pytest.raises(ValueError, match="data-loss acknowledgement"):
                backups.restore(backup_id, acknowledge_data_loss=False)
            assert artifact.read_text() == "post-backup artifact"
            backups.restore(backup_id, acknowledge_data_loss=True)
            with psycopg.connect(**connection_options, dbname=database) as connection:
                assert connection.execute("SELECT value FROM canary").fetchone() == ("before",)
            assert artifact.read_text() == "original artifact"
            assert config.read_text() == configuration
            assert stat.S_IMODE(config.stat().st_mode) == 0o640
            assert config.stat().st_gid == 10001
            assert artifact.stat().st_uid == 10001
            preserved = list((tmp_path / "data").glob("before-restore-*/workspace/artifact.txt"))
            assert len(preserved) == 1
            assert preserved[0].read_text() == "post-backup artifact"
            dump = backups.path(backup_id) / "database.dump"
            with dump.open("ab") as stream:
                stream.write(b"tampered")
            with pytest.raises(ValueError, match="checksum mismatch"):
                backups.restore(backup_id, acknowledge_data_loss=True)
            with psycopg.connect(**connection_options, dbname=database) as connection:
                assert connection.execute("SELECT value FROM canary").fetchone() == ("before",)
        finally:
            # Only the exact random database created above is removed, never the supplied CI DB.
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
