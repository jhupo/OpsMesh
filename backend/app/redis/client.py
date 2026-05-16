from redis import Redis

from backend.app.core.config import Settings, get_settings


def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        health_check_interval=30,
    )


redis_client = create_redis_client(get_settings())

