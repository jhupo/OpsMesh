import hashlib
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from opsmesh.infra.database import now

from .models import LoginThrottle, User
from .passwords import verify_password
from .service import issue_token


def login(db: Session, username: str, password: str, remote: str) -> str | None:
    current = now()
    key = hashlib.sha256(remote.encode()).hexdigest()
    db.execute(
        insert(LoginThrottle)
        .values(key=key, attempts=0, window_until=current)
        .on_conflict_do_nothing()
    )
    throttle = db.get(LoginThrottle, key, with_for_update=True)
    assert throttle is not None
    if throttle.window_until <= current:
        throttle.attempts, throttle.window_until = 0, current + timedelta(minutes=5)
    if throttle.attempts >= 10:
        return None
    throttle.attempts += 1
    user = db.scalar(select(User).where(User.name == username))
    valid = verify_password(password, user.password_hash if user is not None else None)
    if not valid or user is None or not user.enabled:
        return None
    throttle.attempts = 0
    return issue_token(db, user.id, "password-login", current + timedelta(hours=24))
