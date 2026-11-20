"""The agent's tools: plain Python functions that run SQL we wrote and tested.

They know nothing about LangGraph or the LLM. That keeps them easy to test on their
own, and the agent layer will wrap them later.

Every function takes a workspace_id first. It comes from the signed-in session (or the
demo workspace in scripts), NEVER from the LLM, and every query filters by it, so one
business can never see another's data.

Rules every tool follows:
  - Returns a small, JSON-friendly dict. The LLM reads it, so it must be compact.
  - Never guesses which buyer was meant. Unclear names come back as an error with
    candidates, and the agent asks Meera.
  - Never returns more than MAX_ROWS rows, but always returns true totals, so the
    agent can answer "how much in total?" without seeing every invoice.
"""
import difflib
import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.db import query, transaction
from app.embeddings import embed_query, to_pgvector

MAX_ROWS = 15
DEMO_WORKSPACE_ID = 1


def _money(value: Decimal | None) -> float:
    """Decimal -> float for JSON. Safe here: we only display money, never add it up in Python."""
    return float(value or 0)


def workspace_today(workspace_id: int) -> date:
    """The demo workspace is frozen at a fixed date (stable evals); real workspaces use today."""
    rows = query("SELECT as_of_date FROM workspaces WHERE id = %(w)s", {"w": workspace_id})
    if not rows:
        raise ValueError(f"Workspace {workspace_id} does not exist")
    return rows[0]["as_of_date"] or date.today()


def _as_of(workspace_id: int, as_of: str | None = None) -> date:
    return date.fromisoformat(as_of) if as_of else workspace_today(workspace_id)


def resolve_buyer(workspace_id: int, name: str) -> dict[str, Any]:
    """Turn whatever the user typed into exactly one buyer, or explain why we can't.

    Order of checks: exact match, then "contained in" match, then close spellings.
    Returns {"buyer": {...}} on success, or {"error": ..., "candidates": [...]}.
    """
    buyers = query("SELECT id, name FROM buyers WHERE workspace_id = %(w)s ORDER BY name",
                   {"w": workspace_id})
    wanted = name.strip().lower()

    exact = [b for b in buyers if b["name"].lower() == wanted]
    if exact:
        return {"buyer": exact[0]}

    contains = [b for b in buyers if wanted and wanted in b["name"].lower()]
    if len(contains) == 1:
        return {"buyer": contains[0]}
    if len(contains) > 1:
        return {"error": f"'{name}' matches more than one buyer. Ask the user which one they mean.",
                "candidates": [b["name"] for b in contains]}

    by_lower = {b["name"].lower(): b["name"] for b in buyers}
    close = difflib.get_close_matches(wanted, by_lower, n=3, cutoff=0.6)
    if close:
        return {"error": f"No buyer is called '{name}'. Ask the user if they meant one of these.",
                "candidates": [by_lower[c] for c in close]}
    return {"error": f"No buyer matches '{name}'.", "candidates": []}


def get_overdue_invoices(workspace_id: int, min_days_overdue: int = 1, buyer_name: str | None = None,
                         as_of: str | None = None) -> dict[str, Any]:
    """Invoices past their due date that are not fully paid, most overdue first.

    min_days_overdue: only include invoices at least this many days late (default 1).
    buyer_name: optionally limit to one buyer.
    """
    as_of_day = _as_of(workspace_id, as_of)
    min_days = max(1, int(min_days_overdue))   # 0 or negative would mean "not overdue yet"

    buyer_id = None
    if buyer_name:
        found = resolve_buyer(workspace_id, buyer_name)
        if "error" in found:
            return found
        buyer_id = found["buyer"]["id"]

    rows = query(
        """
        SELECT invoice_no, buyer_name, issue_date, due_date, amount, amount_paid, balance, status,
               (%(as_of)s::date - due_date) AS days_overdue
        FROM invoice_balances
        WHERE workspace_id = %(ws)s
          AND balance > 0
          AND (%(as_of)s::date - due_date) >= %(min_days)s
          AND (%(buyer_id)s::int IS NULL OR buyer_id = %(buyer_id)s::int)
        ORDER BY days_overdue DESC, balance DESC
        """,
        {"ws": workspace_id, "as_of": as_of_day, "min_days": min_days, "buyer_id": buyer_id},
    )
    shown = rows[:MAX_ROWS]
    result: dict[str, Any] = {
        "as_of": as_of_day.isoformat(),
        "overdue_invoice_count": len(rows),
        "total_overdue_balance": _money(sum((r["balance"] for r in rows), Decimal(0))),
        "invoices": [
            {"invoice_no": r["invoice_no"], "buyer": r["buyer_name"],
             "invoice_date": r["issue_date"].isoformat(),
             "due_date": r["due_date"].isoformat(), "days_overdue": r["days_overdue"],
             "amount": _money(r["amount"]), "paid": _money(r["amount_paid"]),
             "balance": _money(r["balance"]), "status": r["status"]}
            for r in shown
        ],
    }
    if len(rows) > len(shown):
        result["note"] = f"Showing the {len(shown)} most overdue of {len(rows)}. Totals cover all of them."
    return result


