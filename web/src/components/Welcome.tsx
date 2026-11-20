import { formatDate } from "../api";
import type { Workspace } from "../types";
import { Clock, FileText, Rupee, Send } from "./Icons";

const DEMO = [
  { icon: <Rupee />, text: "Who owes me the most money right now?" },
  { icon: <Clock />, text: "Which invoice has been overdue the longest?" },
  { icon: <FileText />, text: "Does Mahalaxmi Pickles get a discount for paying early?" },
  { icon: <Send />, text: "Draft a firm reminder for Iyer Spice Exports' oldest invoice." },
];
const GENERIC = [
  { icon: <Rupee />, text: "Who owes me the most money right now?" },
  { icon: <Clock />, text: "Which invoice has been overdue the longest?" },
  { icon: <FileText />, text: "What's overdue by more than 30 days?" },
  { icon: <Send />, text: "Draft a gentle reminder for my oldest overdue invoice." },
];

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

// Shown before the first message: a greeting, and either suggestions or a nudge to add data.
export function Welcome({ name, workspace, empty, onAddData, onPick }: {
  name: string;
  workspace: Workspace;
  empty: boolean;
  onAddData: () => void;
  onPick: (text: string) => void;
}) {
  return (
    <section className="welcome">
      <h1>{greeting()}, <span className="grad-text">{name}</span></h1>
      {empty ? (
        <>
          <p><strong>{workspace.name}</strong> has no data yet. Import your buyers, invoices and
            payments from CSV files, and upload contracts, to start asking questions.</p>
          <button className="btn btn--primary" onClick={onAddData}>Add your data</button>
        </>
      ) : (
        <>
          <p>
            {workspace.is_demo
              ? `You're in the demo: ${workspace.name}, with books as of ${formatDate(workspace.as_of)}.`
              : `${workspace.name}'s books, as of today.`} What would you like to know?
          </p>
          <div className="suggestions">
            {(workspace.is_demo ? DEMO : GENERIC).map((s) => (
              <button key={s.text} className="suggestion" onClick={() => onPick(s.text)}>
                <span className="suggestion__icon">{s.icon}</span>
                <span>{s.text}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
