"""Generate Meera's sample business data and load it into Postgres.

Run from the project root:   python -m scripts.seed_data

What it creates
  - 40 buyers, each with their own payment terms and payment habit
  - ~8 months of invoices and the payments made against them
  - one written contract per buyer in data/contracts/ (for RAG later)
  - data/ground_truth.json: the true contract terms, used by our evals

Two properties matter:
  Deterministic: a fixed random SEED and a fixed AS_OF date mean every run
                 produces identical data, so eval answers never drift.
  Safe to re-run: all business tables are wiped and refilled inside ONE
                  transaction. If anything fails, nothing is half-written.
"""
import json
import random
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg

from app.config import DATABASE_URL

SEED = 42
AS_OF = date(2026, 11, 15)   # "today" in Meera's world. Never use date.today() here.
HISTORY_DAYS = 240           # invoices go back about 8 months
SUPPLIER = "Meera Packaging Solutions"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONTRACTS_DIR = DATA_DIR / "contracts"

BUYER_NAMES = [
    "Sharma Traders", "Kulkarni Foods", "Deshmukh Pharma Distributors",
    "Patil Sweets & Farsan", "Joshi Electricals", "Shree Ganesh Bakery",
    "Gupta Hardware Mart", "Mehta Textiles", "Iyer Spice Exports",
    "Khan Dry Fruits", "Bhosale Agro Products", "Rao Ayurvedics",
    "Pawar Dairy", "Agarwal Garments", "Naik Cosmetics", "Sai Krupa Snacks",
    "Jadhav Auto Parts", "Fernandes Bakehouse", "Chopra Home Decor",
    "Reddy Organics", "Shinde Tea Company", "Verma Toys", "Gaikwad Chemicals",
    "Mahalaxmi Pickles", "Banerjee Books & Stationery", "Thakur Footwear",
    "Pune Fresh Kitchens", "Kapoor Gifts", "Sawant Hardware",
    "Nair Coconut Products", "Desai Pharmaceuticals", "Chavan Paper Mart",
    "Om Sai Distributors", "Bansal Electronics", "Lokhande Masala",
    "Hegde Coffee Roasters", "Malhotra Leather Goods", "Kale Organic Farms",
    "Saraf Jewellers", "Wagh Engineering Works",
]
FIRST_NAMES = ["Rahul", "Priya", "Amit", "Sneha", "Vikram", "Anjali", "Rohan",
               "Kavita", "Suresh", "Neha", "Arjun", "Pooja", "Manoj", "Divya"]
LAST_NAMES = ["Sharma", "Kulkarni", "Patil", "Joshi", "Iyer", "Khan",
              "Deshpande", "Rao", "Mehta", "Nair", "Gupta", "Shinde"]

# How each kind of buyer pays. Weights = share of buyers with that habit.
BEHAVIOUR_WEIGHTS = {"prompt": 0.35, "slow": 0.30, "partial": 0.20, "chronic": 0.15}

NUMBER_WORDS = {1: "one", 5: "five", 7: "seven", 10: "ten", 15: "fifteen",
                30: "thirty", 45: "forty-five", 60: "sixty"}


@dataclass
class Terms:
    """The true payment terms written into a buyer's contract."""
    credit_days: int
    late_fee_pct_per_month: float | None   # None = contract has no late fee
    grace_days: int
    early_discount_pct: float | None       # None = no early-payment discount
    early_discount_days: int | None
    dispute_days: int


@dataclass
class Buyer:
    name: str
    contact_name: str
    phone: str
    email: str
    behaviour: str
    terms: Terms
    contract_file: str


@dataclass
class Invoice:
    buyer_index: int
    issue_date: date
    due_date: date
    amount: Decimal
    payments: list[tuple[date, Decimal]]
    invoice_no: str = ""


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def n_words(n: int) -> str:
    """30 -> 'thirty (30)', the way Indian contracts usually write numbers."""
    return f"{NUMBER_WORDS[n]} ({n})"


# ---------------------------------------------------------------- generation