def get_buyer_summary(workspace_id: int, buyer_name: str, as_of: str | None = None) -> dict[str, Any]:
    """One buyer's contact details, totals, payment habit and open invoices."""
    found = resolve_buyer(workspace_id, buyer_name)   # only finds buyers in this workspace
    if "error" in found:
        return found
    buyer_id = found["buyer"]["id"]
    as_of_day = _as_of(workspace_id, as_of)
    params = {"buyer_id": buyer_id, "as_of": as_of_day}

    contact = query("SELECT name, contact_name, phone, email FROM buyers WHERE id = %(buyer_id)s",
                    params)[0]

    totals = query(
        """
        SELECT count(*)                                                AS invoice_count,
               sum(amount)                                             AS total_billed,
               sum(amount_paid)                                        AS total_paid,
               sum(balance)                                            AS outstanding,
               count(*)     FILTER (WHERE balance > 0 AND due_date < %(as_of)s) AS overdue_count,
               sum(balance) FILTER (WHERE balance > 0 AND due_date < %(as_of)s) AS overdue_balance
        FROM invoice_balances
        WHERE buyer_id = %(buyer_id)s
        """,
        params,
    )[0]

    # Payment habit: for fully paid invoices, how many days after the due date did the last payment land?
    habit = query(
        """
        SELECT count(*) AS paid_invoice_count,
               round(avg(last_paid - due_date)) AS avg_days_after_due
        FROM (
            SELECT i.due_date, max(p.paid_on) AS last_paid
            FROM invoices i
            JOIN payments p ON p.invoice_id = i.id
            WHERE i.buyer_id = %(buyer_id)s
            GROUP BY i.id, i.due_date, i.amount
            HAVING sum(p.amount) >= i.amount
        ) fully_paid
        """,
        params,
    )[0]

    last_payment = query(
        """
        SELECT max(p.paid_on) AS last_payment_date
        FROM payments p JOIN invoices i ON i.id = p.invoice_id
        WHERE i.buyer_id = %(buyer_id)s
        """,
        params,
    )[0]["last_payment_date"]

    open_invoices = query(
        """
        SELECT invoice_no, issue_date, due_date, amount, amount_paid, balance, status,
               GREATEST(%(as_of)s::date - due_date, 0) AS days_overdue
        FROM invoice_balances
        WHERE buyer_id = %(buyer_id)s AND balance > 0
        ORDER BY due_date
        """,
        params,
    )

    avg_late = habit["avg_days_after_due"]
    return {
        "as_of": as_of_day.isoformat(),
        "buyer": contact["name"],
        "contact": {"name": contact["contact_name"], "phone": contact["phone"], "email": contact["email"]},
        "totals": {
            "invoice_count": totals["invoice_count"],
            "total_billed": _money(totals["total_billed"]),
            "total_paid": _money(totals["total_paid"]),
            "outstanding": _money(totals["outstanding"]),
            "overdue_count": totals["overdue_count"],
            "overdue_balance": _money(totals["overdue_balance"]),
        },
        "payment_habit": {
            "fully_paid_invoices": habit["paid_invoice_count"],
            # negative = pays early on average; None = has never fully paid an invoice
            "avg_days_after_due": int(avg_late) if avg_late is not None else None,
            "last_payment_date": last_payment.isoformat() if last_payment else None,
        },
        "open_invoices": [
            {"invoice_no": r["invoice_no"], "invoice_date": r["issue_date"].isoformat(),
             "due_date": r["due_date"].isoformat(), "days_overdue": r["days_overdue"], "amount": _money(r["amount"]),
             "paid": _money(r["amount_paid"]), "balance": _money(r["balance"]), "status": r["status"]}
            for r in open_invoices[:MAX_ROWS]
        ],
    }


# Ranking is chosen from this fixed list. Column names can't be passed as %s
# parameters, so we never put text from the LLM into SQL; we only pick one of these.
_RANK_BY = {
    "outstanding": "outstanding",
    "overdue": "overdue_balance",
}


