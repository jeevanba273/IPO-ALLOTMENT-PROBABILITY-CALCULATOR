// Charts, all on Recharts with the shared category colours.
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import type { CalibrationRow, GmpPoint, IpoSummary, SubDay } from "./api";
import { BOOK_LABEL, dateOnly, inr, istDateTime, pct, shortDate, times } from "./format";
import { HEX } from "./ui";

const BOOKS = ["retail", "nii", "qib", "overall"] as const;
const AXIS = { stroke: HEX.axis, tickLine: false } as const;

interface TipRow {
  color?: string;
  text: string;
}

function Tip({ head, rows }: { head: string; rows: TipRow[] }) {
  return (
    <div className="tip">
      <div className="tip-head">{head}</div>
      {rows.map((row, i) => (
        <div className="tip-row" key={i}>
          {row.color && <span className="swatch" style={{ background: row.color }} />}
          {row.text}
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { color: string; label: string; hollow?: boolean }[] }) {
  return (
    <div className="legend">
      {items.map((item) => (
        <span key={item.label}>
          <span className="swatch" style={item.hollow ? { border: `1.5px solid ${item.color}`, background: "transparent", borderRadius: "50%" } : { background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

// ---- subscription by bidding day ------------------------------------------------------

export function SubscriptionChart({ history }: { history: SubDay[] }) {
  const values = history.flatMap((d) => BOOKS.map((b) => d[b]).filter((v): v is number => v !== undefined && v > 0));
  const spread = values.length ? Math.max(...values) / Math.max(0.01, Math.min(...values)) : 1;
  const [log, setLog] = useState(spread > 40);
  const data = history.map((d) => ({
    label: `Day ${d.day} · ${shortDate(d.bid_date)}`,
    ...Object.fromEntries(BOOKS.map((b) => [b, d[b] === undefined ? null : log ? Math.max(0.01, d[b] as number) : d[b]])),
    raw: d,
  }));
  if (!history.length) return <div className="empty">No subscription figures yet.</div>;
  return (
    <div className="stack">
      <div className="split">
        <Legend items={BOOKS.map((b) => ({ color: HEX[b], label: BOOK_LABEL[b] }))} />
        <div className="seg" role="group" aria-label="Scale">
          <button className={log ? "on" : ""} onClick={() => setLog(true)}>Log</button>
          <button className={!log ? "on" : ""} onClick={() => setLog(false)}>Linear</button>
        </div>
      </div>
      <div className="chart" style={{ height: 280 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={HEX.grid} vertical={false} />
            <XAxis dataKey="label" {...AXIS} />
            <YAxis
              {...AXIS}
              width={56}
              scale={log ? "log" : "linear"}
              domain={log ? [0.01, "auto"] : [0, "auto"]}
              allowDataOverflow
              tickFormatter={(v: number) => (v >= 10 ? `${Math.round(v)}×` : `${v}×`)}
            />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const day = (payload[0].payload as { raw: SubDay; label: string });
                return <Tip head={day.label} rows={BOOKS.map((b) => ({ color: HEX[b], text: `${BOOK_LABEL[b]}  ${times(day.raw[b])}` }))} />;
              }}
            />
            {BOOKS.map((b) => (
              <Line key={b} type="linear" dataKey={b} stroke={HEX[b]} strokeWidth={b === "overall" ? 2.5 : 2} dot={{ r: 3.5, fill: HEX[b], strokeWidth: 0 }} isAnimationActive={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ---- GMP over time -----------------------------------------------------------------------

export function GmpChart({ series, listingDate, cutoff }: { series: GmpPoint[]; listingDate: string | null; cutoff: number | null }) {
  const { line, manual } = useMemo(() => {
    const byTs = new Map<number, number>();
    const manualPoints: { t: number; gmp: number }[] = [];
    for (const point of series) {
      const t = new Date(point.ts).getTime();
      if (point.source === "manual") manualPoints.push({ t, gmp: point.gmp_rs });
      else if (!byTs.has(t) || point.interval === "hourly") byTs.set(t, point.gmp_rs);
    }
    const linePoints = [...byTs.entries()].sort((a, b) => a[0] - b[0]).map(([t, gmp]) => ({ t, gmp }));
    return { line: linePoints, manual: manualPoints };
  }, [series]);
  if (!line.length && !manual.length) return <div className="empty">No GMP yet. It is fetched automatically at 10:30 and 15:30 IST.</div>;
  const listingMs = listingDate ? dateOnly(listingDate).getTime() - 5.5 * 3600_000 : null;
  const fmtDay = (t: number) =>
    new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" }).format(new Date(t));
  return (
    <div className="chart" style={{ height: 260 }}>
      <ResponsiveContainer>
        <ComposedChart margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={HEX.grid} vertical={false} />
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={fmtDay} {...AXIS} allowDuplicatedCategory={false} />
          <YAxis {...AXIS} width={52} tickFormatter={(v: number) => `₹${v}`} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const point = payload[0].payload as { t: number; gmp: number };
              const implied = cutoff ? ` (${pct((point.gmp / cutoff) * 100)})` : "";
              return <Tip head={istDateTime(new Date(point.t).toISOString())} rows={[{ color: HEX.accent, text: `GMP ${inr(point.gmp)}${implied}` }]} />;
            }}
          />
          <ReferenceLine y={0} stroke={HEX.axis} />
          {listingMs && <ReferenceLine x={listingMs} stroke={HEX.muted} strokeDasharray="4 3" label={{ value: "Listing", fill: HEX.muted, fontSize: 11, position: "insideTopRight" }} />}
          <Line data={line} dataKey="gmp" type="linear" stroke={HEX.accent} strokeWidth={2} dot={false} isAnimationActive={false} />
          {manual.length > 0 && <Scatter data={manual} dataKey="gmp" fill={HEX.overall} shape="diamond" isAnimationActive={false} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---- listings: subscription against gain -----------------------------------------------------

interface GainPoint {
  x: number;
  y: number;
  name: string;
  board: string;
  flagged: boolean;
  id: string;
}

function hollowOr(color: string) {
  return (props: { cx?: number; cy?: number; payload?: GainPoint }) => {
    const { cx = 0, cy = 0, payload } = props;
    return payload?.flagged ? (
      <circle cx={cx} cy={cy} r={5} fill="none" stroke={color} strokeWidth={1.5} />
    ) : (
      <circle cx={cx} cy={cy} r={5} fill={color} fillOpacity={0.85} />
    );
  };
}

export function GainScatter({ ipos, onPick }: { ipos: IpoSummary[]; onPick?: (id: string) => void }) {
  const points: GainPoint[] = ipos
    .filter((i) => i.listing?.gain_pct !== null && i.listing?.gain_pct !== undefined && (i.subscription?.overall ?? 0) > 0)
    .map((i) => ({ x: i.subscription!.overall!, y: i.listing!.gain_pct!, name: i.name, board: i.board, flagged: i.listing!.flagged, id: i.id }));
  if (!points.length) return <div className="empty">No listings with both a subscription figure and a listing gain yet.</div>;
  const series = (board: string) => points.filter((p) => p.board === board);
  return (
    <div className="stack">
      <Legend items={[{ color: HEX.mainboard, label: "Mainboard" }, { color: HEX.sme, label: "SME" }, { color: HEX.muted, label: "0% or −20%: maybe a default value", hollow: true }]} />
      <div className="chart" style={{ height: 320 }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={HEX.grid} />
            <XAxis type="number" dataKey="x" scale="log" domain={[0.5, "auto"]} allowDataOverflow {...AXIS} tickFormatter={(v: number) => `${v}×`} ticks={[1, 3, 10, 30, 100, 300, 1000]} name="Subscription" />
            <YAxis type="number" dataKey="y" {...AXIS} width={52} tickFormatter={(v: number) => `${v}%`} name="Listing gain" />
            <ZAxis range={[60, 60]} />
            <ReferenceLine y={0} stroke={HEX.muted} strokeDasharray="4 3" />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as GainPoint;
                return <Tip head={p.name} rows={[{ text: `Subscribed ${times(p.x)}` }, { text: `Listing gain ${pct(p.y, 2)}${p.flagged ? " (maybe a default)" : ""}` }]} />;
              }}
            />
            <Scatter data={series("mainboard")} shape={hollowOr(HEX.mainboard)} onClick={(p: { payload?: GainPoint }) => p.payload && onPick?.(p.payload.id)} isAnimationActive={false} />
            <Scatter data={series("sme")} shape={hollowOr(HEX.sme)} onClick={(p: { payload?: GainPoint }) => p.payload && onPick?.(p.payload.id)} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ---- calibration: implied against actual ----------------------------------------------------

export function CalibrationScatter({ rows }: { rows: CalibrationRow[] }) {
  const points = rows
    .filter((r) => r.implied_pct !== null)
    .map((r) => ({ x: r.implied_pct as number, y: r.actual_pct, name: r.name, board: r.board, flagged: r.flagged, id: r.id }));
  if (!points.length) return <div className="empty">No listed IPO has a GMP from before its listing yet. The 21:00 IST backfill adds them a few a day.</div>;
  const all = points.flatMap((p) => [p.x, p.y]);
  const lo = Math.floor(Math.min(0, ...all) / 10) * 10;
  const hi = Math.ceil(Math.max(10, ...all) / 10) * 10;
  return (
    <div className="stack">
      <Legend items={[{ color: HEX.mainboard, label: "Mainboard" }, { color: HEX.sme, label: "SME" }, { color: HEX.muted, label: "Flagged listing", hollow: true }]} />
      <div className="chart" style={{ height: 340 }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={HEX.grid} />
            <XAxis type="number" dataKey="x" domain={[lo, hi]} {...AXIS} tickFormatter={(v: number) => `${v}%`} name="GMP implied" />
            <YAxis type="number" dataKey="y" domain={[lo, hi]} {...AXIS} width={52} tickFormatter={(v: number) => `${v}%`} name="Actual" />
            <ZAxis range={[60, 60]} />
            <ReferenceLine segment={[{ x: lo, y: lo }, { x: hi, y: hi }]} stroke={HEX.muted} strokeDasharray="4 3" ifOverflow="hidden" />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as GainPoint;
                return <Tip head={p.name} rows={[{ text: `GMP implied ${pct(p.x, 2)}` }, { text: `Actual ${pct(p.y, 2)}` }, { text: `Off by ${pct(p.x - p.y, 1)} points` }]} />;
              }}
            />
            <Scatter data={points.filter((p) => p.board === "mainboard")} shape={hollowOr(HEX.mainboard)} isAnimationActive={false} />
            <Scatter data={points.filter((p) => p.board === "sme")} shape={hollowOr(HEX.sme)} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <p className="muted small mt-0">On the dashed line the GMP called the listing exactly; above it the listing beat the GMP.</p>
    </div>
  );
}

// ---- compare: overall subscription by day ------------------------------------------------------

const COMPARE_COLORS = [HEX.retail, HEX.nii, HEX.qib];

export function CompareChart({ items }: { items: { name: string; history: SubDay[] }[] }) {
  const maxDays = Math.max(0, ...items.map((i) => i.history.length));
  if (!maxDays) return <div className="empty">Pick IPOs with subscription figures to compare.</div>;
  const data = Array.from({ length: maxDays }, (_, n) => ({
    day: `Day ${n + 1}`,
    ...Object.fromEntries(items.map((item, i) => [`s${i}`, item.history[n]?.overall ?? null])),
  }));
  return (
    <div className="stack">
      <Legend items={items.map((item, i) => ({ color: COMPARE_COLORS[i], label: item.name }))} />
      <div className="chart" style={{ height: 260 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={HEX.grid} vertical={false} />
            <XAxis dataKey="day" {...AXIS} />
            <YAxis {...AXIS} width={56} tickFormatter={(v: number) => `${v}×`} />
            <Tooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                return <Tip head={String(label)} rows={payload.map((p, i) => ({ color: COMPARE_COLORS[i], text: `${items[i]?.name ?? ""}  ${times(p.value as number)}` }))} />;
              }}
            />
            {items.map((_, i) => (
              <Line key={i} dataKey={`s${i}`} stroke={COMPARE_COLORS[i]} strokeWidth={2} dot={{ r: 3.5, strokeWidth: 0, fill: COMPARE_COLORS[i] }} isAnimationActive={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
