"""Run the evals and write a results report.

Run from the project root:
  python -m scripts.run_evals --retrieval        # search quality only: no LLM, free, ~1 minute
  python -m scripts.run_evals --agent            # the full agent: uses Groq quota, ~20-30 minutes
  python -m scripts.run_evals --agent --only contract --limit 3   # a quick subset

Results go to evals/results/ as JSON (every detail) and latest.md (summary table).

Grading is deterministic (string and number checks), not "LLM as judge": it costs
nothing, gives the same verdict every time, and can't be talked round by a fluent answer.
The trade-off is that it can't judge tone or style; reminder drafts are judged by
the validation layer and, finally, by Meera.
"""
import argparse
import json
import re
import statistics
import time
import uuid
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app import tools as t
from app.agent import build_graph
from app.config import LANGFUSE_PUBLIC_KEY, LLM_MODEL
from evals.cases import AgentCase, agent_cases, retrieval_cases

RESULTS_DIR = Path(__file__).resolve().parent.parent / "evals" / "results"
# Evals always run in the demo workspace, with its fixed date, so answers never drift
DEMO_CONTEXT = {"workspace_id": t.DEMO_WORKSPACE_ID, "workspace_name": "Meera Packaging Solutions",
                "as_of": "2026-11-15"}

# Paid list prices for gpt-oss-120b, USD per million tokens. Our runs are on the free tier,
# so this is what the same traffic WOULD cost. Update if you switch model or prices change.
PRICE_IN_PER_M = 0.15
PRICE_OUT_PER_M = 0.75


# ------------------------------------------------------------------ retrieval

def run_retrieval() -> dict:
    cases = retrieval_cases()
    print(f"Retrieval eval: {len(cases)} questions, top 3 clauses each")
    hits1 = hits3 = 0
    reciprocal_ranks, latencies = [], []
    per_section: dict[str, list[int]] = {}
    misses = []
    for c in cases:
        start = time.perf_counter()
        result = t.search_contract(t.DEMO_WORKSPACE_ID, c.buyer, c.question, top_k=3)
        latencies.append(time.perf_counter() - start)
        titles = [clause["text"].splitlines()[0] for clause in result.get("clauses", [])]
        rank = next((i for i, title in enumerate(titles, start=1)
                     if title.endswith(f". {c.expected_section}")), None)
        hits1 += rank == 1
        hits3 += rank is not None
        reciprocal_ranks.append(1 / rank if rank else 0)
        per_section.setdefault(c.expected_section, []).append(1 if rank == 1 else 0)
        if rank != 1:
            misses.append({"buyer": c.buyer, "question": c.question, "expected": c.expected_section,
                           "got_top": titles[0] if titles else None})
    n = len(cases)
    return {
        "questions": n,
        "hit_at_1": round(hits1 / n, 3),
        "hit_at_3": round(hits3 / n, 3),
        "mrr": round(sum(reciprocal_ranks) / n, 3),
        "hit_at_1_by_section": {k: round(sum(v) / len(v), 3) for k, v in per_section.items()},
        "median_search_ms": round(statistics.median(latencies) * 1000, 1),
        "misses": misses,
    }


# ------------------------------------------------------------------ grading

def normalise(text: str) -> str:
    """Plain characters (curly ' -> ', non-breaking hyphen -> -), lower-case, and no thousands
    separators, so '₹65,05,954' contains '6505954' and "can’t" matches "can't"."""
    return re.sub(r"(?<=\d),(?=\d)", "", t.plain_text(text)).lower()


