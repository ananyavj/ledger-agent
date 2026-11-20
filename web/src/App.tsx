import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ApprovalCard } from "./components/ApprovalCard";
import { Composer } from "./components/Composer";
import { Inbox, Pen } from "./components/Icons";
import { Message, Thinking } from "./components/Message";
import { RemindersPanel } from "./components/RemindersPanel";
import { Welcome } from "./components/Welcome";
import type { ChatMessage, Decision, PendingApproval, ThreadState } from "./types";

const THREAD_KEY = "hisaab.thread";   // remember the conversation across page refreshes

export default function App() {
  const [threadId, setThreadId] = useState<string | null>(() => localStorage.getItem(THREAD_KEY));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [tools, setTools] = useState<Record<number, string[]>>({});   // message index -> tools used
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // The server is the source of truth: after every action, reload the conversation from it.
  const applyThread = useCallback((t: ThreadState, toolsUsed: string[] = []) => {
    setMessages(t.messages);
    setPending(t.pending_approvals);
    if (toolsUsed.length) setTools((prev) => ({ ...prev, [t.messages.length - 1]: toolsUsed }));
  }, []);

  useEffect(() => {   // on first load, restore the saved conversation (if any)
    if (!threadId) return;
    api.thread(threadId).then((t) => applyThread(t)).catch(() => startOver());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending, busy, error]);

  function startOver() {
    localStorage.removeItem(THREAD_KEY);
    setThreadId(null);
    setMessages([]);
    setPending([]);
    setTools({});
    setError(null);
  }

  async function send(text: string) {
    setError(null);
    setMessages((prev) => [...prev, { role: "user", content: text }]);   // show it immediately
    setBusy(true);
    try {
      const res = await api.chat(text, threadId);
      setThreadId(res.thread_id);
      localStorage.setItem(THREAD_KEY, res.thread_id);
      applyThread(await api.thread(res.thread_id), res.tools_called);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function decide(interruptId: string, decision: Decision) {
    if (!threadId) return;
    setError(null);
    setBusy(true);
    try {
      const res = await api.decide(threadId, interruptId, decision);
      applyThread(await api.thread(threadId), res.tools_called);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const empty = messages.length === 0 && !busy;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__left">
          <button className="icon-btn" onClick={startOver} aria-label="New conversation" title="New conversation">
            <Pen />
          </button>
          <div className="brand">
            <span className="brand__mark" />
            <span className="brand__name">Hisaab</span>
            <span className="brand__for">for Meera Packaging</span>
          </div>
        </div>
        <div className="topbar__right">
          <button className="pill-btn" onClick={() => setPanelOpen(true)}>
            <Inbox size={16} /> Reminders
          </button>
          <div className="avatar" aria-label="Meera">M</div>
        </div>
      </header>

      <main className={`thread ${empty ? "thread--empty" : ""}`}>
        {empty ? (
          <Welcome onPick={send} />
        ) : (
          <>
            {messages.map((m, i) => <Message key={i} message={m} tools={tools[i]} />)}
            {pending.map((p) => (
              <ApprovalCard key={p.interrupt_id} pending={p} onDecide={decide} busy={busy} />
            ))}
            {busy && <Thinking />}
            {error && <div className="error-note">{error}</div>}
          </>
        )}
        <div ref={endRef} />
      </main>

      <Composer onSend={send} disabled={busy}
                lockedReason={pending.length ? "Approve or reject the draft above to continue" : undefined} />
      <RemindersPanel open={panelOpen} onClose={() => setPanelOpen(false)} />
    </div>
  );
}
