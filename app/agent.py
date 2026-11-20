"""The agent: an LLM in a loop with our tools, built as a small LangGraph graph.

        START -> agent --(LLM asked for a tool?)-- yes --> tools --+
                   ^                                               |
                   +-----------------------------------------------+
                   |
                   +-- no (final answer) --> END

The LLM only ever DECIDES which tool to call and with what arguments.
Our code (the "tools" node) is what actually runs anything.
"""
import json

from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import interrupt

from app import tools as t
from app.config import AS_OF_DATE, DATABASE_URL, GROQ_API_KEY, LLM_MODEL


# ---------------------------------------------------------------- tools
# The docstring IS the tool description the LLM reads to decide when to use it.
# Writing these well is a big part of making an agent reliable.
# Note: the date is not a parameter. The LLM must never be able to change "today".

@tool
def overdue_invoices(min_days_overdue: int = 1, buyer_name: str | None = None) -> str:
    """List invoices that are past their due date and not fully paid, most overdue first.

    Use for questions like "who owes me money?", "what is overdue?",
    "who is more than 30 days late?". Returns the true count and total of ALL
    overdue invoices, plus details of the 15 most overdue.

    Args:
        min_days_overdue: only include invoices at least this many days late. Default 1.
        buyer_name: optional. Limit the list to one buyer.
    """
    return json.dumps(t.get_overdue_invoices(min_days_overdue=min_days_overdue,
                                             buyer_name=buyer_name))


@tool
def buyer_summary(buyer_name: str) -> str:
    """Everything about one buyer: contact details, total billed, paid and outstanding,
    overdue amount, payment habit, last payment date, and their unpaid invoices.

    Use when the user asks about a specific buyer, e.g. "how is Sharma Traders doing?"
    or "how much does Kulkarni Foods owe?".

    Args:
        buyer_name: the buyer's name, or part of it, as the user wrote it.
    """
    return json.dumps(t.get_buyer_summary(buyer_name))


@tool
def top_debtors(rank_by: str = "outstanding", limit: int = 10) -> str:
    """Rank BUYERS by how much money they owe, with totals across all buyers.

    Use for questions like "who owes me the most?", "my biggest defaulters",
    "how much am I owed in total?". Prefer this over calling buyer_summary
    for many buyers one by one.

    Args:
        rank_by: "outstanding" for everything unpaid (due or not yet due),
                 or "overdue" for only amounts past their due date.
        limit: how many buyers to list, 1 to 20. Default 10.
    """
    return json.dumps(t.get_top_debtors(rank_by=rank_by, limit=limit))


@tool
def invoice_details(invoice_no: str) -> str:
    """Look up ONE invoice by its number: buyer, invoice and due dates, amount, amount paid,
    balance, days overdue and every payment made against it.

    Use whenever the user mentions a specific invoice number, e.g. "INV-2026-0146".

    Args:
        invoice_no: e.g. "INV-2026-0146".
    """
    return json.dumps(t.get_invoice(invoice_no))


@tool
def contract_search(buyer_name: str, question: str) -> str:
    """Search ONE buyer's written contract and return the most relevant clauses.

    Use for any question about agreed terms: payment period, late fees or interest,
    grace period, early-payment discounts, disputes, termination.

    Args:
        buyer_name: the buyer's name, or part of it, as the user wrote it.
        question: what to look for, in plain words, e.g. "late payment fee".
    """
    return json.dumps(t.search_contract(buyer_name, question), ensure_ascii=False)


