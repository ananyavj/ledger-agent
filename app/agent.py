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

from app import tools as t
from app.config import AS_OF_DATE, GROQ_API_KEY, LLM_MODEL


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


TOOLS = [overdue_invoices, buyer_summary, top_debtors]


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
6. You cannot send messages or change any records. You can only look things up.
7. Whenever Meera names or refers to a buyer, call a tool with the name exactly as she \
wrote it, even if you looked up a similar buyer earlier. Never decide yourself which \
buyer a partial name like "pharma" means.
8. Read tool results carefully. If a field is null, say that information isn't available."""


# ---------------------------------------------------------------- graph

def build_graph(checkpointer=None):
    """Assemble and compile the agent graph.

    checkpointer: where conversation state is saved between turns. In-memory for now;
    we'll switch to Postgres when the agent needs to pause for Meera's approval.
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
