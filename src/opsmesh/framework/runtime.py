"""Own the process-local driver shared by framework components."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opsmesh.framework.drivers import Driver

_driver: Driver | None = None


def get_driver() -> Driver:
    """Return the initialized process-local driver.

    Raises:
        ValueError: Framework initialization has not completed.
    """
    if _driver is None:
        raise ValueError("Framework has not been initialized.")
    return _driver


def is_initialized() -> bool:
    """Return whether framework initialization has completed."""
    return _driver is not None


def set_driver(driver: Driver) -> None:
    """Bind the driver after its construction succeeds.

    Args:
        driver: The process-local driver owned by the host application.

    Raises:
        RuntimeError: A driver is already bound to the runtime.
    """
    global _driver
    if _driver is not None:
        raise RuntimeError("Framework has already been initialized.")
    _driver = driver
