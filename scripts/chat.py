"""Chat with the agent in the terminal.

Run from the project root:   python -m scripts.chat

Each run is one conversation (one "thread"), so follow-up questions like
"and how much of that is from Sharma?" work. Every turn is traced to Langfuse.
"""
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from app.agent import build_graph
from app.config import LANGFUSE_PUBLIC_KEY

MAX_STEPS = 10   # hard stop, so a confused agent can't loop (and spend tokens) forever


def make_callbacks() -> list:
    """Langfuse tracing if keys are set; otherwise run without it."""
    if not LANGFUSE_PUBLIC_KEY:
        print("(Langfuse keys not set: running without tracing)")
        return []
    from langfuse.langchain import CallbackHandler
    return [CallbackHandler()]


def main() -> None:
    graph = build_graph()
    thread_id = str(uuid.uuid4())
    callbacks = make_callbacks()
    config = {
        "configurable": {"thread_id": thread_id},     # which conversation to load and save
        "recursion_limit": MAX_STEPS,
        "callbacks": callbacks,
        "metadata": {"langfuse_session_id": thread_id},  # groups this chat's turns in Langfuse
    }
    print("Ask about Meera's receivables. Type 'exit' to quit.")

    while True:
        question = input("\nYou: ").strip()
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue

        seen = len(graph.get_state(config).values.get("messages", []))
        try:
            result = graph.invoke({"messages": [HumanMessage(question)]}, config)
        except GraphRecursionError:
            # The saved conversation now ends mid-loop, so start a fresh one
            # rather than let the half-finished attempt confuse later answers.
            thread_id = str(uuid.uuid4())
            config["configurable"]["thread_id"] = thread_id
            config["metadata"]["langfuse_session_id"] = thread_id
            print(f"Agent: I couldn't finish within {MAX_STEPS} steps, so I've started a "
                  f"fresh conversation. Try a narrower question.")
            continue

        # Show which tools the agent decided to call this turn
        for msg in result["messages"][seen:]:
            if isinstance(msg, AIMessage):
                for call in msg.tool_calls:
                    print(f"  [tool] {call['name']}({call['args']})")
        print(f"Agent: {result['messages'][-1].content}")

    if callbacks:
        from langfuse import get_client
        get_client().flush()   # send any traces still waiting in the buffer before exiting


if __name__ == "__main__":
    main()
