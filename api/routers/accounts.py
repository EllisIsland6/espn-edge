"""Accounts router — cookie-vault-backed CRUD (SPEC 2.3, 8.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import encrypt
from ..db import get_session
from ..models import Account
from ..parse_helpers import normalize_swid_braced
from ..schemas import AccountCreate, AccountOut

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(session: Session = Depends(get_session)) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.id)))


@router.post("", response_model=AccountOut, status_code=201)
def add_account(payload: AccountCreate, session: Session = Depends(get_session)) -> Account:
    swid = normalize_swid_braced(payload.swid)
    if not swid:
        raise HTTPException(400, "SWID looks empty after normalization")
    account = Account(
        label=payload.label,
        swid=swid,
        # Store exactly what the user pasted for espn_s2, encrypted (SPEC 2.3).
        espn_s2_encrypted=encrypt(payload.espn_s2.strip()),
        status="active",
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, session: Session = Depends(get_session)) -> None:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    session.delete(account)
    session.commit()
