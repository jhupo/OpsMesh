"""Transport-layer worker queue dependency.

FastAPI dependency functions belong to the API boundary.  Runtime worker modules expose queue
contracts and implementations but must not import the HTTP framework.
"""

from fastapi import Request

from backend.app.core.redis.client import redis_client
from backend.app.domains.orchestration.runs.service import build_default_queue
from backend.app.runtime.workers.queue import RedisQueue


def get_worker_queue(request: Request) -> RedisQueue:
    app_redis_client = getattr(request.app.state, "redis_client", None)
    return build_default_queue(app_redis_client or redis_client, request.app.state.settings)