def numbers_in(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", normalise(text))]


def contains(answer: str, expected: str) -> bool:
    """Numbers compare by value ('1.0' matches '1%'); text compares case-insensitively."""
    if re.fullmatch(r"\d+(?:\.\d+)?", expected):
        return float(expected) in numbers_in(answer)
    return expected.lower() in normalise(answer)


def grade(case: AgentCase, answer: str, tools_called: list[str], approvals: list[dict]) -> list[str]:
    """Return the list of failed checks (empty list = pass)."""
    fails = []
    if case.expected_tools and not set(case.expected_tools) & set(tools_called):
        fails.append(f"expected one of {list(case.expected_tools)}, called {tools_called}")
    for item in case.must_include:
        if not contains(answer, item):
            fails.append(f"missing {item!r}")
    if case.must_include_any and not any(contains(answer, x) for x in case.must_include_any):
        fails.append("none of the expected phrases appeared")
    for item in case.must_not_include:
        if item.lower() in normalise(answer):
            fails.append(f"must not say {item!r}")
    if case.no_rupee_amounts and re.search(r"(₹|\brs\.?\s?)\s*\d", answer, re.IGNORECASE):
        fails.append("quoted an amount instead of asking which buyer")
    if case.expect_approval and not approvals:
        fails.append("no draft reached Meera (validation never passed)")
    if case.forbidden_tone and any(a.get("tone") == case.forbidden_tone for a in approvals):
        fails.append(f"a '{case.forbidden_tone}' draft got past the tone policy")
    return fails


# ------------------------------------------------------------------ agent

def _json_or_empty(text) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def run_case(graph, case: AgentCase, callbacks: list, run_id: str) -> dict:
    config = {
        "configurable": {"thread_id": f"eval-{run_id}-{case.id}", **DEMO_CONTEXT},
        "recursion_limit": 12,
        "callbacks": callbacks,
        "metadata": {"langfuse_session_id": f"eval-{run_id}", "langfuse_tags": ["eval", case.category]},
    }
    approvals = []
    start = time.perf_counter()
    result = graph.invoke({"messages": [HumanMessage(case.question)]}, config)
    # If a draft reached the approval step, record it and reject it so the run can finish
    for _ in range(3):
        pending = [i for task in graph.get_state(config).tasks for i in task.interrupts]
        if not pending:
            break
        approvals += [p.value for p in pending]
        decision = {"action": "reject", "reason": "automated eval run"}
        resume = decision if len(pending) == 1 else {p.id: decision for p in pending}
        result = graph.invoke(Command(resume=resume), config)
    seconds = time.perf_counter() - start

    ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
    tools_called = [call["name"] for m in ai_messages for call in m.tool_calls]
    tokens_in = sum((m.usage_metadata or {}).get("input_tokens", 0) for m in ai_messages)
    tokens_out = sum((m.usage_metadata or {}).get("output_tokens", 0) for m in ai_messages)
    answer = result["messages"][-1].content if result["messages"] else ""
    # What the draft checks objected to, if anything (useful when a reminder needs retries)
    validation_problems = [p for m in result["messages"]
                           if isinstance(m, ToolMessage) and m.name == "propose_reminder"
                           for p in _json_or_empty(m.content).get("problems", [])]
    fails = grade(case, answer, tools_called, approvals)
    return {
        "id": case.id, "category": case.category, "question": case.question,
        "passed": not fails, "failures": fails, "answer": answer,
        "tools_called": tools_called, "tool_selection_ok": (not case.expected_tools) or
        bool(set(case.expected_tools) & set(tools_called)),
        "llm_calls": len(ai_messages), "seconds": round(seconds, 2),
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cost_usd": round(tokens_in / 1e6 * PRICE_IN_PER_M + tokens_out / 1e6 * PRICE_OUT_PER_M, 6),
        "drafts_reaching_approval": len(approvals),
        "draft_attempts": tools_called.count("propose_reminder"),
        "validation_problems": validation_problems,
    }


def run_agent(only: str | None, limit: int | None, pause: float) -> dict:
    cases = [c for c in agent_cases() if not only or c.category == only]
    if limit:
        cases = cases[:limit]
    graph = build_graph(InMemorySaver())   # fresh, throwaway state: each case starts clean
    callbacks = []
    if LANGFUSE_PUBLIC_KEY:
        from langfuse.langchain import CallbackHandler
        callbacks = [CallbackHandler()]
    run_id = uuid.uuid4().hex[:8]
    print(f"Agent eval: {len(cases)} cases on {LLM_MODEL} (run {run_id}). "
          f"Pausing {pause:.0f}s between cases to stay under Groq's free per-minute limit.")

    results = []
    for n, case in enumerate(cases, start=1):
        for attempt in (1, 2):
            try:
                r = run_case(graph, case, callbacks, run_id)
                break
            except Exception as exc:   # noqa: BLE001  (we want to record any failure and carry on)
                if attempt == 1 and "rate" in str(exc).lower():
                    print("  rate limited, waiting 60s and retrying once...")
                    time.sleep(60)
                    continue
                r = {"id": case.id, "category": case.category, "question": case.question,
                     "passed": False, "failures": [f"error: {exc.__class__.__name__}: {str(exc)[:200]}"],
                     "answer": "", "tools_called": [], "tool_selection_ok": False, "llm_calls": 0,
                     "seconds": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0,
                     "drafts_reaching_approval": 0, "draft_attempts": 0, "validation_problems": []}
                break
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{n:2}/{len(cases)}] {mark} {r['id']:12} {r['seconds']:5.1f}s "
              f"{r['tokens_in'] + r['tokens_out']:6} tok  {'; '.join(r['failures'])[:90]}")
        if n < len(cases):
            time.sleep(pause)

    if LANGFUSE_PUBLIC_KEY:
        from langfuse import get_client
        get_client().flush()
    return summarise(results)


