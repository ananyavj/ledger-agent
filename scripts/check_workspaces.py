"""Automatic test of workspaces, data imports, contract uploads and data isolation.

Run from the project root (database running):   python -m scripts.check_workspaces

Creates two throwaway users and a throwaway workspace, imports deliberately messy CSVs,
uploads a contract, and checks that no workspace can see another's data. Calls the real
API code in-process (no server needed) and makes NO LLM calls. Cleans up after itself.
"""
import json
import uuid

from fastapi.testclient import TestClient

from app import tools as t
from app.agent import buyer_summary, top_debtors
from app.api import app
from app.db import query, transaction

RUN = uuid.uuid4().hex[:6]
PASSWORD = "correct-horse-1"
results: list[bool] = []

BUYERS_CSV = ("﻿Customer Name,Contact Person,Mobile,Email ID\n"   # Excel BOM + other column names
              "Kale Organic Farms,Rohan Kale,+91 98000 11111,accounts@kalefarms.in\n"
              "Shinde Hardware,,,\n")
BAD_INVOICES_CSV = ("Invoice Number,Customer,Invoice Date,Due,Total\n"
                    "K-1,Kale Organic Farm,05/10/2026,04/11/2026,\"₹1,20,000\"\n"   # buyer typo
                    "K-2,Shinde Hardware,2026-11-01,2026-10-01,abc\n")              # due < issue, bad amount
GOOD_INVOICES_CSV = ("Invoice Number,Customer,Invoice Date,Due,Total\n"
                     "K-1,Kale Organic Farms,05/10/2026,04/11/2026,\"₹1,20,000\"\n"
                     "K-2,Shinde Hardware,1 Sep 2026,1 Oct 2026,Rs. 45000\n")
PAYMENTS_CSV = "Invoice,Payment Date,Amount Paid,UTR\nK-1,10/11/2026,20000,UTR001\nK-2,15/10/2026,45000,UTR002\n"
CONTRACT_TXT = ("SUPPLY AGREEMENT between Selftest Co and Kale Organic Farms\n\n"
                "1. Payment Terms\nThe Buyer shall pay within 30 days of the invoice date.\n\n"
                "2. Interest on Late Payment\nInterest at 18% per annum is charged on overdue amounts.\n\n"
                "3. Termination\nEither party may terminate with 60 days written notice.\n")


def check(name: str, ok: bool, detail: object = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok else f'  ->  {detail}'}")


def upload(client: TestClient, kind: str, text: str):
    return client.post(f"/import/{kind}", files={"file": (f"{kind}.csv", text.encode(), "text/csv")})


