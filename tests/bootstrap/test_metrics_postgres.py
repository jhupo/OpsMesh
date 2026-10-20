"""Private operations listener reads current PG facts without exposing principals."""

from functools import partial

import httpx
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from opsmesh.bootstrap.metrics import product_metrics
from opsmesh.identity.quotas.models import UserStorageAccount, UserTokenAccount
from opsmesh.identity.quotas.service import ensure_ledger
from opsmesh.identity.users.service import create_user
from opsmesh.telemetry.metrics import start_metrics_server


def test_private_listener_exports_current_facts_and_releases_socket(database_sessions):
    with database_sessions() as session, session.begin():
        user = create_user(session, email="metrics@example.com", display_name="Metrics")
        ensure_ledger(session, user_id=user.id, token_limit=100, storage_limit_bytes=1000)
        session.flush()
        scope = user.id
        session.get(UserTokenAccount, user.id).used_tokens = 70
        session.get(UserTokenAccount, user.id).reserved_tokens = 20
        session.get(UserStorageAccount, user.id).used_bytes = 400
    exporter = start_metrics_server(
        host="127.0.0.1", port=0, producer=partial(product_metrics, database_sessions)
    )
    port = exporter.server.server_port
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/metrics", trust_env=False)
        response.raise_for_status()
        assert str(scope) not in response.text
        samples = {
            (sample.name, tuple(sorted(sample.labels.items()))): sample.value
            for family in text_string_to_metric_families(response.text)
            for sample in family.samples
        }
        assert samples[("opsmesh_metrics_collection_success", (("source", "postgres"),))] == 1
        assert samples[("opsmesh_user_quota_max_usage_ratio", (("kind", "token"),))] == 0.9
        assert samples[("opsmesh_user_quota_max_usage_ratio", (("kind", "storage"),))] == 0.4
        assert "opsmesh_cost_budgets" not in response.text
        assert "opsmesh_audit_integrity" not in response.text
        assert samples[("opsmesh_temporal_dispatches", (("state", "pending"),))] >= 1
    finally:
        exporter.close()
    assert not exporter.thread.is_alive()
    # A new listener must be able to bind the just-released address.
    replacement = start_metrics_server(host="127.0.0.1", port=port, producer=lambda: [])
    replacement.close()


def test_database_failure_does_not_publish_previous_snapshot(database_sessions, database_engine):
    assert product_metrics(database_sessions)[-1].value == 1

    def fail(*args):
        raise OperationalError("statement", {}, RuntimeError("database unavailable"))

    event.listen(database_engine, "before_cursor_execute", fail)
    try:
        result = product_metrics(database_sessions)
    finally:
        event.remove(database_engine, "before_cursor_execute", fail)
    assert len(result) == 1
    assert result[0].name == "opsmesh_metrics_collection_success"
    assert result[0].value == 0
