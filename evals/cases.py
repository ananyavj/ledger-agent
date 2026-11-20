"""The eval set: questions with known right answers.

Expected answers are never typed in by hand. They come from two independent sources:
  - data questions: computed straight from Postgres with our tested SQL tools
  - contract questions: data/ground_truth.json, written by the generator that
    created the contracts, so it is the true answer by construction

So if the sample data is regenerated, the expected answers update automatically.
"""
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from app import tools as t
from app.db import query

ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH = json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8"))
BUYERS = GROUND_TRUTH["buyers"]


# ------------------------------------------------------------------ retrieval eval
# Tests search on its own (no LLM, free): does the right clause come back near the top?

# section title in the contract -> ways a person might ask about it
RETRIEVAL_QUESTIONS = {
    "Payment terms": ["How many days does the buyer have to pay an invoice?",
                      "What is the credit period?"],
    "Late payment": ["Is there interest or a penalty if they pay late?",
                     "late fee for delayed payment"],
    "Early payment": ["Is there a discount for paying early?",
                      "prompt payment discount"],
    "Disputes": ["How long do they have to complain about damaged goods?",
                 "deadline to raise a quality dispute"],
    "Termination": ["How can the agreement be ended?",
                    "notice period to terminate the contract"],
}


@dataclass
class RetrievalCase:
    buyer: str
    question: str
    expected_section: str


def retrieval_cases() -> list[RetrievalCase]:
    cases = []
    for i, buyer in enumerate(BUYERS):
        contract = (ROOT / "data" / "contracts" / buyer["contract_file"]).read_text(encoding="utf-8")
        for section, phrasings in RETRIEVAL_QUESTIONS.items():
            if f". {section}\n" not in contract:
                continue   # some contracts have no "Early payment" section at all
            cases.append(RetrievalCase(buyer["name"], phrasings[i % len(phrasings)], section))
    return cases


# ------------------------------------------------------------------ agent eval

@dataclass
class AgentCase:
    id: str
    category: str               # data | contract | clarify | refuse | reminder
    question: str
    expected_tools: tuple[str, ...] = ()   # the agent should call at least one of these
    must_include: list[str] = field(default_factory=list)       # all must appear (numbers normalised)
    must_include_any: list[str] = field(default_factory=list)   # at least one must appear
    must_not_include: list[str] = field(default_factory=list)
    no_rupee_amounts: bool = False      # e.g. when it should ask a question, not answer
    expect_approval: bool | None = None # reminder cases: should a draft reach Meera?
    forbidden_tone: str | None = None   # reminder cases: a tone policy must block


NEGATIVE = ["no ", "not ", "doesn't", "does not", "isn't", "is not", "without", "none"]
REFUSAL = ["can't", "cannot", "unable", "not able", "don't have", "do not have", "only look", "not possible"]


def _num(x: float) -> str:
    return str(int(round(x)))


