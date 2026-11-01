// One IPO: timeline, facts, subscription, projections, GMP, the calculator and manual fixes.
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import type { IpoDetail, Projection } from "../api";
import { api, useApi } from "../api";
import { Calculator } from "../Calculator";
import { GmpChart, SubscriptionChart } from "../charts";
import { BOOK_LABEL, ago, count, crore, inr, istDateTime, pct, shortDate, times, timesRange } from "../format";
import { BoardPill, Card, CategoryPill, ConfidencePill, ErrorBox, GmpTag, Icon, Loading, StagePill } from "../ui";

function Timeline({ ipo, today }: { ipo: IpoDetail; today: string }) {
  const steps = [
    { label: "Opens", date: ipo.bid_start },
    { label: "Closes", date: ipo.bid_end },
    { label: "Allotment", date: ipo.allotment_date },
    { label: "Listing", date: ipo.listing_date },
  ];
  if (ipo.pre_apply_start) steps.unshift({ label: "Pre-apply", date: ipo.pre_apply_start });
  const nowIndex = steps.reduce((acc, step, i) => (step.date && step.date <= today ? i : acc), -1);
  return (
    <div className="timeline" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>
      {steps.map((step, i) => (
        <div key={step.label} className={`tl-step ${i < nowIndex ? "done" : i === nowIndex ? "done now" : ""}`}>
          <span className="k">{step.label}</span>
          <span className="v">{shortDate(step.date)}</span>
        </div>
      ))}
    </div>
  );
}