@tool
def propose_reminder(invoice_no: str, tone: str, message: str) -> str:
    """Draft a payment reminder for ONE overdue invoice and hand it to Meera for approval.
    Nothing is ever sent without her approval. She can approve, edit or reject it.

    Before calling: get the invoice's exact balance and days overdue from
    invoice_details, buyer_summary or overdue_invoices. If you mention a late fee, check it with
    contract_search first. Call this for one invoice at a time.

    Args:
        invoice_no: e.g. "INV-2026-0066".
        tone: "gentle" (any overdue invoice), "firm" (16+ days overdue)
              or "final" (61+ days overdue).
        message: the full reminder, addressed to the buyer's contact person by name,
                 stating the invoice number and the exact outstanding balance in rupees,
                 signed "Meera, Meera Packaging Solutions".
    """
    facts = t.validate_reminder(invoice_no, tone, message)   # deterministic guardrails
    if "error" in facts:
        return json.dumps(facts)   # the LLM reads the problems and can fix its draft

    message = facts.pop("message")   # the validated text, with typographic characters normalised
    reminder_id = t.save_pending_reminder(facts["invoice_id"], tone, message)

    # PAUSE: the graph stops here and saves its state. It continues only when the app
    # resumes it with Meera's decision, which becomes the return value of interrupt().
    # On resume this whole function runs again from the top, which is why the steps
    # above must be safe to repeat (validation only reads; the save is idempotent).
    decision = interrupt({"reminder_id": reminder_id, "tone": tone, "message": message, **facts})

    return json.dumps(t.record_decision(reminder_id, decision), ensure_ascii=False)


TOOLS = [overdue_invoices, buyer_summary, top_debtors, invoice_details, contract_search,
         propose_reminder]


# ---------------------------------------------------------------- prompt

SYSTEM_PROMPT = f"""You are the accounts assistant for Meera Packaging Solutions, a small \
packaging business in Pune. You help the owner, Meera, keep track of money her buyers owe.

Today's date is {AS_OF_DATE}.

Rules:
1. Every amount, date, invoice number and name you state must come from a tool result in \
this conversation. Never estimate or invent figures.
2. If a tool returns an "error" with "candidates", do not choose one yourself. Ask Meera \
which buyer she means and list the candidates.
3. If your tools cannot answer the question, say so plainly instead of guessing.
4. Write money in Indian format with the rupee sign, for example ₹1,24,347.
5. Be brief: give the answer first, then at most five supporting details.
6. You cannot send anything yourself. propose_reminder only saves a draft for Meera's \
approval. Never say a reminder was sent.
7. Whenever Meera names or refers to a buyer, call a tool with the name exactly as she \
wrote it, even if you looked up a similar buyer earlier. Never decide yourself which \
buyer a partial name like "pharma" means.
8. Read tool results carefully. If a field is null, say that information isn't available.
9. For questions about contract terms, use contract_search and quote the relevant clause. \
If the returned clauses don't cover the question, say the contract doesn't specify it.
10. If propose_reminder returns problems, fix the draft and try again (at most twice).
11. When you mention a date, say exactly what it is: "invoice date" (when the bill was \
issued) or "due date" (when payment was due). Never call a due date the invoice date.
12. If Meera rejects a draft, stop. Do not propose another reminder unless she asks for one."""


# ---------------------------------------------------------------- graph

def postgres_checkpointer():
    """Save conversation state in Postgres, so a paused approval survives restarts.

    In-memory state disappears when the process stops. A reminder might wait hours for
    Meera's decision, so the paused graph must be stored durably. The pool reuses
    connections instead of opening a new one per step.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        DATABASE_URL, max_size=5, open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    saver = PostgresSaver(pool)
    saver.setup()   # creates LangGraph's checkpoint tables the first time; safe to repeat
    return saver


def build_graph(checkpointer=None):
    """Assemble and compile the agent graph.

    checkpointer: where conversation state is saved. Defaults to in-memory (fine for
    tests); the chat app and API pass postgres_checkpointer().
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is empty. Add it to your .env file.")

    # temperature=0: the same question gets (nearly) the same tool choice every time.
    llm = ChatGroq(model=LLM_MODEL, temperature=0, api_key=GROQ_API_KEY, max_retries=2)
    llm_with_tools = llm.bind_tools(TOOLS)   # sends the tool descriptions with every call

    def agent(state: MessagesState) -> dict:
        # The system prompt is added on every call but never stored in the conversation,
        # so changing the prompt later doesn't leave old copies in saved history.
        response = llm_with_tools.invoke([SystemMessage(SYSTEM_PROMPT)] + state["messages"])
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(TOOLS))     # runs whatever tool calls the LLM asked for
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)   # tool calls -> "tools", else -> END
    graph.add_edge("tools", "agent")             # after tools run, the LLM reads the results
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
