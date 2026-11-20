import { useEffect, useState } from "react";
import { api, formatDate } from "../api";
import type { ReminderRow, ReminderStatus } from "../types";
import { Close } from "./Icons";

const TABS: ReminderStatus[] = ["approved", "pending", "rejected"];

// Slide-over listing every reminder by status, straight from the database.
export function RemindersPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [status, setStatus] = useState<ReminderStatus>("approved");
  const [rows, setRows] = useState<ReminderRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setRows(null);
    setError(null);
    api.reminders(status).then(setRows).catch((e: Error) => setError(e.message));
  }, [open, status]);

  return (
    <>
      <div className={`scrim ${open ? "scrim--open" : ""}`} onClick={onClose} />
      <aside className={`panel ${open ? "panel--open" : ""}`} aria-hidden={!open}>
        <div className="panel__head">
          <h2>Reminders</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Close /></button>
        </div>
        <div className="segmented">
          {TABS.map((t) => (
            <button key={t} className={t === status ? "on" : ""} onClick={() => setStatus(t)}>{t}</button>
          ))}
        </div>
        <div className="panel__body">
          {error && <p className="panel__empty">{error}</p>}
          {!error && rows === null && <p className="panel__empty">Loading…</p>}
          {rows?.length === 0 && <p className="panel__empty">No {status} reminders yet.</p>}
          {rows?.map((r) => (
            <details key={r.id} className="reminder">
              <summary>
                <span className="reminder__title">{r.buyer}</span>
                <span className={`tone tone--${r.tone}`}>{r.tone}</span>
                <span className="reminder__sub">{r.invoice_no} · {formatDate(r.created_at)}</span>
              </summary>
              <pre>{r.draft_text}</pre>
            </details>
          ))}
        </div>
      </aside>
    </>
  );
}
