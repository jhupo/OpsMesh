from fastapi import Request

from backend.app.orchestration.runs import build_default_queue
from backend.app.redis.client import redis_client
from backend.app.workers.queue import RedisQueue


def get_worker_queue(request: Request) -> RedisQueue:
    app_redis_client = getattr(request.app.state, "redis_client", None)
    return build_default_queue(app_redis_client or redis_client, request.app.state.settings)