def make_buyers(rng: random.Random) -> list[Buyer]:
    behaviours = list(BEHAVIOUR_WEIGHTS)
    weights = list(BEHAVIOUR_WEIGHTS.values())
    buyers = []
    for name in BUYER_NAMES:
        has_discount = rng.random() < 0.3
        terms = Terms(
            credit_days=rng.choice([15, 30, 30, 45, 45, 60]),
            late_fee_pct_per_month=rng.choice([None, 1.0, 1.5, 1.5, 2.0]),
            grace_days=rng.choice([0, 0, 5, 7]),
            early_discount_pct=rng.choice([1.0, 2.0]) if has_discount else None,
            early_discount_days=rng.choice([7, 10]) if has_discount else None,
            dispute_days=rng.choice([7, 10, 15]),
        )
        slug = slugify(name)
        buyers.append(Buyer(
            name=name,
            contact_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            phone=f"+91 9{rng.randrange(10**8, 10**9)}",
            email=f"accounts@{slug}.in",
            behaviour=rng.choices(behaviours, weights)[0],
            terms=terms,
            contract_file=f"{slug}.md",
        ))
    return buyers


def make_payments(rng: random.Random, behaviour: str, due: date,
                  amount: Decimal) -> list[tuple[date, Decimal]]:
    """Simulate how this buyer pays one invoice. Payments after AS_OF haven't happened yet."""
    planned: list[tuple[date, Decimal]] = []
    if behaviour == "prompt":
        planned.append((due + timedelta(days=rng.randint(-5, 3)), amount))
    elif behaviour == "slow":
        planned.append((due + timedelta(days=rng.randint(5, 35)), amount))
    elif behaviour == "partial":
        first = (amount * Decimal(rng.uniform(0.3, 0.7))).quantize(Decimal("1"))
        first_day = due + timedelta(days=rng.randint(0, 20))
        planned.append((first_day, first))
        if rng.random() < 0.5:   # half the time, the rest arrives much later
            planned.append((first_day + timedelta(days=rng.randint(20, 60)), amount - first))
    elif behaviour == "chronic":
        if rng.random() < 0.4:   # usually doesn't pay at all
            planned.append((due + timedelta(days=rng.randint(40, 90)), amount))
    return [(d, a) for d, a in planned if d <= AS_OF]


def make_invoices(rng: random.Random, buyers: list[Buyer]) -> list[Invoice]:
    start = AS_OF - timedelta(days=HISTORY_DAYS)
    invoices = []
    for i, buyer in enumerate(buyers):
        for _ in range(rng.randint(4, 10)):
            issue = start + timedelta(days=rng.randint(0, HISTORY_DAYS))
            due = issue + timedelta(days=buyer.terms.credit_days)
            amount = Decimal(rng.randrange(80, 2500) * 100)   # Rs 8,000 to Rs 2,49,900
            invoices.append(Invoice(i, issue, due, amount,
                                    make_payments(rng, buyer.behaviour, due, amount)))
    # Number invoices in date order across all buyers, like a real billing book
    invoices.sort(key=lambda inv: (inv.issue_date, inv.buyer_index))
    for n, inv in enumerate(invoices, start=1):
        inv.invoice_no = f"INV-{inv.issue_date.year}-{n:04d}"
    return invoices


# ----------------------------------------------------------------- contracts

def render_contract(rng: random.Random, buyer: Buyer) -> str:
    """Write a contract. Clause wording varies on purpose, so search can't rely on exact phrases."""
    t = buyer.terms
    signed = date(2025, 1, 1) + timedelta(days=rng.randint(0, 330))

    payment = rng.choice([
        f"The Buyer shall pay each invoice in full within {n_words(t.credit_days)} days of the invoice date.",
        f"Payment terms are net {t.credit_days} days from the date of invoice.",
        f"All invoices are payable no later than {n_words(t.credit_days)} calendar days after they are issued.",
    ])

    if t.late_fee_pct_per_month is None:
        late = ("No interest or late payment charges shall apply to delayed payments. However, the "
                "Supplier may suspend further deliveries if any invoice remains unpaid for more than "
                "sixty (60) days after its due date.")
    else:
        grace = (f" A grace period of {n_words(t.grace_days)} days after the due date shall apply "
                 f"before any such charge is levied." if t.grace_days else "")
        late = rng.choice([
            f"Any amount that remains unpaid after the due date shall attract interest at "
            f"{t.late_fee_pct_per_month}% per month until it is paid in full.",
            f"Overdue invoices will be charged a late payment fee of {t.late_fee_pct_per_month} "
            f"percent per month on the outstanding balance.",
        ]) + grace

    if t.early_discount_pct is not None:
        discount = (f"If the Buyer pays an invoice in full within {n_words(t.early_discount_days)} "
                    f"days of the invoice date, the Buyer may deduct a prompt payment discount of "
                    f"{t.early_discount_pct}% of the invoice value.")
    else:
        discount = rng.choice(["No discount is offered for early payment.", ""])

    clauses = [
        ("Scope of supply", "The Supplier will supply corrugated boxes, printed cartons and related "
         "packaging materials against purchase orders issued by the Buyer from time to time."),
        ("Pricing", "Prices are as per the quotation accepted in each purchase order and are "
         "exclusive of GST, which shall be charged at the applicable rate."),
        ("Payment terms", payment),
        ("Late payment", late),
        ("Early payment", discount),   # dropped below when empty
        ("Disputes", f"Any complaint about the quantity or quality of goods must be raised in "
         f"writing within {n_words(t.dispute_days)} days of delivery. Amounts that are not under "
         f"dispute must still be paid by the due date."),
        ("Termination", "Either party may end this agreement with thirty (30) days' written notice. "
         "Invoices raised before termination remain payable on their original terms."),
        ("Governing law", "This agreement is governed by the laws of India, and the courts at "
         "Pune, Maharashtra shall have exclusive jurisdiction."),
    ]
    clauses = [(title, body) for title, body in clauses if body]
    sections = [
        f"# Supply Agreement: {SUPPLIER} and {buyer.name}",
        f"This Supply Agreement is made on {signed:%d %B %Y} between {SUPPLIER}, Bhosari, Pune "
        f"(\"Supplier\") and {buyer.name} (\"Buyer\").",
    ] + [f"## {n}. {title}\n{body}" for n, (title, body) in enumerate(clauses, start=1)]
    return "\n\n".join(sections) + "\n"


