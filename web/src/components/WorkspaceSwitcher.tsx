import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { Workspace } from "../types";

// Header dropdown: shows the current workspace, switches between them, creates new ones.
export function WorkspaceSwitcher({ current, all, onChanged }: {
  current: Workspace;
  all: Workspace[];
  onChanged: (ws: Workspace) => void;
}) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function choose(ws: Workspace) {
    setOpen(false);
    if (ws.id === current.id) return;
    try {
      onChanged(await api.switchWorkspace(ws.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const ws = await api.createWorkspace(name.trim());
      setName("");
      setCreating(false);
      setOpen(false);
      onChanged(ws);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="switcher">
      <button className="switcher__btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="switcher__name">{current.name}</span>
        {current.is_demo && <span className="demo-tag">demo</span>}
        <span className="switcher__caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="switcher__menu" role="menu">
          <span className="switcher__label">Your workspaces</span>
          {all.map((ws) => (
            <button key={ws.id} className={`switcher__item ${ws.id === current.id ? "on" : ""}`}
                    onClick={() => choose(ws)}>
              <span>{ws.name}</span>
              {ws.is_demo ? <span className="demo-tag">demo</span> : <small>{ws.role}</small>}
            </button>
          ))}
          {creating ? (
            <form className="switcher__create" onSubmit={create}>
              <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
                     placeholder="Business name" maxLength={80} />
              <button className="btn btn--primary" disabled={!name.trim()}>Create</button>
            </form>
          ) : (
            <button className="switcher__new" onClick={() => setCreating(true)}>+ New workspace</button>
          )}
          {error && <p className="auth__error">{error}</p>}
        </div>
      )}
    </div>
  );
}
