import { useState } from "react";
import { formatDate, formatRupees } from "../api";
import type { Decision, PendingApproval } from "../types";
import { SparkAvatar } from "./Message";

// The human-in-the-loop step: the agent is paused until Meera decides here.
export function ApprovalCard({ pending, onDecide, busy }: {
  pending: PendingApproval;
  onDecide: (interruptId: string, decision: Decision) => void;
  busy: boolean;
}) {
  const d = pending.draft;
  const [text, setText] = useState(d.message);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const edited = text.trim() !== d.message.trim();

  const approve = () =>
    onDecide(pending.interrupt_id, edited ? { action: "approve", edited_message: text.trim() } : { action: "approve" });
  const reject = () =>
    onDecide(pending.interrupt_id, { action: "reject", ...(reason.trim() ? { reason: reason.trim() } : {}) });

  return (
    <div className="row row--assistant">
      <SparkAvatar />
      <div className="approval">
        <div className="approval__head">
          <span>A reminder needs your approval</span>
          <span className={`tone tone--${d.tone}`}>{d.tone}</span>
        </div>

        <dl className="approval__facts">
          <div><dt>To</dt><dd>{d.contact}<small>{d.buyer} · {d.email}</small></dd></div>
          <div><dt>Invoice</dt><dd>{d.invoice_no}<small>
            {d.invoice_date ? `issued ${formatDate(d.invoice_date)} · ` : ""}due {formatDate(d.due_date)}</small></dd></div>
          <div><dt>Balance</dt><dd className="money">{formatRupees(d.balance)}</dd></div>
          <div><dt>Overdue</dt><dd className="money">{d.days_overdue}<small>days</small></dd></div>
        </dl>

        <label className="approval__label" htmlFor={`msg-${pending.interrupt_id}`}>
          Message {edited && <em>· edited</em>}
        </label>
        <textarea id={`msg-${pending.interrupt_id}`} className="approval__text" value={text}
                  onChange={(e) => setText(e.target.value)} rows={9} />

        {rejecting && (
          <input className="approval__reason" placeholder="What should change? (optional)"
                 value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        )}

        <div className="approval__actions">
          <span className="approval__note">Checked against your records. Nothing is sent from this demo.</span>
          {rejecting ? (
            <>
              <button className="btn btn--ghost" onClick={() => setRejecting(false)} disabled={busy}>Back</button>
              <button className="btn btn--clay" onClick={reject} disabled={busy}>Reject draft</button>
            </>
          ) : (
            <>
              <button className="btn btn--ghost" onClick={() => setRejecting(true)} disabled={busy}>Reject</button>
              <button className="btn btn--primary" onClick={approve} disabled={busy}>
                {edited ? "Approve edited version" : "Approve"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
