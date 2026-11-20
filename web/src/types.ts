// Shapes of the data the API sends back. They mirror the Pydantic models in app/api.py,
// so a mismatch shows up as a TypeScript error instead of a blank screen.

export interface User {
  id: number;
  name: string;
  email: string;
}

export type Role = "user" | "assistant";

export interface ChatMessage {
  role: Role;
  content: string;
}

export type Tone = "gentle" | "firm" | "final";

export interface ReminderDraft {
  reminder_id: number;
  invoice_id: number;
  invoice_no: string;
  buyer: string;
  contact: string;
  email: string;
  balance: number;
  invoice_date?: string;
  due_date: string;
  days_overdue: number;
  tone: Tone;
  message: string;
}

export interface PendingApproval {
  interrupt_id: string;
  draft: ReminderDraft;
}

export interface AgentResponse {
  thread_id: string;
  reply: string | null;
  tools_called: string[];
  pending_approvals: PendingApproval[];
}

export interface ThreadState {
  thread_id: string;
  messages: ChatMessage[];
  pending_approvals: PendingApproval[];
}

export interface Decision {
  action: "approve" | "reject";
  edited_message?: string;
  reason?: string;
}

export type ReminderStatus = "pending" | "approved" | "rejected";

export interface ReminderRow {
  id: number;
  invoice_no: string;
  buyer: string;
  tone: Tone;
  status: ReminderStatus;
  draft_text: string;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;   // name of the person who approved or rejected it
}

export interface Workspace {
  id: number;
  name: string;
  is_demo: boolean;
  as_of: string;      // the "today" this workspace works with (YYYY-MM-DD)
  role: "owner" | "member";
}

export interface WorkspaceList {
  current: Workspace;
  workspaces: Workspace[];
}

export interface WorkspaceSummary {
  buyers: number;
  invoices: number;
  payments: number;
  contracts: number;
  outstanding: number;
  workspace: Workspace;
}

export type ImportKind = "buyers" | "invoices" | "payments";

export interface ImportReport {
  kind: ImportKind;
  ok: boolean;
  rows: number;
  error_count: number;
  errors: { row: number; message: string }[];
  warnings: { row: number; message: string }[];
  created: number;
  updated: number;
  skipped_duplicates: number;
}

export interface ContractRow {
  buyer: string;
  source_file: string;
  chunks: number;
}