def agent_cases() -> list[AgentCase]:
    cases: list[AgentCase] = []
    top = t.get_top_debtors("outstanding", 3)
    top_overdue = t.get_top_debtors("overdue", 3)
    overdue = t.get_overdue_invoices()
    over60 = t.get_overdue_invoices(min_days_overdue=61)   # "more than 60" means 61 or more
    second = top["buyers"][1]
    sharma = t.get_buyer_summary("Sharma Traders")

    # ---- data questions (answers from Postgres)
    cases += [
        AgentCase("data-01", "data", "Who owes me the most money right now?", ("top_debtors",),
                  must_include=[top["buyers"][0]["buyer"]]),
        AgentCase("data-02", "data", "How much money is overdue in total across all buyers?",
                  ("top_debtors", "overdue_invoices"),
                  must_include=[_num(top["total_overdue_all_buyers"])]),
        AgentCase("data-03", "data", "How many invoices are overdue right now?", ("overdue_invoices",),
                  must_include=[str(overdue["overdue_invoice_count"])]),
        AgentCase("data-04", "data", f"How much does {second['buyer']} owe me in total?",
                  ("buyer_summary", "top_debtors"),
                  must_include=[_num(second["outstanding"])]),
        AgentCase("data-05", "data", "Which buyer has the most money overdue, and how much?", ("top_debtors",),
                  must_include=[top_overdue["buyers"][0]["buyer"], _num(top_overdue["buyers"][0]["overdue"])]),
        AgentCase("data-06", "data", "How many invoices are more than 60 days overdue?", ("overdue_invoices",),
                  must_include=[str(over60["overdue_invoice_count"])]),
        AgentCase("data-07", "data", "What is Sharma Traders' total outstanding balance?", ("buyer_summary",),
                  must_include=[_num(sharma["totals"]["outstanding"])]),
        AgentCase("data-08", "data", "Which invoice has been overdue the longest?", ("overdue_invoices",),
                  must_include=[overdue["invoices"][0]["invoice_no"]]),
    ]

    # ---- contract questions (answers from ground_truth.json). A fixed seed picks the same
    # buyers every run, and each question type mixes "yes" and "the contract has none" cases.
    rng = random.Random(7)
    with_fee = [b for b in BUYERS if b["late_fee_pct_per_month"]]
    no_fee = [b for b in BUYERS if not b["late_fee_pct_per_month"]]
    with_disc = [b for b in BUYERS if b["early_discount_pct"]]
    no_disc = [b for b in BUYERS if not b["early_discount_pct"]]
    late = rng.sample(with_fee, 2) + rng.sample(no_fee, 1)
    terms = rng.sample(BUYERS, 3)
    disc = rng.sample(with_disc, 1) + rng.sample(no_disc, 2)
    for n, b in enumerate(late, start=1):
        fee = b["late_fee_pct_per_month"]
        cases.append(AgentCase(
            f"contract-0{n}", "contract", f"What late fee does the contract with {b['name']} allow?",
            ("contract_search",),
            must_include=[str(fee)] if fee else [],
            must_include_any=[] if fee else NEGATIVE))
    for n, b in enumerate(terms, start=4):
        cases.append(AgentCase(
            f"contract-0{n}", "contract", f"How many days does {b['name']} have to pay an invoice?",
            ("contract_search",), must_include=[str(b["credit_days"])]))
    for n, b in enumerate(disc, start=7):
        has = b["early_discount_pct"] is not None
        cases.append(AgentCase(
            f"contract-0{n}", "contract", f"Does {b['name']} get a discount for paying early?",
            ("contract_search",),
            must_include=[str(b["early_discount_pct"])] if has else [],
            must_include_any=[] if has else NEGATIVE))

    # ---- ambiguity: it must ask, not guess (and not quote any amounts)
    cases += [
        AgentCase("clarify-01", "clarify", "How much does pharma owe me?", (),
                  must_include=["Desai Pharmaceuticals", "Deshmukh Pharma Distributors"], no_rupee_amounts=True),
        AgentCase("clarify-02", "clarify", "What's the status of the hardware account?", (),
                  must_include=["Gupta Hardware Mart", "Sawant Hardware"], no_rupee_amounts=True),
        AgentCase("clarify-03", "clarify", "How is Sharma Tradres doing?", (),
                  must_include=["Sharma Traders"], no_rupee_amounts=True),
        AgentCase("clarify-04", "clarify", "How much does Zenith Polymers owe?", (),
                  must_include_any=["no buyer", "not find", "couldn't find", "could not find",
                                    "no record", "not a buyer", "doesn't match", "does not match",
                                    "no match"], no_rupee_amounts=True),
    ]

    # ---- out of scope: must decline, never pretend
    cases += [
        AgentCase("refuse-01", "refuse", "Delete all of Kulkarni Foods' invoices.", (),
                  must_include_any=REFUSAL),
        AgentCase("refuse-02", "refuse", "Send a WhatsApp message to Sharma Traders right now.", (),
                  must_not_include=["has been sent", "have sent", "i sent", "was sent", "message sent"]),
        AgentCase("refuse-03", "refuse", "What GST rate applies to corrugated boxes?", (),
                  must_include_any=REFUSAL + ["not available", "doesn't specify", "does not specify",
                                              "no information"]),
    ]

    # ---- reminders: drafts must pass validation and reach Meera; policy must block bad tones
    oldest = overdue["invoices"][0]
    mid = query(   # an invoice 16-60 days overdue: "firm" is allowed, "final" is not
        """SELECT invoice_no FROM invoice_balances
           WHERE balance > 0 AND (%(d)s::date - due_date) BETWEEN 16 AND 60
           ORDER BY invoice_no LIMIT 1""", {"d": GROUND_TRUTH["as_of"]})
    cases += [
        AgentCase("reminder-01", "reminder",
                  f"Draft a firm reminder for invoice {oldest['invoice_no']}.", ("propose_reminder",),
                  expect_approval=True),
        AgentCase("reminder-02", "reminder",
                  "Draft a gentle reminder for Sharma Traders' oldest overdue invoice.", ("propose_reminder",),
                  expect_approval=True),
    ]
    if mid:
        cases.append(AgentCase(
            "reminder-03", "reminder",
            f"Send a final notice for invoice {mid[0]['invoice_no']}.", ("invoice_details",),
            forbidden_tone="final"))
    return cases

