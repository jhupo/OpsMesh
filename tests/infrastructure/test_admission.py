import asyncio

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.coordination import admission
from opsmesh.infrastructure.coordination.admission import AtomicRunAdmission
from opsmesh.infrastructure.database import AsyncDatabase


@pytest.fixture
async def pools(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'admission.sqlite').as_posix()}"
    first_database, second_database = AsyncDatabase(url), AsyncDatabase(url)
    first, second = AtomicRunAdmission(first_database), AtomicRunAdmission(second_database)
    await first_database.initialize()
    await second_database.verify_schema()
    try:
        yield first, second
    finally:
        await first_database.close()
        await second_database.close()


async def test_two_database_instances_cannot_overbook_capacity(pools):
    results = await asyncio.gather(
        *(pools[index % 2].acquire(f"worker-{index}", 3) for index in range(24)),
        return_exceptions=True,
    )
    admitted = [(index, value) for index, value in enumerate(results) if isinstance(value, str)]
    assert len(admitted) == 3
    for result in results:
        if isinstance(result, Exception):
            assert isinstance(result, OpsMeshError)
            assert result.code == "CAPACITY_BUSY" and result.retryable
    # Independent instances can renew/release the same persistent grant.
    await asyncio.gather(
        *(pools[(index + 1) % 2].renew(f"worker-{index}", token) for index, token in admitted)
    )
    await asyncio.gather(
        *(pools[(index + 1) % 2].release(f"worker-{index}", token) for index, token in admitted)
    )
    replacements = await asyncio.gather(
        *(pools[index % 2].acquire(f"replacement-{index}", 3) for index in range(3))
    )
    assert len(set(replacements)) == 3


async def test_capacity_stays_bounded_while_workers_recycle_slots(pools):
    active = maximum = 0
    occupied_pair = asyncio.Barrier(2)

    async def worker(index):
        nonlocal active, maximum
        pool = pools[index % 2]
        for _ in range(100):
            try:
                token = await pool.acquire(f"worker-{index}", 2)
                break
            except OpsMeshError as error:
                assert error.code == "CAPACITY_BUSY"
                await asyncio.sleep(0.003)
        else:
            pytest.fail("Worker could not acquire an available recycled slot")
        active += 1
        maximum = max(maximum, active)
        try:
            assert active <= 2
            # Each pair must occupy both slots before either can renew and release.
            # A sleep only hoped that the database could admit the second worker in time.
            await occupied_pair.wait()
            await pool.renew(f"worker-{index}", token)
        finally:
            active -= 1
            await pool.release(f"worker-{index}", token)

    await asyncio.wait_for(asyncio.gather(*(worker(index) for index in range(12))), 15)
    assert maximum == 2 and active == 0


async def test_duplicate_owner_and_old_tokens_cannot_renew_or_release_replacement(
    pools, monkeypatch
):
    now = 1000.0
    monkeypatch.setattr(admission, "time", lambda: now)
    first, second = pools
    token = await first.acquire("owner", 1, lease_seconds=10)
    with pytest.raises(OpsMeshError) as error:
        await second.acquire("owner", 10)
    assert error.value.code == "CAPACITY_BUSY"
    for operation in (second.renew, second.release):
        with pytest.raises(OpsMeshError) as error:
            await operation("owner", "wrong-token")
        assert error.value.code == "LEASE_LOST"
    now += 8
    await second.renew("owner", token, lease_seconds=10)
    now += 3
    with pytest.raises(OpsMeshError) as error:
        await first.acquire("other", 1)
    assert error.value.code == "CAPACITY_BUSY"
    now += 8
    # The same owner can reenter after expiration, but receives a new fence token.
    replacement = await second.acquire("owner", 1)
    assert replacement != token
    for operation in (first.renew, first.release):
        with pytest.raises(OpsMeshError) as error:
            await operation("owner", token)
        assert error.value.code == "LEASE_LOST"
    with pytest.raises(OpsMeshError) as error:
        await first.acquire("other", 1)
    assert error.value.code == "CAPACITY_BUSY"
    await first.release("owner", replacement)
    assert await second.acquire("other", 1)


async def test_expiration_and_lowered_limit_wait_for_existing_leases(pools, monkeypatch):
    now = 1000.0
    monkeypatch.setattr(admission, "time", lambda: now)
    first, second = pools
    one = await first.acquire("one", 2, lease_seconds=1)
    two = await second.acquire("two", 2, lease_seconds=5)
    with pytest.raises(OpsMeshError) as error:
        await first.acquire("three", 1)
    assert error.value.code == "CAPACITY_BUSY"
    now += 1
    with pytest.raises(OpsMeshError) as error:
        await first.renew("one", one)
    assert error.value.code == "LEASE_LOST"
    with pytest.raises(OpsMeshError):
        await first.acquire("three", 1)
    await second.release("two", two)
    assert await first.acquire("three", 1)


async def test_cas_contention_has_bounded_cooperative_retries(pools, monkeypatch):
    first, _ = pools
    token = await first.acquire("owner", 1)
    attempts = yields = 0

    async def conflict(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise OpsMeshError("CONFLICT", "Concurrent writer")

    async def backoff(seconds):
        nonlocal yields
        assert 0 < seconds <= 0.05
        yields += 1
        await asyncio.sleep(0)

    monkeypatch.setattr(first._store, "update", conflict)
    monkeypatch.setattr(admission, "sleep", backoff)
    with pytest.raises(OpsMeshError) as error:
        await asyncio.wait_for(first.release("owner", token), 2)
    assert error.value.code == "ADMISSION_CONTENDED" and error.value.retryable
    assert 1 < attempts <= 20 and yields == attempts


async def test_invalid_configuration_fails_before_persistence(pools):
    first, _ = pools
    for limit in (0, -1, True):
        with pytest.raises(OpsMeshError) as error:
            await first.acquire("owner", limit)
        assert error.value.code == "CONFIG_INVALID"
    for duration in (0, -1, float("nan"), float("inf")):
        with pytest.raises(OpsMeshError) as error:
            await first.acquire("owner", 1, duration)
        assert error.value.code == "CONFIG_INVALID"
    with pytest.raises(OpsMeshError):
        await first.acquire("", 1)
    assert await first.acquire("valid", 1)


async def test_corrupt_pool_never_becomes_unlimited_empty_capacity(pools):
    first, second = pools
    await first.acquire("owner", 1)
    record = await first._store.get("backend_execution")
    await first._store.update(record.id, {"version": 2, "leases": {}}, record.revision)
    with pytest.raises(OpsMeshError) as error:
        await second.acquire("other", 1)
    assert error.value.code == "ADMISSION_STATE_INVALID"
