import logging

import pytest

from backend.app.core.maintenance import MaintenanceJob, MaintenanceRunner


def test_maintenance_runner_runs_jobs_due_on_start() -> None:
    clock = FakeClock()
    calls: list[str] = []
    runner = MaintenanceRunner(
        [
            MaintenanceJob(
                name="cleanup",
                run=lambda: calls.append("cleanup") or {"deleted": 1},
                interval_seconds=10,
            )
        ],
        monotonic=clock.now,
    )

    result = runner.tick()

    assert calls == ["cleanup"]
    assert result.skipped == ()
    assert len(result.ran) == 1
    assert result.ran[0].name == "cleanup"
    assert result.ran[0].status == "completed"
    assert result.ran[0].result == {"deleted": 1}


def test_maintenance_runner_skips_jobs_until_interval_elapses() -> None:
    clock = FakeClock()
    calls = 0

    def job() -> int:
        nonlocal calls
        calls += 1
        return calls

    runner = MaintenanceRunner(
        [MaintenanceJob(name="sweeper", run=job, interval_seconds=5)],
        monotonic=clock.now,
    )

    first = runner.tick()
    second = runner.tick()
    clock.advance(5)
    third = runner.tick()

    assert [item.result for item in first.ran] == [1]
    assert second.ran == ()
    assert second.skipped == ("sweeper",)
    assert [item.result for item in third.ran] == [2]


def test_maintenance_runner_records_failed_job_without_raising() -> None:
    clock = FakeClock()

    def broken() -> None:
        raise RuntimeError("boom")

    runner = MaintenanceRunner(
        [MaintenanceJob(name="broken", run=broken, interval_seconds=5)],
        monotonic=clock.now,
        logger_=logging.getLogger("test.maintenance"),
    )

    result = runner.tick()

    assert result.ran[0].status == "failed"
    assert result.ran[0].error == "boom"


def test_maintenance_runner_validates_job_registration() -> None:
    runner = MaintenanceRunner()
    runner.register(MaintenanceJob(name="one", run=lambda: None))

    with pytest.raises(ValueError, match="already registered"):
        runner.register(MaintenanceJob(name="one", run=lambda: None))
    with pytest.raises(ValueError, match="name is required"):
        runner.register(MaintenanceJob(name="", run=lambda: None))
    with pytest.raises(ValueError, match="interval"):
        runner.register(MaintenanceJob(name="bad", run=lambda: None, interval_seconds=0))


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds
