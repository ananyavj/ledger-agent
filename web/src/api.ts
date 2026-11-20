// The only file that talks to the backend. Every request goes to /api/..., which the
// Vite dev server (or nginx in Docker) forwards to FastAPI.
import type {
  AgentResponse, ContractRow, Decision, ImportKind, ImportReport, ReminderRow, ReminderStatus,
  ThreadState, User, Workspace, WorkspaceList, WorkspaceSummary,
} from "./types";

const BASE = "/api";

// Thrown on 401, so the app knows to show the sign-in screen
export class AuthError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    // File uploads send FormData, and the browser must set that Content-Type itself
    const isForm = init?.body instanceof FormData;
    res = await fetch(`${BASE}${path}`, {
      headers: isForm ? undefined : { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new Error("Can't reach the server. Is the API running?");
  }
  if (!res.ok) {
    // FastAPI puts its error message in {"detail": ...}
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail
      : Array.isArray(body?.detail) ? "Please check the details you entered."   // 422 field errors
      : `Request failed (${res.status})`;
    throw res.status === 401 ? new AuthError(detail) : new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // The session cookie is httpOnly and set by the server; the browser sends it
  // automatically on these same-origin requests, so no token handling is needed here.
  me: () => request<User>("/auth/me"),
  login: (email: string, password: string) =>
    request<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  register: (name: string, email: string, password: string) =>
    request<User>("/auth/register", { method: "POST", body: JSON.stringify({ name, email, password }) }),
  logout: () => request<{ status: string }>("/auth/logout", { method: "POST" }),

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

  workspaces: () => request<WorkspaceList>("/workspaces"),
  createWorkspace: (name: string) =>
    request<Workspace>("/workspaces", { method: "POST", body: JSON.stringify({ name }) }),
  switchWorkspace: (id: number) => request<Workspace>(`/workspaces/${id}/switch`, { method: "POST" }),
  summary: () => request<WorkspaceSummary>("/workspace/summary"),

  importCsv: (kind: ImportKind, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ImportReport>(`/import/${kind}`, { method: "POST", body: form });
  },
  templateUrl: (kind: ImportKind) => `${BASE}/import/templates/${kind}`,
  contracts: () => request<ContractRow[]>("/contracts"),
  uploadContract: (buyerName: string, file: File) => {
    const form = new FormData();
    form.append("buyer_name", buyerName);
    form.append("file", file);
    return request<{ buyer: string; file: string; chunks: number }>("/contracts", { method: "POST", body: form });
  },
};

const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", maximumFractionDigits: 0,
});
export const formatRupees = (n: number) => rupees.format(n);

export const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
