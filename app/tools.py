"""The agent's tools: plain Python functions that run SQL we wrote and tested.

They know nothing about LangGraph or the LLM. That keeps them easy to test on their
own, and the agent layer will wrap them later.

Rules every tool follows:
  - Returns a small, JSON-friendly dict. The LLM reads it, so it must be compact.
  - Never guesses which buyer was meant. Unclear names come back as an error with
    candidates, and the agent asks Meera.
  - Never returns more than MAX_ROWS rows, but always returns true totals, so the
    agent can answer "how much in total?" without seeing every invoice.
"""
import difflib
from datetime import date
from decimal import Decimal
from typing import Any

from app.config import AS_OF_DATE
from app.db import query

MAX_ROWS = 15


def _money(value: Decimal | None) -> float:
    """Decimal -> float for JSON. Safe here: we only display money, never add it up in Python."""
    return float(value or 0)


def _as_of(as_of: str | None) -> date:
    return date.fromisoformat(as_of or AS_OF_DATE)


def resolve_buyer(name: str) -> dict[str, Any]:
    """Turn whatever the user typed into exactly one buyer, or explain why we can't.

    Order of checks: exact match, then "contained in" match, then close spellings.
    Returns {"buyer": {...}} on success, or {"error": ..., "candidates": [...]}.
    """
    buyers = query("SELECT id, name FROM buyers ORDER BY name")
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


def get_overdue_invoices(min_days_overdue: int = 1, buyer_name: str | None = None,
                         as_of: str | None = None) -> dict[str, Any]:
    """Invoices past their due date that are not fully paid, most overdue first.

    min_days_overdue: only include invoices at least this many days late (default 1).
    buyer_name: optionally limit to one buyer.
    """
    as_of_day = _as_of(as_of)
    min_days = max(1, int(min_days_overdue))   # 0 or negative would mean "not overdue yet"

    buyer_id = None
    if buyer_name:
        found = resolve_buyer(buyer_name)
        if "error" in found:
            return found
        buyer_id = found["buyer"]["id"]

    rows = query(
        """
        SELECT invoice_no, buyer_name, due_date, amount, amount_paid, balance, status,
               (%(as_of)s::date - due_date) AS days_overdue
        FROM invoice_balances
        WHERE balance > 0
          AND (%(as_of)s::date - due_date) >= %(min_days)s
          AND (%(buyer_id)s::int IS NULL OR buyer_id = %(buyer_id)s::int)
        ORDER BY days_overdue DESC, balance DESC
        """,
        {"as_of": as_of_day, "min_days": min_days, "buyer_id": buyer_id},
    )
    shown = rows[:MAX_ROWS]
    result: dict[str, Any] = {
        "as_of": as_of_day.isoformat(),
        "overdue_invoice_count": len(rows),
        "total_overdue_balance": _money(sum((r["balance"] for r in rows), Decimal(0))),
        "invoices": [
            {"invoice_no": r["invoice_no"], "buyer": r["buyer_name"],
             "due_date": r["due_date"].isoformat(), "days_overdue": r["days_overdue"],
             "amount": _money(r["amount"]), "paid": _money(r["amount_paid"]),
             "balance": _money(r["balance"]), "status": r["status"]}
            for r in shown
        ],
    }
    if len(rows) > len(shown):
        result["note"] = f"Showing the {len(shown)} most overdue of {len(rows)}. Totals cover all of them."
    return result


def get_buyer_summary(buyer_name: str, as_of: str | None = None) -> dict[str, Any]:
    """One buyer's contact details, totals, payment habit and open invoices."""
    found = resolve_buyer(buyer_name)
    if "error" in found:
        return found
    buyer_id = found["buyer"]["id"]
    as_of_day = _as_of(as_of)
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
            {"invoice_no": r["invoice_no"], "due_date": r["due_date"].isoformat(),
             "days_overdue": r["days_overdue"], "amount": _money(r["amount"]),
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


def get_top_debtors(rank_by: str = "outstanding", limit: int = 10,
                    as_of: str | None = None) -> dict[str, Any]:
    """Buyers ranked by how much they owe, with totals across all buyers.

    rank_by: "outstanding" (everything unpaid, due or not) or "overdue" (only past due).
    limit: how many buyers to list (1 to 20).
    """
    if rank_by not in _RANK_BY:
        return {"error": f"rank_by must be one of {sorted(_RANK_BY)}"}
    as_of_day = _as_of(as_of)
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
        GROUP BY buyer_id, buyer_name
        HAVING sum(balance) > 0
        ORDER BY {_RANK_BY[rank_by]} DESC
        """,
        {"as_of": as_of_day},
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
