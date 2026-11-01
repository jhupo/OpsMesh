"""Offline protocol checks plus one explicit opt-in temporary Redis integration."""

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.coordination import redis as cache
from opsmesh.infrastructure.coordination.redis import RedisLeaseCoordinator, RequestRateLimiter


class ProtocolRedis:
    """A deterministic protocol double for the exact atomic operations we issue.

    This validates call shape and token ownership, not execution by a real Lua VM.
    """

    def __init__(self) -> None:
        self.now = 100.0
        self.values: dict[str, str | int] = {}
        self.expiry: dict[str, float] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.closed = 0
        self.lock = asyncio.Lock()

    def _expire(self, key: str) -> None:
        if self.expiry.get(key, float("inf")) <= self.now:
            self.values.pop(key, None)
            self.expiry.pop(key, None)

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool | None:
        assert nx is True and isinstance(ex, int) and ex > 0
        async with self.lock:
            self.calls.append(("set", key, value, ex, nx))
            self._expire(key)
            if key in self.values:
                return None
            self.values[key] = value
            self.expiry[key] = self.now + ex
            return True

    async def eval(self, script: str, numkeys: int, *arguments: Any) -> int:
        assert numkeys == 1
        normalized = " ".join(script.split())
        async with self.lock:
            self.calls.append(("eval", normalized, numkeys, *arguments))
            key = arguments[0]
            self._expire(key)
            if "'INCR'" in normalized:
                assert normalized == (
                    "local n=redis.call('INCR',KEYS[1]); "
                    "if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n"
                )
                assert len(arguments) == 2 and int(arguments[1]) > 0
                count = int(self.values.get(key, 0)) + 1
                self.values[key] = count
                if count == 1:
                    self.expiry[key] = self.now + int(arguments[1])
                return count
            assert normalized.startswith("if redis.call('GET',KEYS[1])==ARGV[1] then ")
            token = arguments[1]
            if "'EXPIRE'" in normalized:
                assert normalized == (
                    "if redis.call('GET',KEYS[1])==ARGV[1] then "
                    "return redis.call('EXPIRE',KEYS[1],ARGV[2]) else return 0 end"
                )
                assert len(arguments) == 3 and int(arguments[2]) > 0
                if self.values.get(key) != token:
                    return 0
                self.expiry[key] = self.now + int(arguments[2])
                return 1
            assert normalized == (
                "if redis.call('GET',KEYS[1])==ARGV[1] then "
                "return redis.call('DEL',KEYS[1]) else return 0 end"
            )
            assert len(arguments) == 2
            if self.values.get(key) != token:
                return 0
            del self.values[key]
            self.expiry.pop(key, None)
            return 1

    async def aclose(self) -> None:
        self.closed += 1


@pytest.fixture
def redis_protocol(monkeypatch: pytest.MonkeyPatch) -> ProtocolRedis:
    client = ProtocolRedis()

    def from_url(url: str, *, decode_responses: bool, protocol: int) -> ProtocolRedis:
        assert url == "redis://offline.invalid/0" and decode_responses is True and protocol == 2
        return client

    monkeypatch.setattr(cache.Redis, "from_url", from_url)
    return client


def assert_rate_limited(results: list[Any], accepted: int) -> None:
    assert sum(result is None for result in results) == accepted
    rejected = [result for result in results if result is not None]
    assert all(isinstance(result, OpsMeshError) for result in rejected)
    assert all(result.code == "RATE_LIMITED" and result.retryable for result in rejected)


async def test_local_rate_limit_is_atomic_and_window_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(cache, "time", SimpleNamespace(monotonic=lambda: now[0]))
    limiter = RequestRateLimiter()
    results = await asyncio.gather(
        *(limiter.check("principal", limit=7, window_seconds=10) for _ in range(25)),
        return_exceptions=True,
    )
    assert_rate_limited(results, 7)
    await limiter.check("another-principal", limit=1, window_seconds=10)
    now[0] = 109.0
    with pytest.raises(OpsMeshError):
        await limiter.check("principal", limit=7, window_seconds=10)
    now[0] = 110.0
    await limiter.check("principal", limit=7, window_seconds=10)
    await limiter.close()


async def test_local_rate_limit_is_explicitly_process_local() -> None:
    first, second = RequestRateLimiter(), RequestRateLimiter()
    await first.check("same-key", limit=1)
    await second.check("same-key", limit=1)
    await first.close()
    await second.close()


async def test_redis_rate_limit_uses_one_atomic_script_and_fixed_window(
    redis_protocol: ProtocolRedis,
) -> None:
    first = RequestRateLimiter("redis://offline.invalid/0")
    second = RequestRateLimiter("redis://offline.invalid/0")
    results = await asyncio.gather(
        *(limiter.check("shared", limit=5, window_seconds=10) for limiter in (first, second) * 7),
        return_exceptions=True,
    )
    assert_rate_limited(results, 5)
    assert redis_protocol.expiry["opsmesh:rate:shared"] == 110.0
    redis_protocol.now = 109.0
    with pytest.raises(OpsMeshError):
        await first.check("shared", limit=5, window_seconds=10)
    assert redis_protocol.expiry["opsmesh:rate:shared"] == 110.0
    redis_protocol.now = 110.0
    await second.check("shared", limit=5, window_seconds=10)
    assert redis_protocol.expiry["opsmesh:rate:shared"] == 120.0
    assert all(call[0] == "eval" for call in redis_protocol.calls)
    await first.close()
    await second.close()
    assert redis_protocol.closed == 2