def get_invoice(workspace_id: int, invoice_no: str, as_of: str | None = None) -> dict[str, Any]:
    """Everything about one invoice, found by its number, including every payment against it."""
    as_of_day = _as_of(workspace_id, as_of)
    rows = query(
        """
        SELECT invoice_id, invoice_no, buyer_name, issue_date, due_date, amount, amount_paid,
               balance, status, GREATEST(%(as_of)s::date - due_date, 0) AS days_overdue
        FROM invoice_balances
        WHERE workspace_id = %(ws)s AND upper(invoice_no) = upper(%(no)s)
        """,
        {"ws": workspace_id, "no": plain_text(invoice_no).strip(), "as_of": as_of_day},
    )
    if not rows:
        return {"error": f"No invoice numbered {invoice_no}."}
    r = rows[0]
    payments = query("SELECT paid_on, amount, reference FROM payments WHERE invoice_id = %(id)s "
                     "ORDER BY paid_on", {"id": r["invoice_id"]})
    return {
        "as_of": as_of_day.isoformat(), "invoice_no": r["invoice_no"], "buyer": r["buyer_name"],
        "invoice_date": r["issue_date"].isoformat(), "due_date": r["due_date"].isoformat(),
        "days_overdue": r["days_overdue"] if r["balance"] > 0 else 0,
        "amount": _money(r["amount"]), "paid": _money(r["amount_paid"]),
        "balance": _money(r["balance"]), "status": r["status"],
        "payments": [{"date": p["paid_on"].isoformat(), "amount": _money(p["amount"]),
                      "reference": p["reference"]} for p in payments],
    }


def get_top_debtors(workspace_id: int, rank_by: str = "outstanding", limit: int = 10,
                    as_of: str | None = None) -> dict[str, Any]:
    """Buyers ranked by how much they owe, with totals across all buyers.

    rank_by: "outstanding" (everything unpaid, due or not) or "overdue" (only past due).
    limit: how many buyers to list (1 to 20).
    """
    if rank_by not in _RANK_BY:
        return {"error": f"rank_by must be one of {sorted(_RANK_BY)}"}
    as_of_day = _as_of(workspace_id, as_of)
    limit = min(max(1, int(limit)), 20)

    rows = query(
        f"""
        SELECT buyer_name,
               count(*) FILTER (WHERE balance > 0)                              AS open_invoices,
               sum(balance)                                                     AS outstanding,
               COALESCE(sum(balance) FILTER (WHERE balance > 0
                                             AND due_date < %(as_of)s), 0)      AS overdue_balance,
               max(%(as_of)s::date - due_date) FILTER (WHERE balance > 0
                                             AND due_date < %(as_of)s)          AS oldest_overdue_days
        FROM invoice_balances
        WHERE workspace_id = %(ws)s
        GROUP BY buyer_id, buyer_name
        HAVING sum(balance) > 0
        ORDER BY {_RANK_BY[rank_by]} DESC
        """,
        {"ws": workspace_id, "as_of": as_of_day},
    )
    ranked = [r for r in rows if rank_by == "outstanding" or r["overdue_balance"] > 0]
    return {
        "as_of": as_of_day.isoformat(),
        "ranked_by": rank_by,
        "buyers_owing_count": len(ranked),
        "total_outstanding_all_buyers": _money(sum((r["outstanding"] for r in rows), Decimal(0))),
        "total_overdue_all_buyers": _money(sum((r["overdue_balance"] for r in rows), Decimal(0))),
        "buyers": [
            {"buyer": r["buyer_name"], "open_invoices": r["open_invoices"],
             "outstanding": _money(r["outstanding"]), "overdue": _money(r["overdue_balance"]),
             "oldest_overdue_days": r["oldest_overdue_days"]}
            for r in ranked[:limit]
        ],
    }


# ------------------------------------------------------------ contract search (RAG)

def search_contract(workspace_id: int, buyer_name: str, question: str, top_k: int = 3) -> dict[str, Any]:
    """Find the contract clauses most relevant to a question, for one buyer only.

    The question is embedded and compared with each clause's embedding using
    cosine distance (pgvector's <=> operator). Smaller distance = closer meaning.
    """
    found = resolve_buyer(workspace_id, buyer_name)
    if "error" in found:
        return found
    buyer = found["buyer"]
    vector = to_pgvector(embed_query(question))
    rows = query(
        """
        SELECT source_file, content,
               1 - (embedding <=> %(q)s::vector) AS similarity
        FROM contract_chunks
        WHERE buyer_id = %(buyer_id)s            -- filter first: only this buyer's contract
        ORDER BY embedding <=> %(q)s::vector
        LIMIT %(k)s
        """,
        {"q": vector, "buyer_id": buyer["id"], "k": min(max(1, int(top_k)), 5)},
    )
    if not rows:
        return {"error": f"No contract is on file for {buyer['name']}. "
                         "Upload one from the Data panel."}
    return {
        "buyer": buyer["name"],
        "contract_file": rows[0]["source_file"],
        "clauses": [{"text": r["content"], "similarity": round(float(r["similarity"]), 3)}
                    for r in rows],
    }