def main() -> None:
    a_email, b_email = f"selftest-a-{RUN}@example.com", f"selftest-b-{RUN}@example.com"
    ws_id = None
    with TestClient(app) as a, TestClient(app) as b:
        a.post("/auth/register", json={"name": "Alpha", "email": a_email, "password": PASSWORD})
        b.post("/auth/register", json={"name": "Beta", "email": b_email, "password": PASSWORD})

        r = a.get("/workspaces").json()
        check("new users start in the demo workspace", r["current"]["is_demo"], r)
        demo_total = a.get("/workspace/summary").json()["outstanding"]
        r = upload(a, "buyers", BUYERS_CSV)
        check("the demo workspace refuses imports (403)", r.status_code == 403, f"{r.status_code} {r.text}")

        r = a.post("/workspaces", json={"name": f"Selftest Co {RUN}"})
        ws_id = r.json().get("id")
        check("create a workspace and switch to it", r.status_code == 200 and
              a.get("/workspaces").json()["current"]["id"] == ws_id, r.text)

        r = upload(a, "buyers", BUYERS_CSV).json()
        check("messy buyers CSV imports (BOM, other column names)", r.get("ok") and r["created"] == 2, r)

        r = upload(a, "invoices", BAD_INVOICES_CSV).json()
        msgs = " | ".join(e["message"] for e in r.get("errors", []))
        check("bad invoices CSV is rejected with row-level errors", not r.get("ok") and r["error_count"] >= 3, r)
        check("...including a 'did you mean' for the misspelt buyer", "Did you mean 'Kale Organic Farms'" in msgs, msgs)
        check("...and NOTHING was imported", a.get("/workspace/summary").json()["invoices"] == 0)

        r = upload(a, "invoices", GOOD_INVOICES_CSV).json()
        check("fixed invoices CSV imports (Indian dates and amounts)", r.get("ok") and r["created"] == 2, r)
        r = upload(a, "payments", PAYMENTS_CSV).json()
        check("payments import", r.get("ok") and r["created"] == 2, r)
        s1 = a.get("/workspace/summary").json()
        check("outstanding is correct (120000 + 45000 - 65000 = 100000)", s1["outstanding"] == 100000, s1)
        r = upload(a, "payments", PAYMENTS_CSV).json()
        s2 = a.get("/workspace/summary").json()
        check("re-importing the same payments changes nothing", r.get("skipped_duplicates") == 2 and
              s2["outstanding"] == 100000, (r, s2))
        r = upload(a, "invoices", GOOD_INVOICES_CSV).json()
        check("re-importing the same invoices updates, doesn't duplicate", r.get("updated") == 2 and
              a.get("/workspace/summary").json()["invoices"] == 2, r)

        r = a.post("/contracts", data={"buyer_name": "kale organic farms"},
                   files={"file": ("kale.txt", CONTRACT_TXT.encode(), "text/plain")})
        check("contract upload is split into clauses and indexed", r.status_code == 200 and r.json()["chunks"] >= 3, r.text)
        found = t.search_contract(ws_id, "Kale Organic Farms", "interest on late payment")
        check("contract search finds the right clause", "18%" in (found.get("clauses") or [{}])[0].get("text", ""), found)
        r = a.post("/contracts", data={"buyer_name": "Nobody Ltd"},
                   files={"file": ("x.txt", CONTRACT_TXT.encode(), "text/plain")})
        check("contract for an unknown buyer is refused", r.status_code == 422, r.text)

        # ---- isolation
        mine = t.get_top_debtors(ws_id)
        check("tools in the new workspace see only its buyers",
              {x["buyer"] for x in mine["buyers"]} <= {"Kale Organic Farms", "Shinde Hardware"}, mine)
        check("a demo buyer doesn't exist in the new workspace",
              "error" in t.resolve_buyer(ws_id, "Iyer Spice Exports"))
        check("the demo's totals are untouched",
              t.get_top_debtors(t.DEMO_WORKSPACE_ID)["total_outstanding_all_buyers"] == demo_total)

        check("the workspace id is hidden from the LLM's view of the tools",
              "config" not in top_debtors.args and "workspace_id" not in top_debtors.args, top_debtors.args)
        out = json.loads(top_debtors.invoke({"rank_by": "outstanding"},
                                            config={"configurable": {"workspace_id": ws_id}}))
        check("agent tools use the workspace from the request settings",
              out.get("total_outstanding_all_buyers") == 100000, out)
        out = json.loads(buyer_summary.invoke({"buyer_name": "Kale Organic Farms"},
                                              config={"configurable": {"workspace_id": t.DEMO_WORKSPACE_ID}}))
        check("...so the same tool in the demo can't see the new workspace's buyer", "error" in out, out)

        r = b.post(f"/workspaces/{ws_id}/switch")
        check("another user can't switch into it (404)", r.status_code == 404, f"{r.status_code} {r.text}")
        check("...and still sees only the demo", b.get("/workspace/summary").json()["buyers"] == 40)

        thread_id = f"selftest-{RUN}"
        user_a = a.get("/auth/me").json()
        with transaction() as conn:
            conn.execute("INSERT INTO threads (thread_id, user_id, workspace_id) VALUES (%s, %s, %s)",
                         (thread_id, user_a["id"], ws_id))
        check("owner opens their conversation in its workspace", a.get(f"/threads/{thread_id}").status_code == 200)
        a.post("/workspaces/1/switch")
        check("...but not from a different workspace (404)", a.get(f"/threads/{thread_id}").status_code == 404)

    with transaction() as conn:   # clean up: deleting the workspace removes all its data
        if ws_id:
            conn.execute("DELETE FROM workspaces WHERE id = %s", (ws_id,))
        conn.execute("DELETE FROM users WHERE email IN (%s, %s)", (a_email, b_email))
    leftover = query("SELECT count(*) AS n FROM buyers WHERE workspace_id = %(w)s", {"w": ws_id or -1})[0]["n"]
    check("deleting the workspace removed all of its data", leftover == 0, leftover)

    print(f"\n{sum(results)}/{len(results)} checks passed")
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
