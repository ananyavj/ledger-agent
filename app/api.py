"""HTTP API around the agent, so any front end (our Streamlit UI, a mobile app, a CRM)
can use it. This is the "integration" layer an FDE would hand to a client's developers.

Run locally:   uvicorn app.api:app --reload
Docs:          http://localhost:8000/docs  (generated automatically from the code)

Endpoints (all except /health and /auth/register|login need a signed-in user)
  POST /auth/register, /auth/login, /auth/logout, GET /auth/me    accounts (see app/auth.py)
  /workspaces, /import, /contracts                                 workspaces and data (app/workspaces.py)
  POST /chat                    send a message (starts a new conversation if no thread_id)
  POST /threads/{id}/decisions  approve / edit / reject reminders the agent is waiting on
  GET  /threads/{id}            a conversation's messages and any pending approvals
  GET  /reminders               reminders by status, from the database
  GET  /health                  is the API up, and can it reach Postgres?
"""
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agent import build_graph, postgres_checkpointer
from app.auth import User, current_user
from app.auth import router as auth_router
from app.config import LANGFUSE_PUBLIC_KEY
from app.db import query, transaction
from app.migrations import run_migrations
from app.workspaces import Workspace, current_workspace
from app.workspaces import router as workspaces_router

MAX_STEPS = 12
STATE: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Build the graph once at startup and share it across requests (it's stateless;
    each conversation's state lives in Postgres, keyed by thread_id)."""
    run_migrations()   # make sure accounts and workspaces tables exist (safe to repeat)
    STATE["graph"] = build_graph(postgres_checkpointer())
    STATE["callbacks"] = []
    if LANGFUSE_PUBLIC_KEY:
        from langfuse.langchain import CallbackHandler
        STATE["callbacks"] = [CallbackHandler()]
    yield
    if LANGFUSE_PUBLIC_KEY:
        from langfuse import get_client
        get_client().flush()


app = FastAPI(title="LedgerWithLeisure API", version="1.2", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(workspaces_router)


# ------------------------------------------------------------------ request / response shapes
# Pydantic models validate every request: a missing or wrongly typed field gets a clear
# 422 error before any of our code runs.

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = None


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    edited_message: str | None = Field(default=None, max_length=1200)
    reason: str | None = None


class DecisionsRequest(BaseModel):
    decisions: dict[str, Decision]   # interrupt id -> Meera's decision


class PendingApproval(BaseModel):
    interrupt_id: str
    draft: dict[str, Any]


class AgentResponse(BaseModel):
    thread_id: str
    reply: str | None              # None while the agent is waiting for an approval
    tools_called: list[str]
    pending_approvals: list[PendingApproval]


# ------------------------------------------------------------------ helpers

def _config(thread_id: str, user: User | None = None, ws: Workspace | None = None) -> dict:
    configurable: dict[str, Any] = {"thread_id": thread_id}
    metadata = {"langfuse_session_id": thread_id}
    if user:
        configurable["user_name"] = user.name          # the agent greets and addresses this person
        metadata["langfuse_user_id"] = str(user.id)    # traces can be filtered by user
    if ws:
        # Every tool reads the workspace from here. It comes from the session, never the LLM.
        configurable.update(workspace_id=ws.id, workspace_name=ws.name, as_of=ws.as_of)
    return {"configurable": configurable, "recursion_limit": MAX_STEPS,
            "callbacks": STATE["callbacks"], "metadata": metadata}


def _require_owner(thread_id: str, user: User, ws: Workspace) -> None:
    """Authorization: a user may only see and continue their own conversations, in the
    workspace they're currently in. Anything else gets the same 404 as a missing thread,
    so ids can't be probed."""
    rows = query("SELECT user_id, workspace_id FROM threads WHERE thread_id = %(t)s", {"t": thread_id})
    if not rows or rows[0]["user_id"] != user.id or rows[0]["workspace_id"] != ws.id:
        raise HTTPException(404, "Conversation not found.")


def _pending(config: dict) -> list[PendingApproval]:
    state = STATE["graph"].get_state(config)
    return [PendingApproval(interrupt_id=i.id, draft=i.value)
            for task in state.tasks for i in task.interrupts]


def _run(thread_id: str, payload: Any, user: User, ws: Workspace) -> AgentResponse:
    graph, config = STATE["graph"], _config(thread_id, user, ws)
    seen = len(graph.get_state(config).values.get("messages", []))
    try:
        result = graph.invoke(payload, config)
    except GraphRecursionError:
        raise HTTPException(422, f"The agent couldn't finish within {MAX_STEPS} steps. "
                                 "Start a new conversation and ask a narrower question.")
    new = result["messages"][seen:]
    tools = [c["name"] for m in new if isinstance(m, AIMessage) for c in m.tool_calls]
    pending = _pending(config)
    return AgentResponse(thread_id=thread_id,
                         reply=None if pending else result["messages"][-1].content,
                         tools_called=tools, pending_approvals=pending)


# ------------------------------------------------------------------ endpoints
# Plain `def` (not async): FastAPI runs these in a thread pool, so a slow LLM call
# in one request doesn't block other requests.

@app.post("/chat", response_model=AgentResponse)
def chat(req: ChatRequest, user: User = Depends(current_user),
         ws: Workspace = Depends(current_workspace)) -> AgentResponse:
    if req.thread_id:
        thread_id = req.thread_id
        _require_owner(thread_id, user, ws)
    else:
        thread_id = str(uuid.uuid4())
        with transaction() as conn:
            conn.execute("INSERT INTO threads (thread_id, user_id, workspace_id) VALUES (%s, %s, %s)",
                         (thread_id, user.id, ws.id))
    if _pending(_config(thread_id)):
        raise HTTPException(409, "This conversation is waiting for a reminder decision. "
                                 "Approve or reject it first.")
    return _run(thread_id, {"messages": [HumanMessage(req.message)]}, user, ws)


@app.post("/threads/{thread_id}/decisions", response_model=AgentResponse)
def decide(thread_id: str, req: DecisionsRequest, user: User = Depends(current_user),
           ws: Workspace = Depends(current_workspace)) -> AgentResponse:
    _require_owner(thread_id, user, ws)
    pending = {p.interrupt_id for p in _pending(_config(thread_id))}
    if not pending:
        raise HTTPException(404, "Nothing is waiting for a decision in this conversation.")
    if set(req.decisions) != pending:
        raise HTTPException(422, f"Send exactly one decision for each pending id: {sorted(pending)}")
    # Record WHO decided: the user comes from the session cookie, never from the request body
    decisions = {k: {**v.model_dump(exclude_none=True), "decided_by": user.id}
                 for k, v in req.decisions.items()}
    resume = next(iter(decisions.values())) if len(decisions) == 1 else decisions
    return _run(thread_id, Command(resume=resume), user, ws)


@app.get("/threads/{thread_id}")
def get_thread(thread_id: str, user: User = Depends(current_user),
               ws: Workspace = Depends(current_workspace)) -> dict:
    _require_owner(thread_id, user, ws)
    config = _config(thread_id)
    messages = STATE["graph"].get_state(config).values.get("messages", [])
    history = [{"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
               for m in messages
               if isinstance(m, HumanMessage) or (isinstance(m, AIMessage) and m.content)]
    return {"thread_id": thread_id, "messages": history,
            "pending_approvals": [p.model_dump() for p in _pending(config)]}


@app.get("/reminders")
def list_reminders(status: Literal["pending", "approved", "rejected"] | None = None,
                   limit: int = 50, ws: Workspace = Depends(current_workspace)) -> list[dict]:
    rows = query(
        """
        SELECT r.id, i.invoice_no, b.name AS buyer, r.tone, r.status, r.draft_text,
               r.created_at, r.decided_at, u.name AS decided_by
        FROM reminders r JOIN invoices i ON i.id = r.invoice_id JOIN buyers b ON b.id = i.buyer_id
        LEFT JOIN users u ON u.id = r.decided_by
        WHERE i.workspace_id = %(ws)s
          AND (%(status)s::text IS NULL OR r.status = %(status)s::text)
        ORDER BY r.created_at DESC LIMIT %(limit)s
        """,
        {"ws": ws.id, "status": status, "limit": min(max(1, limit), 200)},
    )
    return [{**r, "created_at": r["created_at"].isoformat(),
             "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None} for r in rows]


@app.get("/health")
def health() -> dict:
    try:
        query("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except Exception as exc:   # noqa: BLE001
        raise HTTPException(503, f"database unavailable: {exc.__class__.__name__}")
