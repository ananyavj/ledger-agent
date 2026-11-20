"""Chat with the agent in the terminal, including approving reminder drafts.

Run from the project root:   python -m scripts.chat
Resume an earlier conversation (e.g. a reminder you chose to decide "later"):
                              python -m scripts.chat --thread <id>

Conversation state is saved in Postgres, so a paused approval survives closing
this program. Every turn is traced to Langfuse.
"""
import argparse
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from app.agent import build_graph, postgres_checkpointer
from app.config import LANGFUSE_PUBLIC_KEY

MAX_STEPS = 12   # hard stop, so a confused agent can't loop (and spend tokens) forever


def make_callbacks() -> list:
    """Langfuse tracing if keys are set; otherwise run without it."""
    if not LANGFUSE_PUBLIC_KEY:
        print("(Langfuse keys not set: running without tracing)")
        return []
    from langfuse.langchain import CallbackHandler
    return [CallbackHandler()]


def make_config(thread_id: str, callbacks: list) -> dict:
    return {
        "configurable": {"thread_id": thread_id},          # which conversation to load and save
        "recursion_limit": MAX_STEPS,
        "callbacks": callbacks,
        "metadata": {"langfuse_session_id": thread_id},    # groups this chat's turns in Langfuse
    }


def pending_approvals(graph, config) -> list:
    """Interrupts the graph is currently paused on (one per reminder awaiting a decision)."""
    state = graph.get_state(config)
    return [intr for task in state.tasks for intr in task.interrupts]


def ask_decision(draft: dict) -> dict | None:
    """Show a draft and ask Meera what to do. Returns None for 'decide later'."""
    print("\n------------- Reminder waiting for your approval -------------")
    print(f"To: {draft['contact']} <{draft['email']}>  |  {draft['buyer']}")
    print(f"Invoice {draft['invoice_no']} (issued {draft.get('invoice_date', '?')}, due {draft['due_date']})  |  "
          f"balance Rs {draft['balance']:,.0f}  |  {draft['days_overdue']} days overdue  |  "
          f"tone: {draft['tone']}")
    print("--------------------------------------------------------------")
    print(draft["message"])
    print("--------------------------------------------------------------")
    while True:
        choice = input("[a]pprove, [e]dit, [r]eject, or decide [l]ater? ").strip().lower()
        if choice == "a":
            return {"action": "approve"}
        if choice == "e":
            print("Type the new message. Finish with a line containing only a dot (.)")
            lines = []
            while (line := input()) != ".":
                lines.append(line)
            return {"action": "approve", "edited_message": "\n".join(lines)}
        if choice == "r":
            return {"action": "reject", "reason": input("Reason (optional): ").strip() or None}
        if choice == "l":
            return None


def run(graph, payload, config) -> dict | None:
    """Invoke the graph once and print the tools it chose. Returns None if it hit the step limit."""
    seen = len(graph.get_state(config).values.get("messages", []))
    try:
        result = graph.invoke(payload, config)
    except GraphRecursionError:
        print(f"Agent: I couldn't finish within {MAX_STEPS} steps. Try a narrower question.")
        return None
    for msg in result["messages"][seen:]:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls:
                args = {k: (v[:60] + "...") if isinstance(v, str) and len(v) > 60 else v
                        for k, v in call["args"].items()}
                print(f"  [tool] {call['name']}({args})")
    return result


def handle_approvals(graph, config) -> bool:
    """Resolve any paused reminders. Returns False if Meera chose to decide later."""
    while interrupts := pending_approvals(graph, config):
        decisions = {}
        for intr in interrupts:
            decision = ask_decision(intr.value)
            if decision is None:
                print(f"\nOK, it stays pending. Resume later with:\n"
                      f"  python -m scripts.chat --thread {config['configurable']['thread_id']}")
                return False
            decisions[intr.id] = decision
        # One pause: resume with the decision itself. Several at once: map each pause id to its decision.
        resume = next(iter(decisions.values())) if len(decisions) == 1 else decisions
        result = run(graph, Command(resume=resume), config)
        if result is None:
            return True
        if not pending_approvals(graph, config):
            print(f"Agent: {result['messages'][-1].content}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread", help="resume an earlier conversation by its id")
    args = parser.parse_args()

    graph = build_graph(postgres_checkpointer())
    callbacks = make_callbacks()
    thread_id = args.thread or str(uuid.uuid4())
    config = make_config(thread_id, callbacks)
    print(f"Conversation {thread_id}\nAsk about Meera's receivables. Type 'exit' to quit.")

    if args.thread and not handle_approvals(graph, config):
        return

    while True:
        question = input("\nYou: ").strip()
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue
        result = run(graph, {"messages": [HumanMessage(question)]}, config)
        if result is None:
            # The saved conversation now ends mid-loop, so start a fresh one rather
            # than let the half-finished attempt confuse later answers.
            config = make_config(str(uuid.uuid4()), callbacks)
            print("(Started a fresh conversation.)")
            continue
        if pending_approvals(graph, config):
            if not handle_approvals(graph, config):
                break
        else:
            print(f"Agent: {result['messages'][-1].content}")

    if callbacks:
        from langfuse import get_client
        get_client().flush()   # send any traces still waiting in the buffer before exiting


if __name__ == "__main__":
    main()
