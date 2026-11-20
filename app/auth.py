"""Accounts and sessions: register, sign in, sign out, and "who is this request from?".

How a session works:
  1. On sign-in we create a long random token and send it to the browser in a cookie.
     The cookie is httpOnly (page JavaScript can't read it, which blunts XSS) and
     SameSite=Lax (other websites can't make the browser send it with their requests).
  2. The database stores only a SHA-256 hash of the token. If someone reads the
     sessions table, they still can't use any session.
  3. Every protected endpoint depends on current_user(), which hashes the cookie's
     token and looks it up. Signing out deletes the row, so the token stops working
     immediately (unlike a JWT, which stays valid until it expires).
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.config import COOKIE_SECURE
from app.db import query, transaction

COOKIE_NAME = "session"
SESSION_DAYS = 7
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_hasher = PasswordHasher()   # Argon2id: slow on purpose, so guessing passwords is expensive
_DUMMY_HASH = _hasher.hash("used-only-to-equalise-timing")

router = APIRouter(prefix="/auth", tags=["auth"])


class User(BaseModel):
    id: int
    name: str
    email: str


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _start_session(response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)   # 256 bits of randomness: unguessable
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with transaction() as conn:
        conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                     (token_hash(token), user_id, expires))
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=COOKIE_SECURE, path="/")


def current_user(session: str | None = Cookie(default=None)) -> User:
    """FastAPI dependency: the signed-in user, or a 401 error."""
    if not session:
        raise HTTPException(401, "Please sign in.")
    rows = query(
        """SELECT u.id, u.name, u.email FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = %(h)s AND s.expires_at > now()""",
        {"h": token_hash(session)},
    )
    if not rows:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    return User(**rows[0])


@router.post("/register", response_model=User)
def register(req: RegisterRequest, response: Response) -> User:
    email = req.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    try:
        with transaction() as conn:
            row = conn.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s) "
                "RETURNING id, name, email",
                (req.name.strip(), email, _hasher.hash(req.password)),
            ).fetchone()
            # Everyone can explore the shared demo workspace straight away
            conn.execute("INSERT INTO workspace_members (workspace_id, user_id, role) "
                         "SELECT id, %s, 'member' FROM workspaces WHERE is_demo "
                         "ON CONFLICT DO NOTHING", (row["id"],))
    except psycopg.errors.UniqueViolation:   # the database enforces unique emails, even under races
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    _start_session(response, row["id"])
    return User(**row)


@router.post("/login", response_model=User)
def login(req: LoginRequest, response: Response) -> User:
    rows = query("SELECT id, name, email, password_hash FROM users WHERE email = %(e)s",
                 {"e": req.email.strip().lower()})
    # Same message whether the email or the password is wrong, so nobody can find out
    # which emails have accounts. We also hash a dummy value when the email is unknown,
    # so both cases take about the same time.
    wrong = HTTPException(401, "That email and password don't match.")
    try:
        if not rows:
            _hasher.verify(_DUMMY_HASH, req.password + "x")
            raise wrong
        _hasher.verify(rows[0]["password_hash"], req.password)
    except (VerificationError, InvalidHashError):
        raise wrong
    user = rows[0]
    _start_session(response, user["id"])
    return User(id=user["id"], name=user["name"], email=user["email"])


@router.post("/logout")
def logout(response: Response, session: str | None = Cookie(default=None)) -> dict:
    if session:
        with transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash(session),))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "signed out"}


@router.get("/me", response_model=User)
def me(user: User = Depends(current_user)) -> User:
    return user