async def test_lease_set_nx_expiry_and_stale_owner_cannot_change_replacement(
    redis_protocol: ProtocolRedis,
) -> None:
    first = RedisLeaseCoordinator("redis://offline.invalid/0")
    second = RedisLeaseCoordinator("redis://offline.invalid/0")
    tokens = await asyncio.gather(first.acquire("run", 10), second.acquire("run", 10))
    acquired = [token for token in tokens if token is not None]
    assert len(acquired) == 1 and len(acquired[0]) == 32
    old = acquired[0]
    assert await first.renew("run", "wrong-owner", 10) is False
    assert await second.release("run", "wrong-owner") is False
    assert redis_protocol.values["opsmesh:lease:run"] == old
    redis_protocol.now = 106.0
    assert await first.renew("run", old, 10) is True
    assert redis_protocol.expiry["opsmesh:lease:run"] == 116.0
    redis_protocol.now = 117.0
    replacement = await second.acquire("run", 20)
    assert replacement is not None and replacement != old
    assert await first.renew("run", old, 30) is False
    assert await first.release("run", old) is False
    assert redis_protocol.values["opsmesh:lease:run"] == replacement
    assert redis_protocol.expiry["opsmesh:lease:run"] == 137.0
    assert await second.release("run", replacement) is True
    assert await second.release("run", replacement) is False
    await first.close()
    await second.close()
    assert redis_protocol.closed == 2


async def test_redis_failure_does_not_silently_fall_back_to_local_counts(
    monkeypatch: pytest.MonkeyPatch, redis_protocol: ProtocolRedis
) -> None:
    async def fail(*args: Any) -> int:
        raise ConnectionError("offline protocol failure")

    monkeypatch.setattr(redis_protocol, "eval", fail)
    limiter = RequestRateLimiter("redis://offline.invalid/0")
    with pytest.raises(ConnectionError):
        await limiter.check("principal", limit=10)
    await limiter.close()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("OPSMESH_TEST_REDIS_INTEGRATION") != "1",
    reason="Explicit temporary Redis integration requires OPSMESH_TEST_REDIS_INTEGRATION=1",
)
async def test_real_temporary_redis_atomic_limits_and_lease_ownership(tmp_path: Path) -> None:
    """Start only our own loopback instance; never connect to an existing Redis service."""
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server is not installed")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="redis-integration-", dir=tmp_path) as directory:
        log = Path(directory) / "redis.log"
        process = await asyncio.create_subprocess_exec(
            executable,
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--protected-mode",
            "yes",
            "--dir",
            directory,
            "--logfile",
            str(log),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        url = f"redis://127.0.0.1:{port}/0"
        probe = Redis.from_url(url, decode_responses=True, protocol=2, socket_connect_timeout=0.2)
        first, second = RequestRateLimiter(url), RequestRateLimiter(url)
        owner, competitor = RedisLeaseCoordinator(url), RedisLeaseCoordinator(url)
        try:
            async with asyncio.timeout(10):
                while True:
                    if process.returncode is not None:
                        pytest.fail(
                            f"Temporary Redis failed to start: {log.read_text(errors='replace')}"
                        )
                    try:
                        if await probe.ping():
                            break
                    except (ConnectionError, OSError, RedisConnectionError, RedisTimeoutError):
                        pass
                    await asyncio.sleep(0.05)
            version = await probe.info("server")
            print(f"Temporary loopback Redis version: {version['redis_version']}")
            results = await asyncio.gather(
                *(
                    limiter.check("shared", limit=5, window_seconds=10)
                    for limiter in (first, second) * 10
                ),
                return_exceptions=True,
            )
            assert_rate_limited(results, 5)
            assert 0 < await probe.ttl("opsmesh:rate:shared") <= 10
            claims = await asyncio.gather(owner.acquire("run", 1), competitor.acquire("run", 1))
            tokens = [token for token in claims if token is not None]
            assert len(tokens) == 1
            old = tokens[0]
            assert await owner.renew("run", "wrong-owner", 1) is False
            assert await competitor.release("run", "wrong-owner") is False
            assert await owner.renew("run", old, 1) is True
            await asyncio.sleep(1.1)
            replacement = await competitor.acquire("run", 10)
            assert replacement is not None and replacement != old
            assert await owner.renew("run", old, 20) is False
            assert await owner.release("run", old) is False
            assert await probe.get("opsmesh:lease:run") == replacement
            assert await competitor.release("run", replacement) is True
        finally:
            await asyncio.gather(
                probe.aclose(),
                first.close(),
                second.close(),
                owner.close(),
                competitor.close(),
                return_exceptions=True,
            )
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
