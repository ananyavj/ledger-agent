"""Import a business's own buyers, invoices and payments from CSV files.

This is what onboarding a real client looks like: their exports never match your schema.
So the importer is forgiving about FORMAT and strict about MEANING:
  - Forgiving: column names in any common wording ("Customer", "Invoice Number"), Excel's
    hidden BOM, dates like 05/11/2026 or 5 Nov 2026, amounts like "₹1,24,347.50".
  - Strict: every row is checked first (missing fields, impossible dates, unknown buyers,
    duplicates). If ANY row has an error, NOTHING is imported and you get a report of
    every problem with its row number. Fix the file and upload again.
  - Safe to repeat: existing buyers and invoices are updated, and a payment already
    imported is skipped, so uploading the same file twice changes nothing.
"""
import csv
import difflib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.db import query, transaction

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_REPORTED = 50

REQUIRED = {
    "buyers": ["name"],
    "invoices": ["invoice_no", "buyer_name", "issue_date", "due_date", "amount"],
    "payments": ["invoice_no", "paid_on", "amount"],
}
OPTIONAL = {
    "buyers": ["contact_name", "phone", "email"],
    "invoices": [],
    "payments": ["reference"],
}
# Other names people use for each column (after lower-casing and replacing spaces with _)
ALIASES = {
    "buyers": {"buyer": "name", "buyer_name": "name", "customer": "name", "customer_name": "name",
               "company": "name", "party": "name", "party_name": "name", "contact": "contact_name",
               "contact_person": "contact_name", "mobile": "phone", "phone_number": "phone",
               "email_id": "email", "e_mail": "email"},
    "invoices": {"invoice": "invoice_no", "invoice_number": "invoice_no", "bill_no": "invoice_no",
                 "invoice_id": "invoice_no", "buyer": "buyer_name", "customer": "buyer_name",
                 "customer_name": "buyer_name", "party": "buyer_name", "party_name": "buyer_name",
                 "invoice_date": "issue_date", "bill_date": "issue_date", "date": "issue_date",
                 "due": "due_date", "payment_due": "due_date", "total": "amount",
                 "invoice_amount": "amount", "amount_inr": "amount", "invoice_value": "amount"},
    "payments": {"invoice": "invoice_no", "invoice_number": "invoice_no", "bill_no": "invoice_no",
                 "date": "paid_on", "payment_date": "paid_on", "date_paid": "paid_on",
                 "received_on": "paid_on", "amount_paid": "amount", "amount_inr": "amount",
                 "ref": "reference", "utr": "reference", "transaction_id": "reference",
                 "reference_no": "reference"},
}
TEMPLATES = {
    "buyers": "name,contact_name,phone,email\nSharma Traders,Amit Shinde,+91 98200 00000,accounts@sharmatraders.in\n",
    "invoices": "invoice_no,buyer_name,issue_date,due_date,amount\nINV-001,Sharma Traders,2026-10-01,2026-10-31,124347.50\n",
    "payments": "invoice_no,paid_on,amount,reference\nINV-001,2026-11-02,50000,UTR123456\n",
}
# Day first, as Indian businesses write dates (05/11/2026 is 5 November)
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y",
                "%d-%b-%Y", "%d-%b-%y", "%d/%m/%y", "%d %b, %Y"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ImportError_(ValueError):
    """The whole file is unusable (wrong type, missing columns, too big)."""


@dataclass
class Report:
    kind: str
    rows: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    skipped_duplicates: int = 0

    def error(self, row: int, message: str) -> None:
        self.errors.append({"row": row, "message": message})

    def warn(self, row: int, message: str) -> None:
        self.warnings.append({"row": row, "message": message})

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ok": not self.errors, "rows": self.rows,
                "error_count": len(self.errors), "errors": self.errors[:MAX_REPORTED],
                "warnings": self.warnings[:MAX_REPORTED], "created": self.created,
                "updated": self.updated, "skipped_duplicates": self.skipped_duplicates}


# ------------------------------------------------------------------ parsing helpers

def _header_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def parse_date(text: str) -> date:
    value = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{text}' isn't a date I recognise. Use YYYY-MM-DD or DD/MM/YYYY.")


