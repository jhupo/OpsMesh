import logging
from uuid import uuid4

from backend.app.core.logging import RequestContextFilter
from backend.app.core.request_context import current_log_context, log_context


def test_log_context_filter_adds_standard_fields_to_records() -> None:
    workspace_id = uuid4()
    user_id = uuid4()
    run_id = uuid4()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    with log_context(
        request_id="req-1",
        workspace_id=workspace_id,
        user_id=user_id,
        run_id=run_id,
        worker_id="worker-1",
    ):
        accepted = RequestContextFilter().filter(record)

    assert accepted is True
    assert record.request_id == "req-1"
    assert record.workspace_id == str(workspace_id)
    assert record.user_id == str(user_id)
    assert record.task_id is None
    assert record.run_id == str(run_id)
    assert record.worker_id == "worker-1"


def test_log_context_resets_nested_values() -> None:
    assert current_log_context()["workspace_id"] is None

    with log_context(workspace_id="outer", user_id="u1"):
        assert current_log_context()["workspace_id"] == "outer"
        assert current_log_context()["user_id"] == "u1"
        with log_context(workspace_id="inner", task_id="task-1"):
            assert current_log_context()["workspace_id"] == "inner"
            assert current_log_context()["task_id"] == "task-1"
        assert current_log_context()["workspace_id"] == "outer"
        assert current_log_context()["task_id"] is None

    assert current_log_context()["workspace_id"] is None
    assert current_log_context()["user_id"] is None
