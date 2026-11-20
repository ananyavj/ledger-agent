import { useCallback, useEffect, useRef, useState } from "react";
import { api, AuthError } from "./api";
import { ApprovalCard } from "./components/ApprovalCard";
import { AuthScreen } from "./components/AuthScreen";
import { Composer } from "./components/Composer";
import { DataPanel } from "./components/DataPanel";
import { Inbox, Pen } from "./components/Icons";
import { Message, Thinking } from "./components/Message";
import { RemindersPanel } from "./components/RemindersPanel";
import { Welcome } from "./components/Welcome";
import { WorkspaceSwitcher } from "./components/WorkspaceSwitcher";
import type { ChatMessage, Decision, PendingApproval, ThreadState, User, Workspace } from "./types";

// Remember each user's open conversation in each workspace across page refreshes
const threadKey = (user: User, ws: Workspace) => `lwl.thread.${user.id}.${ws.id}`;

export default function App() {
  // undefined = still checking the session; null = signed out
  const [user, setUser] = useState<User | null | undefined>(undefined);
  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null));
  }, []);

  if (user === undefined) return <Boot />;
  if (user === null) return <AuthScreen onSignedIn={setUser} />;
  return <Shell key={user.id} user={user} onSignedOut={() => setUser(null)} />;
}

const Boot = () => <div className="boot"><span className="brand__mark" /></div>;

// Header, workspace, and side panels. The conversation itself lives in <Conversation>.
function Shell({ user, onSignedOut }: { user: User; onSignedOut: () => void }) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [buyerCount, setBuyerCount] = useState<number | null>(null);
  const [conversation, setConversation] = useState(0);   // bump to start a fresh conversation
  const [panel, setPanel] = useState<"reminders" | "data" | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const loadWorkspaces = useCallback(async () => {
    try {
      const list = await api.workspaces();
      setWorkspace(list.current);
      setWorkspaces(list.workspaces);
      setBuyerCount((await api.summary()).buyers);
    } catch (e) {
      if (e instanceof AuthError) onSignedOut();
    }
  }, [onSignedOut]);

  useEffect(() => { loadWorkspaces(); }, [loadWorkspaces]);

  async function signOut() {
    await api.logout().catch(() => undefined);
    onSignedOut();
  }

  function newConversation() {
    if (workspace) localStorage.removeItem(threadKey(user, workspace));
    setConversation((n) => n + 1);
  }

  if (!workspace) return <Boot />;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__left">
          <button className="icon-btn" onClick={newConversation} aria-label="New conversation" title="New conversation">
            <Pen />
          </button>
          <div className="brand">
            <span className="brand__mark" />
            <span className="brand__name">LedgerWithLeisure</span>
          </div>
          <WorkspaceSwitcher current={workspace} all={workspaces}
                             onChanged={() => { loadWorkspaces(); setConversation((n) => n + 1); }} />
        </div>
        <div className="topbar__right">
          <button className="pill-btn" onClick={() => setPanel("data")}>
            <span className="pill-btn__dot" aria-hidden="true" /> <span className="pill-btn__label">Your data</span>
          </button>
          <button className="pill-btn" onClick={() => setPanel("reminders")}>
            <Inbox size={16} /> <span className="pill-btn__label">Reminders</span>
          </button>
          <div className="account">
            <button className="avatar" onClick={() => setMenuOpen((o) => !o)}
                    aria-label={`Account: ${user.name}`} aria-expanded={menuOpen}>
              {user.name.trim().charAt(0).toUpperCase()}
            </button>
            {menuOpen && (
              <div className="account__menu" role="menu">
                <strong>{user.name}</strong>
                <span>{user.email}</span>
                <button className="btn btn--ghost account__signout" onClick={signOut}>Sign out</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <Conversation key={`${workspace.id}-${conversation}`} user={user} workspace={workspace}
                    empty={buyerCount === 0} onAddData={() => setPanel("data")} onSignedOut={onSignedOut} />

      <RemindersPanel key={`r-${workspace.id}`} open={panel === "reminders"} onClose={() => setPanel(null)} />
      <DataPanel open={panel === "data"} onClose={() => setPanel(null)} workspace={workspace}
                 onCreateWorkspace={() => { setPanel(null); document.querySelector<HTMLButtonElement>(".switcher__btn")?.click(); }}
                 onDataChanged={() => api.summary().then((s) => setBuyerCount(s.buyers)).catch(() => undefined)} />
    </div>
  );
}

function Conversation({ user, workspace, empty, onAddData, onSignedOut }: {
  user: User;
  workspace: Workspace;
  empty: boolean;
  onAddData: () => void;
  onSignedOut: () => void;
}) {
  const key = threadKey(user, workspace);
  const [threadId, setThreadId] = useState<string | null>(() => localStorage.getItem(key));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [tools, setTools] = useState<Record<number, string[]>>({});   // message index -> tools used
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // The server is the source of truth: after every action, reload the conversation from it.
  const applyThread = useCallback((t: ThreadState, toolsUsed: string[] = []) => {
    setMessages(t.messages);
    setPending(t.pending_approvals);
    if (toolsUsed.length) setTools((prev) => ({ ...prev, [t.messages.length - 1]: toolsUsed }));
  }, []);

  useEffect(() => {   // restore this workspace's saved conversation, if any
    if (!threadId) return;
    api.thread(threadId).then((t) => applyThread(t)).catch((e) => {
      if (e instanceof AuthError) onSignedOut();
      else { localStorage.removeItem(key); setThreadId(null); }   // e.g. from before accounts existed
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending, busy, error]);

  function handleError(e: unknown) {
    if (e instanceof AuthError) onSignedOut();   // session ended: back to sign-in
    else setError((e as Error).message);
  }

  async function send(text: string) {
    setError(null);
    setMessages((prev) => [...prev, { role: "user", content: text }]);   // show it immediately
    setBusy(true);
    try {
      const res = await api.chat(text, threadId);
      setThreadId(res.thread_id);
      localStorage.setItem(key, res.thread_id);
      applyThread(await api.thread(res.thread_id), res.tools_called);
    } catch (e) {
      handleError(e);
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
      handleError(e);
    } finally {
      setBusy(false);
    }
  }

  const fresh = messages.length === 0 && !busy;

  return (
    <>
      <main className={`thread ${fresh ? "thread--empty" : ""}`}>
        {fresh ? (
          <Welcome name={user.name.trim().split(/\s+/)[0]} workspace={workspace} empty={empty}
                   onAddData={onAddData} onPick={send} />
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
    </>
  );
}