def parse_amount(text: str) -> Decimal:
    cleaned = re.sub(r"(?i)₹|\binr\b|\brs\.?|,|\s", "", text.strip())
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"'{text}' isn't an amount.")
    if amount <= 0:
        raise ValueError("Amounts must be more than zero.")
    return amount.quantize(Decimal("0.01"))


def read_rows(kind: str, data: bytes) -> list[tuple[int, dict[str, str]]]:
    """Decode, map the header to our column names, and return (row number, values)."""
    if kind not in REQUIRED:
        raise ImportError_(f"Unknown import type '{kind}'.")
    if len(data) > MAX_BYTES:
        raise ImportError_("That file is over 2 MB. Split it into smaller files.")
    try:
        text = data.decode("utf-8-sig")   # -sig strips the invisible BOM Excel adds
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")   # older Excel exports on Windows
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ImportError_("The file is empty.")
    aliases = ALIASES[kind]
    columns = [aliases.get(_header_key(h), _header_key(h)) for h in header]
    missing = [c for c in REQUIRED[kind] if c not in columns]
    if missing:
        raise ImportError_(f"Missing column(s): {', '.join(missing)}. "
                           f"Expected: {', '.join(REQUIRED[kind] + OPTIONAL[kind])}.")
    rows = []
    for n, values in enumerate(reader, start=2):   # row 1 is the header, as in Excel
        if not any(v.strip() for v in values):
            continue   # skip blank lines
        rows.append((n, {col: (values[i].strip() if i < len(values) else "")
                         for i, col in enumerate(columns)}))
        if len(rows) > MAX_ROWS:
            raise ImportError_(f"More than {MAX_ROWS} rows. Split the file.")
    if not rows:
        raise ImportError_("The file has a header but no rows.")
    return rows


def _suggest(value: str, choices: dict[str, Any]) -> str:
    close = difflib.get_close_matches(value.lower(), list(choices), n=1, cutoff=0.75)
    return f" Did you mean '{choices[close[0]]['label']}'?" if close else ""


# ------------------------------------------------------------------ validation (no writes)

def plan_buyers(rows, report: Report) -> list[dict]:
    seen, plan = set(), []
    for n, r in rows:
        name = r.get("name", "")
        if not name:
            report.error(n, "Buyer name is empty.")
            continue
        if name.lower() in seen:
            report.error(n, f"'{name}' appears more than once in this file.")
            continue
        seen.add(name.lower())
        email = r.get("email") or None
        if email and not _EMAIL.match(email):
            report.error(n, f"'{email}' isn't a valid email address.")
            continue
        plan.append({"name": name, "contact_name": r.get("contact_name") or None,
                     "phone": r.get("phone") or None, "email": email})
    return plan


def plan_invoices(rows, report: Report, buyers: dict[str, dict]) -> list[dict]:
    seen, plan = set(), []
    for n, r in rows:
        problems = []
        no = r.get("invoice_no", "").strip()
        buyer = r.get("buyer_name", "").strip()
        if not no:
            problems.append("Invoice number is empty.")
        elif no.upper() in seen:
            problems.append(f"Invoice {no} appears more than once in this file.")
        if not buyer:
            problems.append("Buyer name is empty.")
        elif buyer.lower() not in buyers:
            problems.append(f"Unknown buyer '{buyer}'. Import it in buyers.csv first."
                            + _suggest(buyer, buyers))
        try:
            issue = parse_date(r.get("issue_date", ""))
            due = parse_date(r.get("due_date", ""))
            if due < issue:
                problems.append(f"Due date {due} is before invoice date {issue}.")
        except ValueError as e:
            problems.append(str(e))
        try:
            amount = parse_amount(r.get("amount", ""))
        except ValueError as e:
            problems.append(str(e))
        if problems:
            for p in problems:
                report.error(n, p)
            continue
        seen.add(no.upper())
        plan.append({"invoice_no": no, "buyer_id": buyers[buyer.lower()]["id"],
                     "issue_date": issue, "due_date": due, "amount": amount})
    return plan


