"""Automatic test of sign-up, sign-in, sessions and conversation privacy.

Run from the project root (database running):   python -m scripts.check_auth

It calls the real API code in-process (FastAPI's TestClient), so it doesn't depend on
which server is listening on port 8000. Uses throwaway accounts and deletes them after.
No LLM calls, so it costs no Groq tokens.
"""
import uuid

from fastapi.testclient import TestClient

from app.api import app
from app.db import transaction

RUN = uuid.uuid4().hex[:6]
A_EMAIL, B_EMAIL = f"selftest-a-{RUN}@example.com", f"selftest-b-{RUN}@example.com"
PASSWORD = "correct-horse-1"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok else f'  ->  {detail}'}")


def main() -> None:
    with TestClient(app) as a, TestClient(app) as b:   # two separate browsers, two cookie jars
        r = a.get("/auth/me")
        check("signed out: /auth/me is refused", r.status_code == 401, f"{r.status_code} {r.text}")

        r = a.post("/auth/register", json={"name": "Test Alpha", "email": A_EMAIL.upper(), "password": PASSWORD})
        check("register works", r.status_code == 200, f"{r.status_code} {r.text}")
        check("register sets an httpOnly session cookie",
              "session=" in r.headers.get("set-cookie", "") and "httponly" in r.headers.get("set-cookie", "").lower(),
              r.headers.get("set-cookie", "(no cookie)"))
        check("email is stored lower-case", r.json().get("email") == A_EMAIL, r.text)

        r = a.get("/auth/me")
        check("after register, /auth/me returns the user", r.status_code == 200 and r.json()["name"] == "Test Alpha",
              f"{r.status_code} {r.text}")

        r = a.post("/auth/register", json={"name": "Dup", "email": A_EMAIL, "password": PASSWORD})
        check("duplicate email is refused (409)", r.status_code == 409, f"{r.status_code} {r.text}")

        r = a.post("/auth/register", json={"name": "Short", "email": f"x{RUN}@example.com", "password": "short"})
        check("password under 8 characters is refused (422)", r.status_code == 422, f"{r.status_code} {r.text}")

        r = a.post("/auth/logout")
        check("logout works", r.status_code == 200, f"{r.status_code} {r.text}")
        r = a.get("/auth/me")
        check("after logout, the session no longer works", r.status_code == 401, f"{r.status_code} {r.text}")

        r1 = a.post("/auth/login", json={"email": A_EMAIL, "password": "wrong-password"})
        r2 = a.post("/auth/login", json={"email": f"nobody-{RUN}@example.com", "password": PASSWORD})
        check("wrong password is refused (401)", r1.status_code == 401, f"{r1.status_code} {r1.text}")
        check("unknown email gets the SAME message as a wrong password",
              r2.status_code == 401 and r1.json() == r2.json(), f"{r1.text} vs {r2.text}")

        r = a.post("/auth/login", json={"email": A_EMAIL, "password": PASSWORD})
        check("login with the right password works", r.status_code == 200, f"{r.status_code} {r.text}")

        # Conversation privacy: give user A a conversation, then let user B ask for it
        user_a = a.get("/auth/me").json()
        thread_id = f"selftest-{RUN}"
        with transaction() as conn:
            conn.execute("INSERT INTO threads (thread_id, user_id, workspace_id) VALUES (%s, %s, 1)",
                         (thread_id, user_a["id"]))
        r = a.get(f"/threads/{thread_id}")
        check("owner can open their conversation", r.status_code == 200, f"{r.status_code} {r.text}")

        b.post("/auth/register", json={"name": "Test Beta", "email": B_EMAIL, "password": PASSWORD})
        r = b.get(f"/threads/{thread_id}")
        check("another user CANNOT open it (404)", r.status_code == 404, f"{r.status_code} {r.text}")
        r = b.get("/threads/does-not-exist")
        check("...and gets the same answer as for a missing one", r.status_code == 404, f"{r.status_code} {r.text}")

        r = b.get("/reminders?status=approved")
        check("signed-in user can list reminders", r.status_code == 200, f"{r.status_code} {r.text}")
        r = TestClient(app).get("/reminders")
        check("signed-out request to /reminders is refused", r.status_code == 401, f"{r.status_code} {r.text}")

    with transaction() as conn:   # clean up: deleting the users also deletes their sessions and threads
        conn.execute("DELETE FROM users WHERE email IN (%s, %s)", (A_EMAIL, B_EMAIL))

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
