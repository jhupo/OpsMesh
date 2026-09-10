from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=invalid"])
def test_http_pagination_still_rejects_invalid_queries(query: str) -> None:
    client = pagination_client()
    assert client.get(f"/?{query}").status_code == 422


def test_http_pagination_maps_to_shared_contract() -> None:
    client = pagination_client()
    assert client.get("/").json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert client.get("/?limit=100&offset=7").json() == {
        "items": [],
        "total": 0,
        "limit": 100,
        "offset": 7,
    }


def pagination_client() -> TestClient:
    app = FastAPI()

    @app.get("/")
    def page(params: Annotated[PageParams, Depends(pagination_params)]) -> PageResponse[int]:
        assert type(params) is PageParams
        return PageResponse(items=[], total=0, limit=params.limit, offset=params.offset)

    return TestClient(app)


class PaginationBase(DeclarativeBase):
    pass


class PaginationRow(PaginationBase):
    __tablename__ = "pagination_rows"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace: Mapped[str]


def test_database_pagination_preserves_scope_and_total() -> None:
    engine = create_engine("sqlite://")
    try:
        PaginationBase.metadata.create_all(engine)
        with Session(engine) as session:
            session.add_all(
                [
                    PaginationRow(id=1, workspace="own"),
                    PaginationRow(id=2, workspace="foreign"),
                    PaginationRow(id=3, workspace="own"),
                ]
            )
            session.flush()
            statement = (
                select(PaginationRow)
                .where(PaginationRow.workspace == "own")
                .order_by(PaginationRow.id)
            )
            rows, total = page_scalars(session, statement, PageParams(limit=1, offset=1))
            assert [row.id for row in rows] == [3]
            assert total == 2
            assert page_scalars(session, statement, PageParams(limit=1, offset=2)) == ([], 2)
    finally:
        engine.dispose()
