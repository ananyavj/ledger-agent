import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "./Icons";

const QUICK = [
  { label: "Overdue", text: "What's overdue right now?" },
  { label: "Contracts", text: "What's the late fee in Sharma Traders' contract?" },
  { label: "Draft a reminder", text: "Draft a gentle reminder for Sharma Traders' oldest overdue invoice." },
];

// The floating input bar. Enter sends, Shift+Enter adds a new line.
export function Composer({ onSend, disabled, lockedReason }: {
  onSend: (text: string) => void;
  disabled: boolean;
  lockedReason?: string;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {   // grow with the text, up to a limit
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [text]);

  const send = () => {
    const value = text.trim();
    if (!value || disabled) return;
    onSend(value);
    setText("");
  };

  return (
    <div className="composer-wrap">
      <div className={`composer ${lockedReason ? "composer--locked" : ""}`}>
        <textarea
          ref={ref}
          rows={1}
          value={text}
          placeholder={lockedReason ?? "Ask about your receivables"}
          disabled={!!lockedReason}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
        />
        <div className="composer__row">
          <div className="chips">
            {QUICK.map((q) => (
              <button key={q.label} className="chip" disabled={disabled || !!lockedReason}
                      onClick={() => onSend(q.text)}>{q.label}</button>
            ))}
          </div>
          <button className="send" onClick={send} aria-label="Send"
                  disabled={disabled || !!lockedReason || !text.trim()}>
            <ArrowUp size={18} />
          </button>
        </div>
      </div>
      <p className="footnote">Answers come from your records. Reminders never go out without your approval.</p>
    </div>
  );
}
