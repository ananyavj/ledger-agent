import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import type { ChatMessage } from "../types";
import { Check, Copy, Spark } from "./Icons";

// Friendly names for the agent's tools, shown as small chips under an answer
const TOOL_LABELS: Record<string, string> = {
  top_debtors: "Ranked buyers",
  overdue_invoices: "Checked overdue invoices",
  buyer_summary: "Looked up buyer",
  invoice_details: "Looked up invoice",
  contract_search: "Read the contract",
  propose_reminder: "Drafted a reminder",
};

export function SparkAvatar({ busy = false }: { busy?: boolean }) {
  return (
    <div className={`spark ${busy ? "spark--busy" : ""}`}>
      <Spark size={15} />
    </div>
  );
}

export function Message({ message, tools }: { message: ChatMessage; tools?: string[] }) {
  const [copied, setCopied] = useState(false);

  if (message.role === "user") {
    return (
      <div className="row row--user">
        <div className="bubble">{message.content}</div>
      </div>
    );
  }

  const copy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const uniqueTools = [...new Set(tools ?? [])];

  return (
    <div className="row row--assistant">
      <SparkAvatar />
      <div className="answer">
        <div className="prose"><Markdown>{message.content}</Markdown></div>
        <div className="answer__meta">
          {uniqueTools.map((t) => (
            <span key={t} className="tool-chip">{TOOL_LABELS[t] ?? t}</span>
          ))}
          <button className="icon-btn icon-btn--quiet" onClick={copy} aria-label="Copy answer">
            {copied ? <Check size={15} /> : <Copy size={15} />}
          </button>
        </div>
      </div>
    </div>
  );
}

const THINKING = ["Checking the books", "Adding things up", "Reading the fine print", "Cross-checking numbers"];

export function Thinking() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % THINKING.length), 1800);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="row row--assistant">
      <SparkAvatar busy />
      <div className="answer"><span className="shimmer">{THINKING[i]}…</span></div>
    </div>
  );
}
