// Listed IPOs: how subscription related to the listing gain, by board.
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { IpoSummary, IposResponse } from "../api";
import { useApi } from "../api";
import { GainScatter } from "../charts";
import { daysBetween, inr, pct, shortDate, times } from "../format";
import { BoardPill, Card, ErrorBox, Loading, Stat } from "../ui";

type BoardFilter = "all" | "mainboard" | "sme";
type SortKey = "listing_date" | "name" | "subs" | "gain";

function median(values: number[]): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function boardStats(rows: IpoSummary[]) {
  const gains = rows.filter((r) => !r.listing!.flagged).map((r) => r.listing!.gain_pct as number);
  return { n: gains.length, median: median(gains), positive: gains.filter((g) => g > 0).length };
}

export default function Listings() {
  const { data, error, reload } = useApi<IposResponse>("/api/ipos", 300_000);
  const navigate = useNavigate();
  const [board, setBoard] = useState<BoardFilter>("all");
  const [days, setDays] = useState(90);
  const [hideFlagged, setHideFlagged] = useState(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "listing_date", desc: true });

  const rows = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.ipos.filter(
      (i) =>
        i.stage === "listed" &&
        i.listing?.gain_pct !== null &&
        i.listing?.gain_pct !== undefined &&
        (board === "all" || i.board === board) &&
        (!hideFlagged || !i.listing.flagged) &&
        (!days || (i.listing_date !== null && daysBetween(i.listing_date, data.today) <= days)) &&
        (!q || i.name.toLowerCase().includes(q) || (i.symbol ?? "").toLowerCase().includes(q)),
    );
  }, [data, board, days, hideFlagged, query]);

  const sorted = useMemo(() => {
    const value = (i: IpoSummary): string | number => {
      switch (sort.key) {
        case "name": return i.name.toLowerCase();
        case "subs": return i.subscription?.overall ?? -1;
        case "gain": return i.listing?.gain_pct ?? -999;
        default: return i.listing_date ?? "";
      }
    };
    return [...rows].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      const c = x < y ? -1 : x > y ? 1 : 0;
      return sort.desc ? -c : c;
    });
  }, [rows, sort]);

  if (error) return <div className="page"><ErrorBox error={error} retry={reload} /></div>;
  if (!data) return <div className="page"><Loading height={420} /></div>;

  const main = boardStats(rows.filter((r) => r.board === "mainboard"));
  const sme = boardStats(rows.filter((r) => r.board === "sme"));
  const header = (key: SortKey, label: string, right = false) => (
    <th className={right ? "r" : ""} aria-sort={sort.key === key ? (sort.desc ? "descending" : "ascending") : "none"}>
      <button onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "name" }))}>
        {label}{sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Listings</h1>
          <p>Listing-day gain over the issue price. Gains of exactly 0% or −20% are flagged as possible default values and left out of the medians.</p>
        </div>
      </div>

      <div className="toolbar">
        <div className="seg" role="group" aria-label="Board">
          {(["all", "mainboard", "sme"] as BoardFilter[]).map((b) => (
            <button key={b} className={board === b ? "on" : ""} onClick={() => setBoard(b)}>{b === "all" ? "All" : b === "sme" ? "SME" : "Mainboard"}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Period">
          {[30, 90, 0].map((d) => (
            <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d ? `${d} days` : "All"}</button>
          ))}
        </div>
        <button className={`chip ${hideFlagged ? "on" : ""}`} onClick={() => setHideFlagged((v) => !v)}>Hide flagged</button>
        <input id="listing-search" className="input" placeholder="Search name or symbol" value={query} onChange={(e) => setQuery(e.target.value)} style={{ width: 220 }} aria-label="Search listings" />
      </div>

      <div className="stats">
        <Stat label="Listings shown" value={rows.length} sub={`${rows.filter((r) => r.listing!.flagged).length} flagged`} />
        <Stat label="Mainboard median gain" value={main.median === null ? "—" : pct(main.median)} tone={main.median !== null && main.median < 0 ? "bad" : "good"} sub={`${main.positive} of ${main.n} above issue`} />
        <Stat label="SME median gain" value={sme.median === null ? "—" : pct(sme.median)} tone={sme.median !== null && sme.median < 0 ? "bad" : "good"} sub={`${sme.positive} of ${sme.n} above issue`} />
      </div>

      <Card title="Subscription against listing gain" note="Final overall subscription (log scale) and listing-day gain. Click a dot to open the IPO.">
        <GainScatter ipos={rows} onPick={(id) => navigate(`/ipo/${id}`)} />
      </Card>

      <Card title="All listings" note={`${sorted.length} IPOs`}>
        <div className="table-wrap tall">
          <table className="t">
            <thead>
              <tr>
                {header("name", "IPO")}
                <th>Board</th>
                {header("listing_date", "Listed")}
                {header("subs", "Subscribed", true)}
                <th className="r">Issue</th>
                <th className="r">Listing</th>
                {header("gain", "Gain", true)}
              </tr>
            </thead>
            <tbody>
              {sorted.map((i) => (
                <tr key={i.id}>
                  <td className="name"><Link to={`/ipo/${i.id}`}>{i.name}</Link></td>
                  <td><BoardPill board={i.board} /></td>
                  <td className="num">{shortDate(i.listing_date)}</td>
                  <td className="r num">{times(i.subscription?.overall)}</td>
                  <td className="r num">{inr(i.cutoff)}</td>
                  <td className="r num">{inr(i.listing?.price, true)}</td>
                  <td className={`r num ${(i.listing?.gain_pct ?? 0) >= 0 ? "good" : "bad"}`}>
                    {pct(i.listing?.gain_pct, 2)} {i.listing?.flagged && <span className="pill low">Check</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