function ProjectionTable({ projections }: { projections: Record<string, Projection> }) {
  const order = ["retail", "nii", "qib", "overall"].filter((k) => projections[k]);
  if (!order.length) return <div className="empty">Projections appear once bidding starts.</div>;
  const first = projections[order[0]];
  if (first.status === "final") return <div className="muted small">Bidding is over: these are the final figures.</div>;
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>Category</th>
            <th className="r">Day {first.day}</th>
            <th className="r">Median final</th>
            <th className="r">Middle half</th>
            <th className="r">Full range</th>
            <th className="r">Past IPOs</th>
          </tr>
        </thead>
        <tbody>
          {order.map((k) => {
            const p = projections[k];
            return (
              <tr key={k}>
                <td>{BOOK_LABEL[k]}</td>
                <td className="r num">{times(p.current)}</td>
                {p.status === "ok" ? (
                  <>
                    <td className="r num" style={{ fontWeight: 700 }}>{times(p.median)}</td>
                    <td className="r num">{timesRange(p.q1!, p.q3!)}</td>
                    <td className="r num muted">{timesRange(p.low!, p.high!)}</td>
                  </>
                ) : (
                  <td className="r muted" colSpan={3}>{p.message}</td>
                )}
                <td className="r num muted">{p.samples}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function useAction(onDone: () => void) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      setMessage(success);
      onDone();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return { busy, message, run };
}

function ManualGmp({ ipo, onDone }: { ipo: IpoDetail; onDone: () => void }) {
  const [value, setValue] = useState("");
  const { busy, message, run } = useAction(onDone);
  const hasManual = ipo.gmp_series.some((p) => p.source === "manual");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const gmp = Number(value);
    if (!Number.isFinite(gmp) || value.trim() === "") return;
    run(() => api(`/api/ipos/${ipo.id}/gmp`, { method: "POST", body: JSON.stringify({ gmp_rs: gmp }) }), "Saved. A newer automatic GMP will take over when it arrives.");
    setValue("");
  };
  return (
    <form className="inline-form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="manual-gmp">Add a GMP you saw (₹ per share, now)</label>
        <input id="manual-gmp" className="input" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. 42" style={{ width: 140 }} />
      </div>
      <button className="btn" disabled={busy || value.trim() === ""}>Save GMP</button>
      {hasManual && (
        <button type="button" className="btn ghost" disabled={busy} onClick={() => run(() => api(`/api/ipos/${ipo.id}/gmp`, { method: "DELETE" }), "Manual GMP entries removed.")}>
          Remove manual entries
        </button>
      )}
      {message && <span className="muted small">{message}</span>}
    </form>
  );
}

function SymbolFix({ ipo, onDone }: { ipo: IpoDetail; onDone: () => void }) {
  const [value, setValue] = useState("");
  const { busy, message, run } = useAction(onDone);
  const manual = ipo.symbols.find((s) => s.source === "manual");
  return (
    <form
      className="inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) run(() => api(`/api/ipos/${ipo.id}/symbol`, { method: "PUT", body: JSON.stringify({ symbol: value.trim() }) }), "Symbol saved; the next GMP poll uses it.");
        setValue("");
      }}
    >
      <div className="field">
        <label htmlFor="symbol-fix">Exchange symbol used for GMP</label>
        <input id="symbol-fix" className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder={ipo.symbol ?? "e.g. ACMETECH"} style={{ width: 160 }} />
      </div>
      <button className="btn" disabled={busy || !value.trim()}>Set symbol</button>
      {manual && (
        <button type="button" className="btn ghost" disabled={busy} onClick={() => run(() => api(`/api/ipos/${ipo.id}/symbol`, { method: "DELETE" }), "Back to the automatic symbol.")}>
          Use automatic symbol
        </button>
      )}
      {message && <span className="muted small">{message}</span>}
    </form>
  );
}

function ReservationEditor({ ipo, onDone }: { ipo: IpoDetail; onDone: () => void }) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const { busy, message, run } = useAction(onDone);
  return (
    <div className="stack">
      {ipo.reservation_warning && (
        <div className="notice">
          <Icon name="alert" />
          <span>{ipo.reservation_warning} Type the RHP figure below to replace it; the odds then use yours.</span>
        </div>
      )}
      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th>Category</th>
              <th className="r">IndianAPI</th>
              <th className="r">From the RHP</th>
              <th>Set RHP figure</th>
            </tr>
          </thead>
          <tbody>
            {ipo.reservation.map((row) => (
              <tr key={row.category}>
                <td>{BOOK_LABEL[row.category]}</td>
                <td className="r num">{count(row.indianapi)}</td>
                <td className="r num">{row.manual ? <span className="good">{count(row.manual)}</span> : <span className="muted">—</span>}</td>
                <td>
                  <form
                    className="inline-form"
                    style={{ flexWrap: "nowrap" }}
                    onSubmit={(e) => {
                      e.preventDefault();
                      const shares = Number((drafts[row.category] ?? "").replace(/,/g, ""));
                      if (!Number.isInteger(shares) || shares <= 0) return;
                      run(
                        () => api(`/api/ipos/${ipo.id}/reservation/${row.category}`, { method: "PUT", body: JSON.stringify({ shares, note: "RHP" }) }),
                        `${BOOK_LABEL[row.category]} reserved shares saved.`,
                      );
                      setDrafts((d) => ({ ...d, [row.category]: "" }));
                    }}
                  >
                    <input
                      id={`res-${row.category}`}
                      aria-label={`${BOOK_LABEL[row.category]} reserved shares from the RHP`}
                      className="input"
                      inputMode="numeric"
                      style={{ width: 130 }}
                      value={drafts[row.category] ?? ""}
                      onChange={(e) => setDrafts((d) => ({ ...d, [row.category]: e.target.value }))}
                      placeholder="shares"
                    />
                    <button className="btn small" disabled={busy}>Save</button>
                    {row.manual && (
                      <button type="button" className="btn small ghost" disabled={busy} onClick={() => run(() => api(`/api/ipos/${ipo.id}/reservation/${row.category}`, { method: "DELETE" }), "Back to IndianAPI's figure.")}>
                        Clear
                      </button>
                    )}
                  </form>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {message && <span className="muted small">{message}</span>}
    </div>
  );
}

export default function IpoPage() {
  const { id = "" } = useParams();
  const { data: ipo, error, reload } = useApi<IpoDetail>(`/api/ipos/${encodeURIComponent(id)}`, 120_000);
  const { data: list } = useApi<{ today: string }>("/api/ipos", 0);
  if (error) return <div className="page"><ErrorBox error={error} retry={reload} /></div>;
  if (!ipo) return <div className="page"><Loading height={120} /><Loading height={300} /></div>;
  const today = list?.today ?? new Date().toISOString().slice(0, 10);
  const latest = ipo.subscription_history[ipo.subscription_history.length - 1];
  const figures = ipo.analysis?.latest?.figures ?? {};

  return (
    <div className="page">
      <div>
        <Link to="/" className="muted small" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <Icon name="back" size={14} /> Market board
        </Link>
      </div>
      <div className="page-head">
        <div>
          <h1>{ipo.name}</h1>
          <div className="toolbar" style={{ marginTop: 8 }}>
            <StagePill stage={ipo.stage} />
            <BoardPill board={ipo.board} />
            <CategoryPill category={ipo.min_category} />
            {ipo.industry && <span className="pill">{ipo.industry}</span>}
            {ipo.symbol ? <span className="pill num">{ipo.symbol}</span> : <span className="pill">No GMP mapping</span>}
          </div>
        </div>
        <div className="toolbar">
          {ipo.allotment_link && (
            <a className="btn" href={ipo.allotment_link} target="_blank" rel="noreferrer">
              Check allotment <Icon name="external" size={14} />
            </a>
          )}
          <Link className="btn" to={`/compare?ids=${ipo.id}`}>Compare</Link>
        </div>
      </div>

      <Card>
        <Timeline ipo={ipo} today={today} />
      </Card>

      <div className="stats">
        <div className="stat">
          <span className="stat-label">Price band</span>
          <span className="stat-value">{ipo.price_min && ipo.price_max && ipo.price_min !== ipo.price_max ? `${inr(ipo.price_min)}–${ipo.price_max}` : inr(ipo.price_max)}</span>
          <span className="stat-sub">Cut-off {inr(ipo.cutoff)} · face value {inr(ipo.face_value)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Minimum bid</span>
          <span className="stat-value">{inr(ipo.min_investment)}</span>
          <span className="stat-sub">
            {count(ipo.min_qty)} shares = {ipo.min_lots ?? "—"} lot{ipo.min_lots === 1 ? "" : "s"} of {count(ipo.lot_size)}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Retail limit</span>
          <span className="stat-value">{ipo.max_retail_lots ? `${ipo.max_retail_lots} lots` : "None"}</span>
          <span className="stat-sub">{ipo.max_retail_lots ? "Most lots that still count as retail" : "Even one bid is above ₹2 lakh"}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Issue size</span>
          <span className="stat-value">{crore(ipo.issue_size_cr)}</span>
          <span className="stat-sub">{ipo.registrar_name ? `Registrar: ${ipo.registrar_name}` : "Registrar not known"}</span>
        </div>
        <div className="stat">
          <span className="stat-label">{ipo.listing ? "Listing gain" : "Latest GMP"}</span>
          {ipo.listing ? (
            <>
              <span className={`stat-value ${(ipo.listing.gain_pct ?? 0) >= 0 ? "good" : "bad"}`}>{pct(ipo.listing.gain_pct, 2)}</span>
              <span className="stat-sub">Listed at {inr(ipo.listing.price, true)}{ipo.listing.flagged ? " · maybe a default value" : ""}</span>
            </>
          ) : ipo.gmp ? (
            <>
              <span className="stat-value">{inr(ipo.gmp.gmp_rs)}</span>
              <span className="stat-sub">{ipo.gmp.gmp_pct !== null ? `${pct(ipo.gmp.gmp_pct * 100)} implied · ` : ""}{ago(ipo.gmp.ts)}</span>
            </>
          ) : (
            <>
              <span className="stat-value muted">—</span>
              <span className="stat-sub">No GMP yet</span>
            </>
          )}
        </div>
      </div>

      <Card title="Allotment calculator" note="If I apply N lots from M PANs: odds now, odds at the projected final, and the expected value at the GMP">
        <Calculator ipo={ipo} />
      </Card>

      <div className="grid grid-2">
        <Card title="Subscription by day" note="Times subscribed, cumulative at the end of each bidding day">
          <SubscriptionChart history={ipo.subscription_history} />
        </Card>
        <Card
          title="Subscription now"
          note={latest ? `Day ${latest.day}, ${shortDate(latest.bid_date)}${ipo.detail_fetched_at ? ` · updated ${ago(ipo.detail_fetched_at)}` : ""}` : "Not started"}
        >
          {Object.keys(figures).length ? (
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th>Category</th>
                    <th className="r">Times</th>
                    <th className="r">Bid for</th>
                    <th className="r">Reserved</th>
                  </tr>
                </thead>
                <tbody>
                  {["retail", "nii", "qib", "overall"].filter((k) => figures[k]).map((k) => (
                    <tr key={k}>
                      <td>{BOOK_LABEL[k]}</td>
                      <td className="r num" style={{ fontWeight: 700 }}>{times(figures[k].times)}</td>
                      <td className="r num">{count(figures[k].applied)}</td>
                      <td className="r num">
                        {count(figures[k].reserved)} {figures[k].source === "manual" && <span className="pill ok">RHP</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">No subscription figures yet.</div>
          )}
          <div className="hr" style={{ margin: "14px 0" }} />
          <h3 style={{ fontSize: 13, marginBottom: 8 }}>Projected final subscription</h3>
          {ipo.analysis ? <ProjectionTable projections={ipo.analysis.projections} /> : <div className="empty">Appears once bidding starts.</div>}
        </Card>
      </div>

      <Card
        title="Grey market premium"
        note="Fetched automatically at 10:30 and 15:30 IST; points from the listing day on are dropped"
        action={ipo.gmp ? <GmpTag gmp={ipo.gmp} /> : undefined}
      >
        <GmpChart series={ipo.gmp_series} listingDate={ipo.listing_date} cutoff={ipo.cutoff} />
        {ipo.gmp && (
          <p className="muted small">
            Latest {inr(ipo.gmp.gmp_rs)} from {ipo.gmp.source} at {istDateTime(ipo.gmp.ts)} · {ipo.gmp.recent_points} points in the last 48 h ·{" "}
            <ConfidencePill confidence={ipo.gmp.confidence} />
          </p>
        )}
        <div className="hr" style={{ margin: "14px 0" }} />
        <ManualGmp ipo={ipo} onDone={reload} />
      </Card>

      <div className="grid grid-2">
        <Card title="Reserved shares" note="Used to work out times subscribed and the odds">
          <ReservationEditor ipo={ipo} onDone={reload} />
        </Card>
        <Card title="Symbol and links" note="The symbol links this IPO to its GMP">
          <div className="stack">
            <div className="kv">
              <div>
                <span className="k">Symbol in use</span>
                <span className="v num">{ipo.symbol ?? "None: no GMP mapping"}</span>
              </div>
              <div>
                <span className="k">Came from</span>
                <span className="v">{ipo.symbol_source ?? "—"}</span>
              </div>
              <div>
                <span className="k">Registrar</span>
                <span className="v">{ipo.registrar_name ?? "—"}</span>
              </div>
              <div>
                <span className="k">Allotment status</span>
                <span className="v">
                  {ipo.allotment_link ? (
                    <a className="link-out" href={ipo.allotment_link} target="_blank" rel="noreferrer">Registrar page</a>
                  ) : (
                    "—"
                  )}
                </span>
              </div>
            </div>
            <SymbolFix ipo={ipo} onDone={reload} />
          </div>
        </Card>
      </div>
    </div>
  );
}