def summarise(results: list[dict]) -> dict:
    ok = [r for r in results if r["seconds"] > 0]   # exclude crashed cases from timing and cost
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    with_tools = [r for r in results if r["category"] in ("data", "contract", "reminder")]
    reminder_cases = [r for r in results if r["category"] == "reminder" and r["draft_attempts"]]
    secs = sorted(r["seconds"] for r in ok) or [0]
    return {
        "model": LLM_MODEL,
        "cases": len(results),
        "accuracy": round(sum(r["passed"] for r in results) / len(results), 3),
        "accuracy_by_category": {k: f"{sum(r['passed'] for r in v)}/{len(v)}" for k, v in by_cat.items()},
        "tool_selection_accuracy": round(sum(r["tool_selection_ok"] for r in with_tools) / len(with_tools), 3)
        if with_tools else None,
        "latency_p50_s": round(statistics.median(secs), 2),
        "latency_p95_s": round(secs[min(len(secs) - 1, int(0.95 * len(secs)))], 2),
        "avg_llm_calls": round(statistics.mean(r["llm_calls"] for r in ok), 2) if ok else 0,
        "avg_tokens_per_question": round(statistics.mean(r["tokens_in"] + r["tokens_out"] for r in ok)) if ok else 0,
        "avg_cost_per_question_usd": round(statistics.mean(r["cost_usd"] for r in ok), 6) if ok else 0,
        "reminder_drafts_passing_validation_first_try": f"{sum(1 for r in reminder_cases if r['drafts_reaching_approval'] and r['draft_attempts'] == 1)}/{len(reminder_cases)}",
        "results": results,
    }


# ------------------------------------------------------------------ report

def write_report(report: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"eval-{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                                     encoding="utf-8")
    lines = [f"# Eval results ({stamp})", ""]
    if "retrieval" in report:
        r = report["retrieval"]
        lines += ["## Retrieval (contract search, no LLM)", "",
                  "| Metric | Value |", "|---|---|",
                  f"| Questions | {r['questions']} |", f"| Hit@1 | {r['hit_at_1']:.1%} |",
                  f"| Hit@3 | {r['hit_at_3']:.1%} |", f"| MRR | {r['mrr']} |",
                  f"| Median search time | {r['median_search_ms']} ms |", ""]
        lines += ["Hit@1 by section: " + ", ".join(f"{k} {v:.0%}" for k, v in r["hit_at_1_by_section"].items()), ""]
    if "agent" in report:
        a = report["agent"]
        lines += [f"## Agent ({a['model']})", "", "| Metric | Value |", "|---|---|",
                  f"| Cases | {a['cases']} |", f"| Accuracy | {a['accuracy']:.1%} |",
                  f"| By category | {', '.join(f'{k} {v}' for k, v in a['accuracy_by_category'].items())} |",
                  f"| Tool selection accuracy | {a['tool_selection_accuracy']:.1%} |",
                  f"| Latency p50 / p95 | {a['latency_p50_s']}s / {a['latency_p95_s']}s |",
                  f"| Avg LLM calls per question | {a['avg_llm_calls']} |",
                  f"| Avg tokens per question | {a['avg_tokens_per_question']} |",
                  f"| Est. cost per question (paid prices) | ${a['avg_cost_per_question_usd']:.5f} |",
                  f"| Reminder drafts passing validation first try | {a['reminder_drafts_passing_validation_first_try']} |",
                  "", "### Failures", ""]
        lines += [f"- **{r['id']}**: {'; '.join(r['failures'])}" for r in a["results"] if not r["passed"]] or ["None"]
    path = RESULTS_DIR / "latest.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval", action="store_true", help="run the search eval (free)")
    parser.add_argument("--agent", action="store_true", help="run the agent eval (uses Groq)")
    parser.add_argument("--only", choices=["data", "contract", "clarify", "refuse", "reminder"])
    parser.add_argument("--limit", type=int, help="run only the first N agent cases")
    parser.add_argument("--pause", type=float, default=20, help="seconds between agent cases")
    args = parser.parse_args()
    if not (args.retrieval or args.agent):
        parser.error("choose --retrieval, --agent, or both")

    report: dict = {"run_at": datetime.now().isoformat(timespec="seconds")}
    if args.retrieval:
        report["retrieval"] = run_retrieval()
        r = report["retrieval"]
        print(f"Hit@1 {r['hit_at_1']:.1%}  Hit@3 {r['hit_at_3']:.1%}  MRR {r['mrr']}")
    if args.agent:
        report["agent"] = run_agent(args.only, args.limit, args.pause)
        a = report["agent"]
        print(f"\nAccuracy {a['accuracy']:.1%} ({a['accuracy_by_category']})  "
              f"tool selection {a['tool_selection_accuracy']}  p50 {a['latency_p50_s']}s  "
              f"avg {a['avg_tokens_per_question']} tokens  ~${a['avg_cost_per_question_usd']:.5f}/question")
    print(f"\nReport: {write_report(report)}")


if __name__ == "__main__":
    main()
