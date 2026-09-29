"""Accounts router — cookie-vault-backed CRUD (SPEC 2.3, 8.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import encrypt
from ..db import get_session
from ..models import Account, League
from ..parse_helpers import normalize_swid_braced
from ..schemas import AccountCreate, AccountOut, AccountReauth
from ..tenancy import current_tenant_id

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(session: Session = Depends(get_session)) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.id)))


@router.post("", response_model=AccountOut, status_code=201)
def add_account(payload: AccountCreate, session: Session = Depends(get_session)) -> Account:
    swid = normalize_swid_braced(payload.swid)
    if not swid:
        raise HTTPException(400, "account identifier looks empty after normalization")
    account = Account(
        label=payload.label,
        swid=swid,
        # Store exactly what the user pasted for espn_s2, encrypted (SPEC 2.3).
        espn_s2_encrypted=encrypt(payload.espn_s2.strip()),
        status="active",
        tenant_id=current_tenant_id(session),
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


@router.post("/{account_id}/reauth", response_model=AccountOut)
def reauth_account(
    account_id: int, payload: AccountReauth, session: Session = Depends(get_session)
) -> Account:
    """Replace an account's cookies after its ESPN session expired and clear the
    needs_reauth status (Phase 7). Never logs or returns swid/espn_s2."""
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    swid = normalize_swid_braced(payload.swid)
    if not swid:
        raise HTTPException(400, "account identifier looks empty after normalization")
    account.swid = swid
    account.espn_s2_encrypted = encrypt(payload.espn_s2.strip())
    account.status = "active"
    session.commit()
    session.refresh(account)
    return account


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, session: Session = Depends(get_session)) -> None:
    """Delete an account. Blocked with 409 while leagues still reference it — the
    user must first re-point or remove those leagues, so a delete can never orphan
    a league or silently drop synced data. (SPEC has no cascade for accounts.)
    """
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    linked = list(
        session.scalars(select(League.espn_league_id).where(League.account_id == account_id))
    )
    if linked:
        raise HTTPException(
            409,
            f"account has {len(linked)} linked league(s): {linked}. "
            "Re-point or remove them before deleting this account.",
        )
    session.delete(account)
    session.commit()
