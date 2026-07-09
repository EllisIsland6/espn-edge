"""Export endpoints (Phase 5) — CSV / JSON / XLSX portfolio downloads.

Read-only; all data comes from the DB via the exports service (no recomputation, no
ESPN, no secrets). Each response is an attachment.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..services import exports

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
    return JSONResponse(content=exports.portfolio_json(session), headers=_attach("portfolio.json"))


@router.get("/portfolio.xlsx")
def export_xlsx(session: Session = Depends(get_session)) -> Response:
    return Response(
        content=exports.portfolio_xlsx(session),
        media_type=_XLSX_MIME,
        headers=_attach("portfolio.xlsx"),
    )
