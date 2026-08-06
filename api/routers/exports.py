"""Export endpoints (Phase 5) — CSV / JSON / XLSX portfolio downloads.

Read-only; all data comes from the DB via the exports service (no recomputation, no
ESPN, no secrets). Each response is an attachment.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..services import exports
from ..services.portfolio_filters import PortfolioFilters

router = APIRouter(prefix="/api/exports", tags=["exports"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _attach(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@router.get("/portfolio.csv")
def export_csv(session: Session = Depends(get_session)) -> Response:
    return Response(
        content=exports.portfolio_csv(session),
        media_type="text/csv",
        headers=_attach("portfolio.csv"),
    )


@router.get("/portfolio.json")
def export_json(session: Session = Depends(get_session)) -> JSONResponse:
    return JSONResponse(
        content=jsonable_encoder(exports.portfolio_json(session)),
        headers=_attach("portfolio.json"),
    )


@router.get("/portfolio.xlsx")
def export_xlsx(session: Session = Depends(get_session)) -> Response:
    return Response(
        content=exports.portfolio_xlsx(session),
        media_type=_XLSX_MIME,
        headers=_attach("portfolio.xlsx"),
    )


@router.get("/exposure.csv")
def export_exposure_csv(
    scope: Literal["me", "opponents"] = "me",
    view: Literal["rostered", "field_owned", "all"] = "all",
    season: int | None = None,
    account_id: int | None = None,
    verdict: str | None = None,
    session: Session = Depends(get_session),
) -> Response:
    filename = f"exposure-{scope}.csv" if view == "all" else f"exposure-{scope}-{view}.csv"
    return Response(
        content=exports.exposure_csv(
            session,
            scope=scope,
            view=view,
            filters=PortfolioFilters(
                season=season, account_id=account_id, verdict=verdict
            ),
        ),
        media_type="text/csv",
        headers=_attach(filename),
    )


@router.get("/draft-adp.csv")
def export_draft_adp_csv(
    season: int | None = None,
    account_id: int | None = None,
    verdict: str | None = None,
    session: Session = Depends(get_session),
) -> Response:
    return Response(
        content=exports.draft_adp_csv(
            session,
            filters=PortfolioFilters(
                season=season, account_id=account_id, verdict=verdict
            ),
        ),
        media_type="text/csv",
        headers=_attach("draft-adp.csv"),
    )


@router.get("/strategies.csv")
def export_strategies_csv(
    season: int | None = None,
    account_id: int | None = None,
    verdict: str | None = None,
    session: Session = Depends(get_session),
) -> Response:
    return Response(
        content=exports.strategies_csv(
            session,
            filters=PortfolioFilters(
                season=season, account_id=account_id, verdict=verdict
            ),
        ),
        media_type="text/csv",
        headers=_attach("strategies.csv"),
    )
