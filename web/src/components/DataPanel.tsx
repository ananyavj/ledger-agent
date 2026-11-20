import { useCallback, useEffect, useState } from "react";
import { api, formatRupees } from "../api";
import type { ContractRow, ImportKind, ImportReport, Workspace, WorkspaceSummary } from "../types";
import { Close } from "./Icons";

const STEPS: { kind: ImportKind; title: string; help: string }[] = [
  { kind: "buyers", title: "1. Buyers", help: "Name, plus contact name, phone and email if you have them." },
  { kind: "invoices", title: "2. Invoices", help: "Invoice number, buyer name, invoice date, due date, amount." },
  { kind: "payments", title: "3. Payments", help: "Invoice number, payment date, amount, and a reference if any." },
];

// Slide-over for bringing a business's own data in: CSV imports and contract uploads.
export function DataPanel({ open, onClose, workspace, onCreateWorkspace, onDataChanged }: {
  open: boolean;
  onClose: () => void;
  workspace: Workspace;
  onCreateWorkspace: () => void;
  onDataChanged: () => void;
}) {
  const [summary, setSummary] = useState<WorkspaceSummary | null>(null);
  const [contracts, setContracts] = useState<ContractRow[]>([]);
  const [reports, setReports] = useState<Partial<Record<ImportKind, ImportReport | string>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [buyer, setBuyer] = useState("");
  const [contractMsg, setContractMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const refresh = useCallback(() => {
    api.summary().then(setSummary).catch(() => undefined);
    api.contracts().then(setContracts).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (open) { refresh(); setReports({}); setContractMsg(null); }
  }, [open, workspace.id, refresh]);

  async function upload(kind: ImportKind, file: File) {
    setBusy(kind);
    try {
      const report = await api.importCsv(kind, file);
      setReports((r) => ({ ...r, [kind]: report }));
      if (report.ok) { refresh(); onDataChanged(); }
    } catch (e) {
      setReports((r) => ({ ...r, [kind]: (e as Error).message }));
    } finally {
      setBusy(null);
    }
  }

  async function uploadContract(file: File) {
    setBusy("contract");
    setContractMsg(null);
    try {
      const res = await api.uploadContract(buyer, file);
      setContractMsg({ ok: true, text: `Indexed ${res.file} for ${res.buyer} (${res.chunks} sections).` });
      refresh();
    } catch (e) {
      setContractMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className={`scrim ${open ? "scrim--open" : ""}`} onClick={onClose} />
      <aside className={`panel panel--wide ${open ? "panel--open" : ""}`} aria-hidden={!open}>
        <div className="panel__head">
          <h2>Your data</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Close /></button>
        </div>
        <div className="panel__body">
          {summary && (
            <div className="stats">
              <div><b>{summary.buyers}</b><span>buyers</span></div>
              <div><b>{summary.invoices}</b><span>invoices</span></div>
              <div><b>{summary.payments}</b><span>payments</span></div>
              <div><b>{summary.contracts}</b><span>contracts</span></div>
              <div className="stats__wide"><b>{formatRupees(summary.outstanding)}</b><span>outstanding</span></div>
            </div>
          )}

          {workspace.is_demo ? (
            <div className="notice">
              <p><strong>{workspace.name}</strong> is a read-only demo with sample data.
                To use your own buyers, invoices and contracts, create a workspace for your business.</p>
              <button className="btn btn--primary" onClick={onCreateWorkspace}>Create my workspace</button>
            </div>
          ) : (
            <>
              <p className="panel__intro">Upload CSV files in this order. Every row is checked first:
                if anything's wrong, nothing is imported and you'll see exactly which rows to fix.
                Uploading the same file again is safe.</p>
              {STEPS.map(({ kind, title, help }) => {
                const report = reports[kind];
                return (
                  <section key={kind} className="upload">
                    <div className="upload__head">
                      <h3>{title}</h3>
                      <a href={api.templateUrl(kind)} download>Template</a>
                    </div>
                    <p>{help}</p>
                    <label className={`file-btn ${busy === kind ? "file-btn--busy" : ""}`}>
                      {busy === kind ? "Checking…" : "Choose CSV"}
                      <input type="file" accept=".csv,text/csv" disabled={!!busy}
                             onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(kind, f); e.target.value = ""; }} />
                    </label>
                    {typeof report === "string" && <p className="result result--bad">{report}</p>}
                    {report && typeof report !== "string" && (report.ok ? (
                      <div className="result result--good">
                        Imported {report.rows} rows: {report.created} new, {report.updated} updated
                        {report.skipped_duplicates ? `, ${report.skipped_duplicates} already there` : ""}.
                        {report.warnings.length > 0 && (
                          <ul>{report.warnings.map((w, i) => <li key={i}>Row {w.row}: {w.message}</li>)}</ul>
                        )}
                      </div>
                    ) : (
                      <div className="result result--bad">
                        Nothing was imported. Fix {report.error_count} problem{report.error_count === 1 ? "" : "s"} and upload again:
                        <ul>{report.errors.map((e, i) => <li key={i}>Row {e.row}: {e.message}</li>)}</ul>
                      </div>
                    ))}
                  </section>
                );
              })}

              <section className="upload">
                <div className="upload__head"><h3>4. Contracts</h3></div>
                <p>A PDF or text contract for one buyer. Scanned PDFs without selectable text won't work.</p>
                <input className="upload__buyer" value={buyer} onChange={(e) => setBuyer(e.target.value)}
                       placeholder="Buyer name, exactly as imported" />
                <label className={`file-btn ${busy === "contract" || !buyer.trim() ? "file-btn--busy" : ""}`}>
                  {busy === "contract" ? "Reading and indexing…" : "Choose contract"}
                  <input type="file" accept=".pdf,.txt,.md" disabled={!!busy || !buyer.trim()}
                         onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadContract(f); e.target.value = ""; }} />
                </label>
                {contractMsg && <p className={`result ${contractMsg.ok ? "result--good" : "result--bad"}`}>{contractMsg.text}</p>}
              </section>
            </>
          )}

          {contracts.length > 0 && (
            <section className="upload">
              <h3>Contracts on file</h3>
              <ul className="contract-list">
                {contracts.map((c) => (
                  <li key={`${c.buyer}-${c.source_file}`}><span>{c.buyer}</span><small>{c.source_file}</small></li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </aside>
    </>
  );
}