def write_files(rng: random.Random, buyers: list[Buyer]) -> None:
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    for old in CONTRACTS_DIR.glob("*.md"):
        old.unlink()
    for buyer in buyers:
        (CONTRACTS_DIR / buyer.contract_file).write_text(render_contract(rng, buyer), encoding="utf-8")

    truth = {
        "as_of": AS_OF.isoformat(),
        "seed": SEED,
        "buyers": [{"name": b.name, "contract_file": b.contract_file,
                    "behaviour": b.behaviour, **asdict(b.terms)} for b in buyers],
    }
    (DATA_DIR / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ database

def load_into_db(buyers: list[Buyer], invoices: list[Invoice]) -> None:
    # `with psycopg.connect(...)` commits if the block finishes, rolls back if it raises.
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("""
            TRUNCATE audit_log, reminders, contract_chunks, payments, invoices, buyers
            RESTART IDENTITY CASCADE
        """)
        buyer_ids = []
        for b in buyers:
            cur.execute(
                "INSERT INTO buyers (name, contact_name, phone, email) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (b.name, b.contact_name, b.phone, b.email),   # %s placeholders: never f-strings in SQL
            )
            buyer_ids.append(cur.fetchone()[0])

        for inv in invoices:
            cur.execute(
                "INSERT INTO invoices (buyer_id, invoice_no, issue_date, due_date, amount) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (buyer_ids[inv.buyer_index], inv.invoice_no, inv.issue_date, inv.due_date, inv.amount),
            )
            invoice_id = cur.fetchone()[0]
            for n, (paid_on, amount) in enumerate(inv.payments, start=1):
                cur.execute(
                    "INSERT INTO payments (invoice_id, paid_on, amount, reference) "
                    "VALUES (%s, %s, %s, %s)",
                    (invoice_id, paid_on, amount, f"UPI-{inv.invoice_no}-{n}"),
                )


def summarise(buyers: list[Buyer], invoices: list[Invoice]) -> None:
    unpaid = [inv for inv in invoices if sum(a for _, a in inv.payments) < inv.amount]
    overdue = [inv for inv in unpaid if inv.due_date < AS_OF]
    outstanding = sum(inv.amount - sum(a for _, a in inv.payments) for inv in unpaid)
    print(f"As of {AS_OF}: {len(buyers)} buyers, {len(invoices)} invoices, "
          f"{sum(len(i.payments) for i in invoices)} payments")
    print(f"Not fully paid: {len(unpaid)} invoices, Rs {outstanding:,.0f} outstanding")
    print(f"Overdue: {len(overdue)} invoices")
    print(f"Contracts written to {CONTRACTS_DIR}")


def main() -> None:
    rng = random.Random(SEED)   # own generator, so nothing else can disturb the sequence
    buyers = make_buyers(rng)
    invoices = make_invoices(rng, buyers)
    write_files(rng, buyers)
    load_into_db(buyers, invoices)
    summarise(buyers, invoices)


if __name__ == "__main__":
    main()
