"""Transport-neutral HTTP and WebSocket data models."""

import abc
import urllib.request
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    MutableMapping,
)
from dataclasses import dataclass
from enum import Enum
from http.cookiejar import Cookie, CookieJar
from typing import IO, Any, TypeAlias

from multidict import CIMultiDict
from yarl import URL as URL

from opsmesh.framework.utils import UNSET, UnsetType


@dataclass
class Timeout:
    """Request timeout settings."""

    total: float | UnsetType | None = UNSET
    connect: float | UnsetType | None = UNSET
    read: float | UnsetType | None = UNSET
    close: float | UnsetType | None = UNSET
    ping: float | UnsetType | None = UNSET


DEFAULT_TIMEOUT = Timeout(
    total=None, connect=5.0, read=30.0, close=10.0, ping=20.0
)


RawURL: TypeAlias = tuple[bytes, bytes, int | None, bytes]

SimpleQuery: TypeAlias = str | int | float
QueryVariable: TypeAlias = SimpleQuery | list[SimpleQuery]
QueryTypes: TypeAlias = (
    str | Mapping[str, QueryVariable] | list[tuple[str, SimpleQuery]] | None
)

HeaderTypes: TypeAlias = (
    CIMultiDict[str] | dict[str, str] | list[tuple[str, str]] | None
)

CookieTypes: TypeAlias = (
    "Cookies | CookieJar | dict[str, str] | list[tuple[str, str]] | None"
)

ContentTypes: TypeAlias = str | bytes | None
ResponseContentTypes: TypeAlias = "str | bytes | None | ResponseStream"
DataTypes: TypeAlias = dict | None
FileContent: TypeAlias = IO[bytes] | bytes
FileType: TypeAlias = tuple[str | None, FileContent, str | None]
FileTypes: TypeAlias = (
    FileContent  # file (or bytes)
    | tuple[str | None, FileContent]  # (filename, file (or bytes))
    | FileType  # (filename, file (or bytes), content_type)
)
FilesTypes: TypeAlias = (
    dict[str, FileTypes] | list[tuple[str, FileTypes]] | None
)
TimeoutTypes: TypeAlias = float | Timeout | None
PingIntervalTypes: TypeAlias = float | None


class HTTPVersion(Enum):
    """HTTP protocol versions supported by the transport model."""

    H10 = "1.0"
    H11 = "1.1"
    H2 = "2"


class Request:
    """HTTP request data shared across driver implementations."""

    def __init__(
        self,
        method: str | bytes,
        url: "URL | str | RawURL",
        *,
        params: QueryTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        content: ContentTypes = None,
        data: DataTypes = None,
        json: Any = None,
        files: FilesTypes = None,
        version: str | HTTPVersion = HTTPVersion.H11,
        timeout: TimeoutTypes | UnsetType = UNSET,
        proxy: str | None = None,
        ping_interval: PingIntervalTypes | UnsetType = UNSET,
        auto_redirects: bool | UnsetType = UNSET,
        path_params: Mapping[str, str] | None = None,
    ):
        # method
        self.method: str = (
            method.decode("ascii").upper()
            if isinstance(method, bytes)
            else method.upper()
        )
        # http version
        self.version: HTTPVersion = HTTPVersion(version)
        # timeout
        self.timeout: TimeoutTypes | UnsetType = timeout
        # proxy
        self.proxy: str | None = proxy
        # ping interval
        self.ping_interval: PingIntervalTypes | UnsetType = ping_interval
        # auto redirects
        self.auto_redirects: bool | UnsetType = auto_redirects

        # url
        if isinstance(url, tuple):
            scheme, host, port, path = url
            url = URL.build(
                scheme=scheme.decode("ascii"),
                host=host.decode("ascii"),
                port=port,
                path=path.decode("ascii"),
            )
        else:
            url = URL(url)

        if params is not None:
            url = url.update_query(params)
        self.url: URL = url

        # matched path template values supplied by the transport
        self.path_params: dict[str, str] = dict(path_params or {})

        # headers
        self.headers: CIMultiDict[str] = (
            CIMultiDict(headers) if headers is not None else CIMultiDict()
        )
        # cookies
        self.cookies = Cookies(cookies)

        # body
        self.content: ContentTypes = content
        self.data: DataTypes = data
        self.json: Any = json
        self.files: list[tuple[str, FileType]] | None = None
        if files:
            self.files = []
            files_ = files.items() if isinstance(files, dict) else files
            for name, file_info in files_:
                if not isinstance(file_info, tuple):
                    self.files.append((name, (name, file_info, None)))
                elif len(file_info) == 2:
                    self.files.append(
                        (name, (file_info[0], file_info[1], None))
                    )
                else:
                    self.files.append((name, file_info))  # type: ignore

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(method={self.method!r}, "
            f"url='{self.url!s}')"
        )