def plan_payments(rows, report: Report, invoices: dict[str, dict]) -> list[dict]:
    plan, adding = [], {}
    for n, r in rows:
        problems = []
        no = r.get("invoice_no", "").strip()
        inv = invoices.get(no.upper()) if no else None
        if not no:
            problems.append("Invoice number is empty.")
        elif inv is None:
            problems.append(f"Unknown invoice '{no}'. Import it in invoices.csv first."
                            + _suggest(no.upper(), invoices))
        try:
            paid_on = parse_date(r.get("paid_on", ""))
        except ValueError as e:
            problems.append(str(e))
        try:
            amount = parse_amount(r.get("amount", ""))
        except ValueError as e:
            problems.append(str(e))
        if problems:
            for p in problems:
                report.error(n, p)
            continue
        if paid_on < inv["issue_date"]:
            report.warn(n, f"Payment on {paid_on} is before invoice {no} was issued ({inv['issue_date']}).")
        adding[inv["id"]] = adding.get(inv["id"], Decimal(0)) + amount
        if inv["paid"] + adding[inv["id"]] > inv["amount"]:
            report.warn(n, f"Payments for {no} now exceed its amount (overpaid).")
        plan.append({"invoice_id": inv["id"], "paid_on": paid_on, "amount": amount,
                     "reference": r.get("reference") or None})
    return plan


# ------------------------------------------------------------------ the import itself

def import_csv(workspace_id: int, kind: str, data: bytes, user_id: int | None = None) -> dict:
    rows = read_rows(kind, data)
    report = Report(kind=kind, rows=len(rows))

    if kind == "buyers":
        plan = plan_buyers(rows, report)
    elif kind == "invoices":
        buyers = {r["name"].lower(): {"id": r["id"], "label": r["name"]} for r in query(
            "SELECT id, name FROM buyers WHERE workspace_id = %(w)s", {"w": workspace_id})}
        plan = plan_invoices(rows, report, buyers)
    else:
        invoices = {r["invoice_no"].upper(): {**r, "label": r["invoice_no"]} for r in query(
            """SELECT invoice_id AS id, invoice_no, issue_date, amount, amount_paid AS paid
               FROM invoice_balances WHERE workspace_id = %(w)s""", {"w": workspace_id})}
        plan = plan_payments(rows, report, invoices)

    if report.errors:
        return report.as_dict()   # all or nothing: one bad row means nothing is written

    with transaction() as conn:
        for item in plan:
            if kind == "buyers":
                row = conn.execute(
                    """INSERT INTO buyers (workspace_id, name, contact_name, phone, email)
                       VALUES (%(w)s, %(name)s, %(contact_name)s, %(phone)s, %(email)s)
                       ON CONFLICT (workspace_id, lower(name)) DO UPDATE SET
                         contact_name = COALESCE(EXCLUDED.contact_name, buyers.contact_name),
                         phone = COALESCE(EXCLUDED.phone, buyers.phone),
                         email = COALESCE(EXCLUDED.email, buyers.email)
                       RETURNING (xmax = 0) AS inserted""", {"w": workspace_id, **item}).fetchone()
            elif kind == "invoices":
                row = conn.execute(
                    """INSERT INTO invoices (workspace_id, buyer_id, invoice_no, issue_date, due_date, amount)
                       VALUES (%(w)s, %(buyer_id)s, %(invoice_no)s, %(issue_date)s, %(due_date)s, %(amount)s)
                       ON CONFLICT (workspace_id, upper(invoice_no)) DO UPDATE SET
                         buyer_id = EXCLUDED.buyer_id, issue_date = EXCLUDED.issue_date,
                         due_date = EXCLUDED.due_date, amount = EXCLUDED.amount
                       RETURNING (xmax = 0) AS inserted""", {"w": workspace_id, **item}).fetchone()
            else:
                row = conn.execute(
                    """INSERT INTO payments (invoice_id, paid_on, amount, reference)
                       VALUES (%(invoice_id)s, %(paid_on)s, %(amount)s, %(reference)s)
                       ON CONFLICT DO NOTHING
                       RETURNING true AS inserted""", item).fetchone()
            if row is None:
                report.skipped_duplicates += 1   # payment already imported
            elif row["inserted"]:
                report.created += 1
            else:
                report.updated += 1
        conn.execute(
            "INSERT INTO audit_log (workspace_id, actor, action, details) VALUES (%s, 'user', %s, %s)",
            (workspace_id, f"import_{kind}",
             json.dumps({"user_id": user_id, "rows": report.rows, "created": report.created,
                         "updated": report.updated, "skipped": report.skipped_duplicates})),
        )
    return report.as_dict()
