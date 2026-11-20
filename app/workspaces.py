"""Workspaces (one per business), switching between them, and bringing in your own data.

Every request runs "inside" one workspace: the one saved on the session. The workspace
is never taken from the request body or the LLM. Membership is checked on every request,
so removing someone from a workspace locks them out immediately.

Endpoints
  GET  /workspaces                   your workspaces and which one is current
  POST /workspaces                   create one (you become its owner and switch to it)
  POST /workspaces/{id}/switch       change the current workspace
  GET  /workspace/summary            counts for the current workspace
  GET  /import/templates/{kind}      example CSV for buyers / invoices / payments
  POST /import/{kind}                upload a CSV (validated first; all or nothing)
  GET  /contracts                    contracts uploaded in this workspace
  POST /contracts                    upload a contract (.pdf/.txt/.md) for one buyer
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import contracts, importer
from app.auth import User, current_user, token_hash
from app.db import query, transaction

router = APIRouter(tags=["workspaces"])
Kind = Literal["buyers", "invoices", "payments"]


class Workspace(BaseModel):
    id: int
    name: str
    is_demo: bool
    as_of: str      # the "today" this workspace works with
    role: str       # your role in it: owner or member


class CreateWorkspace(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _to_ws(r: dict) -> Workspace:
    return Workspace(id=r["id"], name=r["name"], is_demo=r["is_demo"], role=r["role"],
                     as_of=(r["as_of_date"] or date.today()).isoformat())


def memberships(user_id: int) -> list[Workspace]:
    rows = query(
        """SELECT w.id, w.name, w.is_demo, w.as_of_date, m.role
           FROM workspace_members m JOIN workspaces w ON w.id = m.workspace_id
           WHERE m.user_id = %(u)s ORDER BY w.is_demo, w.created_at""",   # your own first, demo last
        {"u": user_id},
    )
    return [_to_ws(r) for r in rows]


def _set_session_workspace(session: str, workspace_id: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE sessions SET workspace_id = %s WHERE token_hash = %s",
                     (workspace_id, token_hash(session)))


def current_workspace(user: User = Depends(current_user),
                      session: str | None = Cookie(default=None)) -> Workspace:
    """FastAPI dependency: the workspace this request runs in (membership re-checked)."""
    rows = query(
        """SELECT w.id, w.name, w.is_demo, w.as_of_date, m.role
           FROM sessions s
           JOIN workspace_members m ON m.workspace_id = s.workspace_id AND m.user_id = s.user_id
           JOIN workspaces w ON w.id = s.workspace_id
           WHERE s.token_hash = %(h)s""",
        {"h": token_hash(session or "")},
    )
    if rows:
        return _to_ws(rows[0])
    # Nothing chosen yet (or access was removed): fall back to their first workspace
    mine = memberships(user.id)
    if not mine:
        raise HTTPException(409, "You're not in any workspace yet. Create one to get started.")
    _set_session_workspace(session or "", mine[0].id)
    return mine[0]


def writable_workspace(ws: Workspace = Depends(current_workspace)) -> Workspace:
    if ws.is_demo:
        raise HTTPException(403, "The demo workspace is read-only. Create your own workspace to add data.")
    return ws


@router.get("/workspaces")
def list_workspaces(user: User = Depends(current_user),
                    ws: Workspace = Depends(current_workspace)) -> dict:
    return {"current": ws, "workspaces": memberships(user.id)}


@router.post("/workspaces", response_model=Workspace)
def create_workspace(req: CreateWorkspace, user: User = Depends(current_user),
                     session: str | None = Cookie(default=None)) -> Workspace:
    with transaction() as conn:
        row = conn.execute("INSERT INTO workspaces (name) VALUES (%s) RETURNING id",
                           (req.name.strip(),)).fetchone()
        conn.execute("INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (%s, %s, 'owner')",
                     (row["id"], user.id))
    _set_session_workspace(session or "", row["id"])
    return next(w for w in memberships(user.id) if w.id == row["id"])


@router.post("/workspaces/{workspace_id}/switch", response_model=Workspace)
def switch_workspace(workspace_id: int, user: User = Depends(current_user),
                     session: str | None = Cookie(default=None)) -> Workspace:
    target = next((w for w in memberships(user.id) if w.id == workspace_id), None)
    if target is None:   # not a member: same answer as "doesn't exist"
        raise HTTPException(404, "Workspace not found.")
    _set_session_workspace(session or "", workspace_id)
    return target


@router.get("/workspace/summary")
def summary(ws: Workspace = Depends(current_workspace)) -> dict:
    row = query(
        """SELECT (SELECT count(*) FROM buyers WHERE workspace_id = %(w)s) AS buyers,
                  (SELECT count(*) FROM invoices WHERE workspace_id = %(w)s) AS invoices,
                  (SELECT count(*) FROM payments p JOIN invoices i ON i.id = p.invoice_id
                   WHERE i.workspace_id = %(w)s) AS payments,
                  (SELECT count(DISTINCT (c.buyer_id, c.source_file)) FROM contract_chunks c
                   JOIN buyers b ON b.id = c.buyer_id WHERE b.workspace_id = %(w)s) AS contracts,
                  (SELECT COALESCE(sum(balance), 0) FROM invoice_balances
                   WHERE workspace_id = %(w)s) AS outstanding""",
        {"w": ws.id},
    )[0]
    return {**{k: int(v) for k, v in row.items() if k != "outstanding"},
            "outstanding": float(row["outstanding"]), "workspace": ws}


@router.get("/import/templates/{kind}", response_class=PlainTextResponse)
def template(kind: Kind) -> PlainTextResponse:
    return PlainTextResponse(importer.TEMPLATES[kind], media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'})


# Plain `def` with file.file.read(): parsing, database writes and embedding are blocking
# work, so FastAPI runs these in its thread pool instead of stalling other requests.
@router.post("/import/{kind}")
def import_csv(kind: Kind, file: UploadFile = File(...), user: User = Depends(current_user),
               ws: Workspace = Depends(writable_workspace)) -> dict:
    data = file.file.read(importer.MAX_BYTES + 1)
    try:
        return importer.import_csv(ws.id, kind, data, user_id=user.id)
    except importer.ImportError_ as e:
        raise HTTPException(422, str(e))


@router.get("/contracts")
def get_contracts(ws: Workspace = Depends(current_workspace)) -> list[dict]:
    return contracts.list_contracts(ws.id)


@router.post("/contracts")
def upload_contract(buyer_name: str = Form(..., max_length=120), file: UploadFile = File(...),
                          ws: Workspace = Depends(writable_workspace)) -> dict:
    buyer = query("SELECT id, name FROM buyers WHERE workspace_id = %(w)s AND lower(name) = lower(%(n)s)",
                  {"w": ws.id, "n": buyer_name.strip()})
    if not buyer:
        raise HTTPException(422, f"No buyer called '{buyer_name}' in this workspace. Import buyers first.")
    data = file.file.read(contracts.MAX_BYTES + 1)
    try:
        text = contracts.extract_text(file.filename or "contract.txt", data)
    except contracts.ContractError as e:
        raise HTTPException(422, str(e))
    chunks = contracts.chunk_contract(text, buyer[0]["name"])
    stored = contracts.store_contract(buyer[0]["id"], contracts.safe_filename(file.filename or ""), chunks)
    return {"buyer": buyer[0]["name"], "file": contracts.safe_filename(file.filename or ""),
            "chunks": stored, "characters": len(text)}