class ResponseStream:
    """An asynchronous byte stream with an idempotent close contract.

    Drivers iterate the stream without buffering it and must call
    ``aclose`` after a complete send, a disconnect, a timeout, or a send
    error. ``aclose`` may be called more than once.
    """

    __slots__ = ("_iterator", "_close", "_closed", "_on_exit", "failure")

    def __init__(
        self,
        iterator: AsyncIterator[bytes],
        *,
        close: Callable[[], Awaitable[None]] | None = None,
        on_exit: Callable[[BaseException | None], Awaitable[None]]
        | None = None,
    ) -> None:
        self._iterator = iterator
        self._close = close
        self._closed = False
        self._on_exit = on_exit
        self.failure: BaseException | None = None

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterator

    async def aclose(self, error: BaseException | None = None) -> None:
        """Release the underlying iterator or explicit close callback."""
        if self._closed:
            return
        self._closed = True
        self.failure = error or self.failure
        if self._on_exit is not None:
            await self._on_exit(self.failure)
            return
        close = self._close
        if close is None:
            close = getattr(self._iterator, "aclose", None)
        if close is not None:
            await close()


class Response:
    """HTTP response metadata and buffered or streamed content."""

    def __init__(
        self,
        status_code: int,
        *,
        headers: HeaderTypes = None,
        content: ResponseContentTypes = None,
        request: Request | None = None,
    ):
        # status code
        self.status_code: int = status_code

        # headers
        self.headers: CIMultiDict[str] = (
            CIMultiDict(headers) if headers is not None else CIMultiDict()
        )
        # body
        self.content: ResponseContentTypes = content

        # request
        self.request: Request | None = request

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(status_code={self.status_code!r})"


