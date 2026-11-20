// The only file that talks to the backend. Every request goes to /api/..., which the
// Vite dev server (or nginx in Docker) forwards to FastAPI.
import type {
  AgentResponse, Decision, ReminderRow, ReminderStatus, ThreadState,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new Error("Can't reach the server. Is the API running?");
  }
  if (!res.ok) {
    // FastAPI puts its error message in {"detail": ...}
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  chat: (message: string, threadId: string | null) =>
    request<AgentResponse>("/chat", {
      method: "POST",
      body: JSON.stringify({ message, thread_id: threadId }),
    }),

  decide: (threadId: string, interruptId: string, decision: Decision) =>
    request<AgentResponse>(`/threads/${threadId}/decisions`, {
      method: "POST",
      body: JSON.stringify({ decisions: { [interruptId]: decision } }),
    }),

  thread: (threadId: string) => request<ThreadState>(`/threads/${threadId}`),

  reminders: (status: ReminderStatus) => request<ReminderRow[]>(`/reminders?status=${status}`),
};

const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", maximumFractionDigits: 0,
});
export const formatRupees = (n: number) => rupees.format(n);

export const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