# ------------------------------------------------------------ reminders (write path)

# LLMs often write typographic characters: non-breaking hyphens in "INV‑2026‑0012",
# curly apostrophes, non-breaking spaces. Normalise them so checks and matching work.
_TYPOGRAPHY = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                             "\u2014": "-", "\u2212": "-", "\u2018": "'", "\u2019": "'",
                             "\u201c": '"', "\u201d": '"', "\u00a0": " ", "\u202f": " "})


def plain_text(text: str) -> str:
    return text.translate(_TYPOGRAPHY)


TONE_MIN_DAYS = {"gentle": 1, "firm": 16, "final": 61}   # policy: how late before each tone is allowed
_RUPEES = re.compile(r"(?:₹|\brs\.?|\binr)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_DATE_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _dates_in(text: str) -> set[date]:
    """Find dates written like '27 May 2026', 'May 27, 2026' or '2026-05-27'."""
    found = set()
    for day, month, year in _DATE_DMY.findall(text):
        if month[:3].lower() in _MONTHS:
            found.add(date(int(year), _MONTHS[month[:3].lower()], int(day)))
    for month, day, year in _DATE_MDY.findall(text):
        if month[:3].lower() in _MONTHS:
            found.add(date(int(year), _MONTHS[month[:3].lower()], int(day)))
    for year, month, day in _DATE_ISO.findall(text):
        found.add(date(int(year), int(month), int(day)))
    return found


def validate_reminder(workspace_id: int, invoice_no: str, tone: str, message: str,
                      as_of: str | None = None) -> dict[str, Any]:
    """Deterministic checks on a drafted reminder BEFORE Meera ever sees it.

    The LLM wrote the message, so we don't trust it: every rupee amount and every
    percentage in the text is checked against the database and the contract.
    Returns the invoice facts on success, or {"error": ...} explaining what to fix.
    """
    if tone not in TONE_MIN_DAYS:
        return {"error": f"tone must be one of {list(TONE_MIN_DAYS)}"}
    message = plain_text(message).strip()
    rows = query(
        """
        SELECT ib.invoice_id, ib.invoice_no, ib.buyer_id, ib.buyer_name, ib.issue_date, ib.due_date,
               ib.amount, ib.balance, b.contact_name, b.email,
               (%(as_of)s::date - ib.due_date) AS days_overdue
        FROM invoice_balances ib JOIN buyers b ON b.id = ib.buyer_id
        WHERE ib.workspace_id = %(ws)s AND upper(ib.invoice_no) = upper(%(no)s)
        """,
        {"ws": workspace_id, "no": plain_text(invoice_no).strip(), "as_of": _as_of(workspace_id, as_of)},
    )
    if not rows:
        return {"error": f"Invoice {invoice_no} does not exist."}
    inv = rows[0]
    problems = []
    if inv["balance"] <= 0:
        problems.append("This invoice is fully paid. No reminder is needed.")
    if inv["days_overdue"] < 1:
        problems.append("This invoice is not overdue yet.")
    elif inv["days_overdue"] < TONE_MIN_DAYS[tone]:
        problems.append(f"A '{tone}' tone needs at least {TONE_MIN_DAYS[tone]} days overdue; "
                        f"this invoice is {inv['days_overdue']} days overdue.")
    if inv["invoice_no"].lower() not in message.lower():
        problems.append(f"The message must mention the invoice number {inv['invoice_no']}.")

    amounts = [Decimal(m.replace(",", "")) for m in _RUPEES.findall(message)]
    if inv["balance"] not in amounts:
        problems.append(f"The message must state the outstanding balance exactly "
                        f"(Rs {inv['balance']:,.0f}).")
    wrong = [a for a in amounts if a not in (inv["balance"], inv["amount"])]
    if wrong:
        problems.append(f"These amounts don't match this invoice: {[float(a) for a in wrong]}. "
                        f"Only use the balance or the invoice total.")

    percents = {Decimal(p) for p in _PERCENT.findall(message)}
    if percents:   # any percentage (e.g. a late fee) must appear in this buyer's contract
        contract = " ".join(r["content"] for r in query(
            "SELECT content FROM contract_chunks WHERE buyer_id = %(b)s", {"b": inv["buyer_id"]}))
        allowed = {Decimal(p) for p in _PERCENT.findall(contract)}   # so "2%" matches "2.0%"
        for p in sorted(percents - allowed):
            problems.append(f"{p}% does not appear in {inv['buyer_name']}'s contract. "
                            f"Check it with contract_search or leave it out.")
    try:
        bad_dates = _dates_in(message) - {inv["issue_date"], inv["due_date"]}
    except ValueError:   # e.g. "31 February": not a real date
        bad_dates = {"an invalid date"}
    if bad_dates:
        problems.append(f"Dates {sorted(str(d) for d in bad_dates)} are not this invoice's dates. "
                        f"Invoice date is {inv['issue_date']}, due date is {inv['due_date']}. "
                        f"Say clearly which one you mean.")
    if len(message) > 1200:
        problems.append("The message is too long. Keep it under 1200 characters.")

    if problems:
        return {"error": "Draft rejected by validation. Fix these and try again.",
                "problems": problems}
    return {"invoice_id": inv["invoice_id"], "invoice_no": inv["invoice_no"],
            "buyer": inv["buyer_name"], "contact": inv["contact_name"], "email": inv["email"],
            "balance": _money(inv["balance"]), "invoice_date": inv["issue_date"].isoformat(),
            "due_date": inv["due_date"].isoformat(),
            "days_overdue": inv["days_overdue"], "message": message}


def save_pending_reminder(workspace_id: int, invoice_id: int, tone: str, message: str) -> int:
    """Store the draft as 'pending'. Safe to call twice for the same invoice.

    LangGraph re-runs a tool from the top when it resumes after an approval pause,
    so this WILL be called twice. The unique "one pending reminder per invoice" index
    turns the second call into an update instead of a duplicate row, and the audit
    entry is only written when a row was really inserted (xmax = 0 means "new row").
    """
    with transaction() as conn:
        row = conn.execute(
            """
            INSERT INTO reminders (invoice_id, tone, draft_text)
            VALUES (%(inv)s, %(tone)s, %(msg)s)
            ON CONFLICT (invoice_id) WHERE status = 'pending'
            DO UPDATE SET tone = EXCLUDED.tone, draft_text = EXCLUDED.draft_text
            RETURNING id, (xmax = 0) AS inserted
            """,
            {"inv": invoice_id, "tone": tone, "msg": message},
        ).fetchone()
        if row["inserted"]:
            conn.execute(
                "INSERT INTO audit_log (workspace_id, actor, action, details) "
                "VALUES (%(w)s, 'agent', 'draft_reminder', %(d)s)",
                {"w": workspace_id,
                 "d": json.dumps({"reminder_id": row["id"], "invoice_id": invoice_id, "tone": tone})},
            )
    return row["id"]


def record_decision(reminder_id: int, decision: dict[str, Any]) -> dict[str, Any]:
    """Apply Meera's decision: approve (optionally with her edited text) or reject."""
    action = decision.get("action")
    if action not in ("approve", "reject"):
        return {"error": f"Unknown decision {action!r}"}
    status = "approved" if action == "approve" else "rejected"
    with transaction() as conn:
        row = conn.execute(
            """
            UPDATE reminders r
            SET status = %(status)s,
                draft_text = COALESCE(%(edited)s, r.draft_text),
                decided_at = now(),
                decided_by = %(by)s
            FROM invoices i
            WHERE r.id = %(id)s AND r.status = 'pending' AND i.id = r.invoice_id
            RETURNING r.id, r.draft_text, i.workspace_id
            """,
            {"status": status, "edited": decision.get("edited_message"), "id": reminder_id,
             "by": decision.get("decided_by")},   # None from the terminal chat and evals
        ).fetchone()
        if row is None:
            return {"error": "This reminder was already decided."}
        conn.execute(
            "INSERT INTO audit_log (workspace_id, actor, action, details) VALUES (%(w)s, 'owner', %(a)s, %(d)s)",
            {"w": row["workspace_id"], "a": f"{action}_reminder",
             "d": json.dumps({"reminder_id": reminder_id, "user_id": decision.get("decided_by"),
                              "edited": decision.get("edited_message") is not None,
                              "reason": decision.get("reason")})},
        )
    if status == "approved":
        return {"status": "approved", "reminder_id": reminder_id, "final_message": row["draft_text"],
                "note": "Saved as approved. This demo does not actually send messages."}
    return {"status": "rejected", "reminder_id": reminder_id, "reason": decision.get("reason"),
            "instruction": "Meera rejected this draft. Do NOT propose another reminder unless she "
                           "asks. Tell her it was rejected and ask what she would like changed."}
