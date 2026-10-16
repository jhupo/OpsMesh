"""Register feature routers; each step adds its own module routers here."""

from fastapi import FastAPI


def register_routers(app: FastAPI, *, prefix: str) -> None:
    """Include feature routers under the configured API prefix.

    S2 ships no feature routers yet; identity (S4), capabilities/resources (S5)
    and later steps register their own routers in this assembly point.
    """
    _ = app, prefix
