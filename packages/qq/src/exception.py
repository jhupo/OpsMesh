import json

from opsmesh.drivers import Response
from opsmesh.exception import ActionFailed as BaseActionFailed
from opsmesh.exception import AdapterException
from opsmesh.exception import ApiNotAvailable as BaseApiNotAvailable
from opsmesh.exception import NetworkError as BaseNetworkError
from opsmesh.exception import NoLogException as BaseNoLogException

from .store import AuditResultStore


class QQAdapterException(AdapterException):
    def __init__(self):
        super().__init__("qq")


class NoLogException(BaseNoLogException, QQAdapterException):
    pass


class ActionFailed(BaseActionFailed, QQAdapterException):
    def __init__(self, response: Response):
        self.response = response

        self.body: dict | None = None
        if response.content:
            try:
                value = json.loads(response.content)
                if isinstance(value, dict):
                    self.body = value
            except Exception:
                pass

    @property
    def status_code(self) -> int:
        return self.response.status_code

    @property
    def code(self) -> int | None:
        return None if self.body is None else self.body.get("code", None)

    @property
    def message(self) -> str | None:
        return None if self.body is None else self.body.get("message", None)

    @property
    def data(self) -> dict | None:
        return None if self.body is None else self.body.get("data", None)

    @property
    def trace_id(self) -> str | None:
        return self.response.headers.get("X-Tps-trace-ID", None)

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: HTTP {self.status_code}>"

    def __str__(self):
        return self.__repr__()


class UnauthorizedException(ActionFailed):
    pass


class RateLimitException(ActionFailed):
    pass


class NetworkError(BaseNetworkError, QQAdapterException):
    def __init__(self, msg: str | None = None):
        super().__init__()
        self.msg: str | None = msg
        """错误原因"""

    def __repr__(self):
        return f"<NetWorkError message={self.msg}>"

    def __str__(self):
        return self.__repr__()


class ApiNotAvailable(BaseApiNotAvailable, QQAdapterException):
    pass


class AuditException(QQAdapterException):
    """消息审核"""

    def __init__(self, audit_id: str, audit_result: AuditResultStore):
        super().__init__()
        self.audit_id = audit_id
        self._audit_result = audit_result

    async def get_audit_result(self, timeout: float | None = None):
        """获取审核结果"""
        return await self._audit_result.fetch(self.audit_id, timeout)
