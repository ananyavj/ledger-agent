import { Clock, FileText, Rupee, Send } from "./Icons";

const SUGGESTIONS = [
  { icon: <Rupee />, text: "Who owes me the most money right now?" },
  { icon: <Clock />, text: "Which invoice has been overdue the longest?" },
  { icon: <FileText />, text: "Does Mahalaxmi Pickles get a discount for paying early?" },
  { icon: <Send />, text: "Draft a firm reminder for Iyer Spice Exports' oldest invoice." },
];

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

// Shown before the first message: a greeting and a few things to try.
export function Welcome({ onPick }: { onPick: (text: string) => void }) {
  return (
    <section className="welcome">
      <h1>{greeting()}, <span className="grad-text">Meera</span></h1>
      <p>Your books are up to date as of 15 Nov 2026. What would you like to know?</p>
      <div className="suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s.text} className="suggestion" onClick={() => onPick(s.text)}>
            <span className="suggestion__icon">{s.icon}</span>
            <span>{s.text}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
