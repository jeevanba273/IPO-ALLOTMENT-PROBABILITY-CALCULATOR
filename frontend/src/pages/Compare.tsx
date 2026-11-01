// Two or three IPOs side by side.
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { IpoDetail, IposResponse } from "../api";
import { useApi } from "../api";
import { CompareChart } from "../charts";
import { CATEGORY_LABEL, STAGE_LABEL, count, crore, inr, pct, prob, shortDate, times } from "../format";
import { Card, ErrorBox, Loading } from "../ui";

const SLOTS = [0, 1, 2];

function useDetail(id: string | undefined) {
  return useApi<IpoDetail>(id ? `/api/ipos/${encodeURIComponent(id)}` : null);
}

export default function Compare() {
  const { data, error, reload } = useApi<IposResponse>("/api/ipos", 300_000);
  const [params, setParams] = useSearchParams();
  const ids = (params.get("ids") ?? "").split(",").filter(Boolean).slice(0, 3);
  const a = useDetail(ids[0]);
  const b = useDetail(ids[1]);
  const c = useDetail(ids[2]);
  const details = [a.data, b.data, c.data].filter((d): d is IpoDetail => !!d && ids.includes(d.id));

  if (error) return <div className="page"><ErrorBox error={error} retry={reload} /></div>;
  if (!data) return <div className="page"><Loading height={300} /></div>;

  const choices = data.ipos
    .filter((i) => i.bid_start && i.stage !== "pre_apply")
    .sort((x, y) => (y.bid_start ?? "").localeCompare(x.bid_start ?? ""))
    .slice(0, 120);
  const setSlot = (slot: number, id: string) => {
    const next = [...ids];
    next[slot] = id;
    setParams({ ids: next.filter(Boolean).join(",") });
  };

  const rows: { label: string; cell: (d: IpoDetail) => ReactNode }[] = [
    { label: "Board · stage", cell: (d) => `${d.board === "sme" ? "SME" : "Mainboard"} · ${STAGE_LABEL[d.stage]}` },
    { label: "Bidding", cell: (d) => `${shortDate(d.bid_start)} – ${shortDate(d.bid_end)}` },
    { label: "Listing", cell: (d) => shortDate(d.listing_date) },
    { label: "Price band", cell: (d) => `${inr(d.price_min)} – ${inr(d.price_max)}` },
    { label: "Issue size", cell: (d) => crore(d.issue_size_cr) },
    { label: "Minimum bid", cell: (d) => `${inr(d.min_investment)} (${count(d.min_qty)} shares)` },
    { label: "Minimum bid category", cell: (d) => (d.min_category ? CATEGORY_LABEL[d.min_category] : "—") },
    { label: "Retail subscription", cell: (d) => times(d.subscription?.retail) },
    { label: "NII subscription", cell: (d) => times(d.subscription?.nii) },
    { label: "QIB subscription", cell: (d) => times(d.subscription?.qib) },
    { label: "Overall subscription", cell: (d) => times(d.subscription?.overall) },
    { label: "Latest GMP", cell: (d) => (d.gmp ? `${inr(d.gmp.gmp_rs)} (${d.gmp.gmp_pct !== null ? pct(d.gmp.gmp_pct * 100) : "—"}, ${d.gmp.confidence})` : "—") },
    {
      label: "Odds at minimum bid",
      cell: (d) => {
        const odds = d.analysis?.odds_final ?? d.analysis?.odds_now;
        return odds ? `${prob(odds.p_low)} – ${prob(odds.p_high)}${odds.basis === "projected" ? " (projected)" : ""}` : "—";
      },
    },
    { label: "Listing gain", cell: (d) => (d.listing ? pct(d.listing.gain_pct, 2) : "—") },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Compare</h1>
          <p>Pick up to three IPOs to see them side by side.</p>
        </div>
      </div>
      <Card>
        <div className="grid grid-3">
          {SLOTS.map((slot) => (
            <div className="field" key={slot}>
              <label htmlFor={`compare-${slot}`}>IPO {slot + 1}</label>
              <select id={`compare-${slot}`} className="input" value={ids[slot] ?? ""} onChange={(e) => setSlot(slot, e.target.value)}>
                <option value="">None</option>
                {choices.map((i) => (
                  <option key={i.id} value={i.id}>{i.name} ({shortDate(i.bid_start)})</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      </Card>
      {details.length > 0 ? (
        <>
          <Card title="Side by side">
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th></th>
                    {details.map((d) => (
                      <th key={d.id}><Link to={`/ipo/${d.id}`} style={{ color: "var(--fg)" }}>{d.name}</Link></th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.label}>
                      <td className="muted">{row.label}</td>
                      {details.map((d) => (
                        <td key={d.id} className="num">{row.cell(d)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title="Overall subscription by bidding day">
            <CompareChart items={details.map((d) => ({ name: d.name, history: d.subscription_history }))} />
          </Card>
        </>
      ) : (
        <div className="card empty">Choose an IPO above to start.</div>
      )}
    </div>
  );
}