class WebSocket(abc.ABC):
    """Transport-independent WebSocket operations and connection state."""

    def __init__(self, *, request: Request):
        self.request: Request = request

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}('{self.request.url!s}')"

    @property
    @abc.abstractmethod
    def closed(self) -> bool:
        """Whether the connection has closed."""
        raise NotImplementedError

    @abc.abstractmethod
    async def accept(self) -> None:
        """Accept an incoming WebSocket connection."""
        raise NotImplementedError

    @abc.abstractmethod
    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the WebSocket connection."""
        raise NotImplementedError

    @abc.abstractmethod
    async def receive(self) -> str | bytes:
        """Receive one text or binary WebSocket message."""
        raise NotImplementedError

    @abc.abstractmethod
    async def receive_text(self) -> str:
        """Receive one text WebSocket message."""
        raise NotImplementedError

    @abc.abstractmethod
    async def receive_bytes(self) -> bytes:
        """Receive one binary WebSocket message."""
        raise NotImplementedError

    async def send(self, data: str | bytes) -> None:
        """Send one text or binary WebSocket message."""
        if isinstance(data, str):
            await self.send_text(data)
        elif isinstance(data, bytes):
            await self.send_bytes(data)
        else:
            raise TypeError("WebSocket send method expects str or bytes!")

    @abc.abstractmethod
    async def send_text(self, data: str) -> None:
        """Send one text WebSocket message."""
        raise NotImplementedError

    @abc.abstractmethod
    async def send_bytes(self, data: bytes) -> None:
        """Send one binary WebSocket message."""
        raise NotImplementedError


class Cookies(MutableMapping):
    """Cookie-jar mapping with optional domain and path selection."""

    def __init__(self, cookies: CookieTypes = None) -> None:
        self.jar: CookieJar = (
            cookies if isinstance(cookies, CookieJar) else CookieJar()
        )
        if cookies is not None and not isinstance(cookies, CookieJar):
            if isinstance(cookies, dict):
                for key, value in cookies.items():
                    self.set(key, value)
            elif isinstance(cookies, list):
                for key, value in cookies:
                    self.set(key, value)
            elif isinstance(cookies, Cookies):
                for cookie in cookies.jar:
                    self.jar.set_cookie(cookie)
            else:
                raise TypeError(
                    f"Cookies must be dict or list, not {type(cookies)}"
                )

    def set(
        self, name: str, value: str, domain: str = "", path: str = "/"
    ) -> None:
        """Store a session cookie for the specified domain and path."""
        cookie = Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=bool(domain),
            domain_initial_dot=domain.startswith("."),
            path=path,
            path_specified=bool(path),
            secure=False,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
        self.jar.set_cookie(cookie)

    def get(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        name: str,
        default: str | None = None,
        domain: str | None = None,
        path: str | None = None,
    ) -> str | None:
        """Return a cookie value, rejecting ambiguous names across scopes."""
        value: str | None = None
        for cookie in self.jar:
            if (
                cookie.name == name
                and (domain is None or cookie.domain == domain)
                and (path is None or cookie.path == path)
            ):
                if value is not None:
                    message = f"Multiple cookies exist with name={name}"
                    raise ValueError(message)
                value = cookie.value

        return default if value is None else value

    def delete(
        self, name: str, domain: str | None = None, path: str | None = None
    ) -> None:
        """Remove matching cookies, optionally restricted by domain and path."""
        if domain is not None and path is not None:
            return self.jar.clear(domain, path, name)

        remove = [
            cookie
            for cookie in self.jar
            if cookie.name == name
            and (domain is None or cookie.domain == domain)
            and (path is None or cookie.path == path)
        ]

        for cookie in remove:
            self.jar.clear(cookie.domain, cookie.path, cookie.name)

    def clear(self, domain: str | None = None, path: str | None = None) -> None:
        """Clear cookies using the cookie jar domain and path rules."""
        self.jar.clear(domain, path)

    def update(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, cookies: CookieTypes = None
    ) -> None:
        """Merge cookies, replacing entries with the same name and scope."""
        cookies = Cookies(cookies)
        for cookie in cookies.jar:
            self.jar.set_cookie(cookie)

    def as_header(self, request: Request) -> dict[str, str]:
        """Generate cookie headers applicable to the supplied request."""
        urllib_request = self._CookieCompatRequest(request)
        self.jar.add_cookie_header(urllib_request)
        return urllib_request.added_headers

    def __setitem__(self, name: str, value: str) -> None:
        return self.set(name, value)

    def __getitem__(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise KeyError(name)
        return value

    def __delitem__(self, name: str) -> None:
        return self.delete(name)

    def __len__(self) -> int:
        return len(self.jar)

    def __iter__(self) -> Iterator[Cookie]:
        return iter(self.jar)

    def __repr__(self) -> str:
        cookies_repr = ", ".join(
            f"Cookie({cookie.name}={cookie.value} for {cookie.domain})"
            for cookie in self.jar
        )
        return f"{self.__class__.__name__}({cookies_repr})"

    class _CookieCompatRequest(urllib.request.Request):
        def __init__(self, request: Request) -> None:
            super().__init__(
                url=str(request.url),
                headers=dict(request.headers),
                method=request.method,
            )
            self.request = request
            self.added_headers: dict[str, str] = {}

        def add_unredirected_header(  # pyright: ignore[reportIncompatibleMethodOverride]
            self, key: str, value: str
        ) -> None:
            super().add_unredirected_header(key, value)
            self.added_headers[key] = value


@dataclass
class HTTPServerSetup:
    """HTTP server route configuration."""

    # Route paths must be relative URLs.
    path: URL
    method: str
    name: str
    handle_func: Callable[[Request], Awaitable[Response]]


@dataclass
class WebSocketServerSetup:
    """WebSocket server route configuration."""

    # Route paths must be relative URLs.
    path: URL
    name: str
    handle_func: Callable[[WebSocket], Awaitable[Any]]
