import inspect
from typing import IO, Any, get_args, get_origin, get_type_hints
from urllib.parse import urlsplit

from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.exception import ActionFailed, AuditException
from nonebot.adapters.qq.utils import API
from opsmesh_sdk.channels import OperationRejected
from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

from .messages import binary_values


def native_apis() -> dict[str, API]:
    return {name: method for name, method in vars(Bot).items() if isinstance(method, API)}


def catalogue() -> dict[str, Any]:
    return {
        name: {"signature": str(inspect.signature(method.func))}
        for name, method in native_apis().items()
    }


def parameter(annotation: Any, value: Any) -> Any:
    value = binary_values(value)
    if any(get_origin(part) is IO for part in get_args(annotation)):
        if value is not None and not isinstance(value, bytes):
            raise ValueError("Binary content is required; file handles and paths are not accepted")
        return value
    return TypeAdapter(annotation).validate_python(value)


async def call(bot: Bot, name: str, parameters: dict[str, Any]) -> Any:
    method = native_apis().get(name)
    if method is None:
        raise OperationRejected("unknown_qq_operation")
    try:
        signature = inspect.signature(method.func)
        signature.bind(bot, **parameters)
        hints = get_type_hints(method.func)
        converted = {
            key: parameter(hints.get(key, Any), value) for key, value in parameters.items()
        }
        if "presigned_url" in converted:
            url = urlsplit(converted["presigned_url"])
            host = url.hostname or ""
            if (
                url.scheme != "https"
                or url.username
                or url.password
                or url.port not in {None, 443}
                or not any(
                    host == domain or host.endswith("." + domain)
                    for domain in ("qq.com", "qq.com.cn", "qpic.cn", "myqcloud.com")
                )
            ):
                raise ValueError("Untrusted QQ upload endpoint")
    except (ValueError, TypeError) as exc:
        raise OperationRejected("invalid_qq_parameters") from exc
    try:
        result = await bot.call_api(name, **converted)
    except ActionFailed as exc:
        if 400 <= exc.status_code < 500:
            raise OperationRejected(f"qq_rejected_{exc.status_code}_{exc.code or 0}") from exc
        raise
    except AuditException as exc:
        # Submission is accepted but not yet publishable. Keep the native audit receipt.
        return {"status": "pending_audit", "audit_id": exc.audit_id}
    return to_jsonable_python(result, bytes_mode="base64")
