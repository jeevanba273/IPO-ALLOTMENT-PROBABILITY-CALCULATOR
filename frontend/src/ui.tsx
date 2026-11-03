// Small shared pieces: pills, stat tiles, subscription bars, GMP tag, states.
import type { ReactNode } from "react";
import type { Board, Confidence, Gmp, InvestorCategory, Stage, SubDay } from "./api";
import { CATEGORY_LABEL, STAGE_LABEL, inr, pct, times } from "./format";

export const COLORS = {
  retail: "var(--c-retail)",
  nii: "var(--c-nii)",
  qib: "var(--c-qib)",
  overall: "var(--c-overall)",
} as const;

// Recharts needs real colour values for SVG attributes it computes with.
export const HEX = {
  retail: "#f0a63a",
  nii: "#5aa9f0",
  qib: "#a68bf5",
  overall: "#e8ebf2",
  good: "#39cc8c",
  bad: "#f2656b",
  warn: "#f0c23a",
  muted: "#7e889c",
  grid: "#1b2436",
  axis: "#243049",
  accent: "#f0a63a",
  sme: "#a68bf5",
  mainboard: "#5aa9f0",
} as const;

export function StagePill({ stage }: { stage: Stage }) {
  return (
    <span className={`pill ${stage}`}>
      <span className="dot" />
      {STAGE_LABEL[stage]}
    </span>
  );
}

export function BoardPill({ board }: { board: Board }) {
  return <span className={`pill ${board}`}>{board === "sme" ? "SME" : "Mainboard"}</span>;
}

export function CategoryPill({ category }: { category: InvestorCategory | null }) {
  if (!category) return null;
  return <span className={`pill ${category}`}>{CATEGORY_LABEL[category]}</span>;
}

export function ConfidencePill({ confidence }: { confidence: Confidence }) {
  return (
    <span className={`pill ${confidence}`} title={confidence === "high" ? "Mainboard, 3+ GMP points in 48 h, latest under 24 h old" : "SME, few recent points, or a stale GMP"}>
      {confidence === "high" ? "High confidence" : "Low confidence"}
    </span>
  );
}

export function GmpTag({ gmp, compact = false }: { gmp: Gmp | null; compact?: boolean }) {
  if (!gmp) return <span className="muted small">No GMP yet</span>;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span className="num" style={{ fontWeight: 600 }}>
        GMP {inr(gmp.gmp_rs)}
      </span>
      {gmp.gmp_pct !== null && <span className={`num ${gmp.gmp_pct >= 0 ? "good" : "bad"}`}>{pct(gmp.gmp_pct * 100)}</span>}
      {!compact && <ConfidencePill confidence={gmp.confidence} />}
      {compact && gmp.confidence === "low" && <span className="pill low">Low conf.</span>}
    </span>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" | "warn" }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${tone ?? ""}`}>{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

export function Card({ title, note, action, children, id }: { title?: ReactNode; note?: ReactNode; action?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="card" id={id}>
      {(title || action) && (
        <div className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {note && <p>{note}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function SectionTitle({ title, count, right }: { title: string; count?: number; right?: ReactNode }) {
  return (
    <div className="split">
      <div className="section-title">
        <h2>{title}</h2>
        {count !== undefined && <span className="count">{count}</span>}
      </div>
      {right}
    </div>
  );
}

const BOOKS = ["retail", "nii", "qib", "overall"] as const;

/** Subscription bars on a log scale, so 0.5x and 500x both read; the tick marks 1x (fully subscribed). */
export function SubscriptionBars({ sub }: { sub: SubDay | null }) {
  if (!sub) return <div className="muted small">No subscription figures yet</div>;
  const max = Math.log10(1000);
  const width = (x: number | undefined) => {
    if (x === undefined || x <= 0) return 0;
    return Math.max(2, Math.min(100, ((Math.log10(x) + 1) / (max + 1)) * 100));
  };
  const oneX = (1 / (max + 1)) * 100;
  return (
    <div className="bars">
      {BOOKS.map((book) => (
        <div className="bar-row" key={book}>
          <span className="label">{book === "overall" ? "Overall" : book.toUpperCase() === "RETAIL" ? "Retail" : book.toUpperCase()}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ display: "block", width: `${width(sub[book])}%`, background: COLORS[book] }} />
            <span className="bar-mark" style={{ left: `${oneX}%` }} />
          </span>
          <span className="value">{times(sub[book])}</span>
        </div>
      ))}
    </div>
  );
}

export function Loading({ height = 160 }: { height?: number }) {
  return <div className="skeleton" style={{ minHeight: height }} aria-label="Loading" />;
}

export function ErrorBox({ error, retry }: { error: string; retry?: () => void }) {
  return (
    <div className="notice bad" role="alert">
      <Icon name="alert" />
      <div style={{ display: "grid", gap: 6 }}>
        <span>Couldn’t load this: {error}</span>
        {retry && (
          <button className="btn small" onClick={retry} style={{ width: "fit-content" }}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

const PATHS: Record<string, string> = {
  market: "M3 17l5-6 4 3 7-9M3 21h18",
  calendar: "M4 6h16v14H4zM4 10h16M9 3v4M15 3v4",
  calculator: "M6 3h12v18H6zM9 7h6M9 12h1M14 12h1M9 16h1M14 16h1",
  listings: "M4 6h16M4 12h16M4 18h10",
  calibration: "M4 20L20 4M4 14l4 4M10 8l4 4M14 4h6v6",
  compare: "M8 4v16M16 4v16M4 8h8M12 16h8",
  health: "M3 12h4l2-6 4 12 2-6h6",
  alert: "M12 8v5M12 16.5v.5M10.3 3.8L2.6 17a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 3.8a2 2 0 00-3.4 0z",
  info: "M12 11v6M12 7.5v.5M12 21a9 9 0 110-18 9 9 0 010 18z",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5",
  back: "M15 18l-6-6 6-6",
  signout: "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9",
};

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.info} />
    </svg>
  );
}
