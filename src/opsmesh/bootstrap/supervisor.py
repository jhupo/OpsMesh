import asyncio
import signal
import sys


async def run_services() -> None:
    processes: list[asyncio.subprocess.Process] = []
    waiters: list[asyncio.Task[int]] = []
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: loop.call_soon_threadsafe(stop.set))
    stopped = asyncio.create_task(stop.wait())
    try:
        prefix = (
            [sys.executable]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "opsmesh.bootstrap.cli"]
        )
        commands = [["serve"], *(["worker", kind] for kind in ("tasks", "scheduler", "gateway"))]
        for command in commands:
            processes.append(await asyncio.create_subprocess_exec(*prefix, *command))
        waiters = [asyncio.create_task(process.wait()) for process in processes]
        done, _ = await asyncio.wait([*waiters, stopped], return_when=asyncio.FIRST_COMPLETED)
        if stopped not in done:
            raise RuntimeError("An OpsMesh service exited; stopping the remaining services")
    finally:
        signal.signal(signal.SIGTERM, previous)
        stopped.cancel()
        for process in processes:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                await asyncio.wait_for(process.wait(), timeout=15)
            except TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.gather(*waiters, stopped, return_exceptions=True)
