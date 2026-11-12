"""Access adapters, channels, and applications through the active driver."""

from typing import Any, TypeVar, overload

from opsmesh.framework.adapters import Adapter, Channel
from opsmesh.framework.drivers import ASGIMixin
from opsmesh.framework.runtime import get_driver as get_driver

A = TypeVar("A", bound=Adapter)


@overload
def get_adapter(name: str) -> Adapter: ...


@overload
def get_adapter(name: type[A]) -> A: ...


def get_adapter(name: str | type[Adapter]) -> Adapter:
    """Return a registered adapter instance.

    Args:
        name: The registered adapter name or adapter class.

    Returns:
        The adapter instance registered under the resolved name.

    Raises:
        ValueError: The requested adapter has not been registered.
        ValueError: The global driver has not been initialized.
            The host must register its driver before accessing this interface.

    Examples:
        ```python
        adapter = opsmesh.framework.get_adapter("registered_adapter")
        ```
    """
    adapters = get_adapters()
    target = name if isinstance(name, str) else name.get_name()
    if target not in adapters:
        raise ValueError(f"Adapter {target} not registered.")
    return adapters[target]


def get_adapters() -> dict[str, Adapter]:
    """Return all registered adapter instances.

    Returns:
        A copy of the registered adapter mapping.

    Raises:
        ValueError: The global driver has not been initialized.
            The host must register its driver before accessing this interface.

    Examples:
        ```python
        adapters = opsmesh.framework.get_adapters()
        ```
    """
    return get_driver().adapters


def get_app() -> Any:
    """Return the server application associated with the global ASGI driver.

    Returns:
        The server application object.

    Raises:
        AssertionError: The global driver does not implement ASGIMixin.
        ValueError: The global driver has not been initialized.
            The host must register its driver before accessing this interface.

    Examples:
        ```python
        app = opsmesh.framework.get_app()
        ```
    """
    driver = get_driver()
    assert isinstance(driver, ASGIMixin), (
        "app object is only available for asgi driver"
    )
    return driver.server_app


def get_asgi() -> Any:
    """Return the ASGI application associated with the global driver.

    See https://asgi.readthedocs.io/ for the ASGI protocol.

    Returns:
        The ASGI application object.

    Raises:
        AssertionError: The global driver does not implement ASGIMixin.
        ValueError: The global driver has not been initialized.
            The host must register its driver before accessing this interface.

    Examples:
        ```python
        asgi = opsmesh.framework.get_asgi()
        ```
    """
    driver = get_driver()
    assert isinstance(driver, ASGIMixin), (
        "asgi object is only available for asgi driver"
    )
    return driver.asgi


def channel(self_id: str | None = None) -> Channel:
    """Return a connected channel.

    With self_id, this is equivalent to channels()[self_id].
    Without self_id, return the first available channel.

    Args:
        self_id: The identifier of the requested channel,
            as exposed by Channel.self_id.

    Returns:
        A connected Channel instance.

    Raises:
        KeyError: No channel exists with the requested self_id.
        ValueError: No self_id was supplied and no channels are connected.
        ValueError: The global driver has not been initialized.
            The host must register its driver before accessing this interface.

    Examples:
        ```python
        assert (
            opsmesh.framework.channel("12345")
            == opsmesh.framework.channels()["12345"]
        )

        another_unspecified_channel = opsmesh.framework.channel()
        ```
    """
    registered_channels = channels()
    if self_id is not None:
        return registered_channels[self_id]

    for connected_channel in registered_channels.values():
        return connected_channel

    raise ValueError("There are no channels to get.")


def channels() -> dict[str, Channel]:
    """Return a snapshot of all connected channels.

    The returned mapping is a copy; mutating it does not connect or
    disconnect channels. Use the driver lifecycle to do so.

    Returns:
        A mapping keyed by each channel's self_id,
        containing the corresponding Channel instances.

    Raises:
        ValueError: The global driver has not been initialized.
            The host must register its driver before accessing this interface.

    Examples:
        ```python
        channels = opsmesh.framework.channels()
        ```
    """
    return get_driver().channels
