// Small inline SVG icons: no icon library to install, and they inherit the text colour.
type P = { size?: number };
const base = (size = 18) => ({
  width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
  "aria-hidden": true,
});

export const ArrowUp = ({ size }: P) => (
  <svg {...base(size)}><path d="M12 19V5M5 12l7-7 7 7" /></svg>
);
export const Pen = ({ size }: P) => (
  <svg {...base(size)}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>
);
export const Inbox = ({ size }: P) => (
  <svg {...base(size)}><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6Z" /></svg>
);
export const Copy = ({ size }: P) => (
  <svg {...base(size)}><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
);
export const Check = ({ size }: P) => (
  <svg {...base(size)}><path d="M20 6 9 17l-5-5" /></svg>
);
export const Close = ({ size }: P) => (
  <svg {...base(size)}><path d="M18 6 6 18M6 6l12 12" /></svg>
);
export const Spark = ({ size = 16 }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
    <path fill="currentColor" d="M12 2c.6 4.9 3.1 7.4 8 8-4.9.6-7.4 3.1-8 8-.6-4.9-3.1-7.4-8-8 4.9-.6 7.4-3.1 8-8Z" />
  </svg>
);
export const Rupee = ({ size }: P) => (
  <svg {...base(size)}><path d="M6 3h12M6 8h12M6 13l8.5 8M6 13h3c6.7 0 6.7-10 0-10" /></svg>
);
export const FileText = ({ size }: P) => (
  <svg {...base(size)}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></svg>
);
export const Clock = ({ size }: P) => (
  <svg {...base(size)}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
);
export const Send = ({ size }: P) => (
  <svg {...base(size)}><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></svg>
);
