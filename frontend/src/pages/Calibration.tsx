// How well GMP called listing gains, and the history behind subscription projections.
import { Link } from "react-router-dom";
import type { Board, CalibrationResponse, MultiplierRow } from "../api";
import { useApi } from "../api";
import { CalibrationScatter } from "../charts";
import { BOOK_LABEL, inr, pct, shortDate, times } from "../format";
import { BoardPill, Card, ConfidencePill, ErrorBox, Loading, Stat } from "../ui";

function BoardStats({ board, data }: { board: Board; data: CalibrationResponse }) {
  const s = data.summary[board];
  const name = board === "sme" ? "SME" : "Mainboard";
  return (
    <>
      <Stat
        label={`${name}: typical miss`}
        value={s.median_abs_error_pp === null ? "—" : `${s.median_abs_error_pp.toFixed(1)} pts`}
        sub={s.with_gmp ? `Median gap between GMP-implied and actual gain, ${s.with_gmp} listings` : "No listing with a GMP yet"}
      />
      <Stat
        label={`${name}: GMP bias`}
        value={s.median_error_pp === null ? "—" : pct(s.median_error_pp)}
        tone={s.median_error_pp === null ? undefined : s.median_error_pp > 0 ? "warn" : "good"}
        sub={s.median_error_pp === null ? "—" : s.median_error_pp > 0 ? "GMP ran ahead of the listing" : "Listings beat the GMP"}
      />
      <Stat
        label={`${name}: within 5 points`}
        value={s.within_5pp === null ? "—" : `${Math.round(s.within_5pp * 100)}%`}
        sub={`${s.listings} listings in all`}
      />
    </>
  );
}

function MultiplierTable({ rows, minSamples }: { rows: MultiplierRow[]; minSamples: number }) {
  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>Board</th>
            <th>Category</th>
            <th className="r">From day</th>
            <th className="r">Past IPOs</th>
            <th className="r">Min</th>
            <th className="r">Median</th>
            <th className="r">Max</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.board}-${r.category}-${r.day}`}>
              <td><BoardPill board={r.board} /></td>
              <td>{BOOK_LABEL[r.category]}</td>
              <td className="r num">{r.day}</td>
              <td className="r num">{r.samples}</td>
              {r.samples >= minSamples ? (
                <>
                  <td className="r num">{times(r.low)}</td>
                  <td className="r num" style={{ fontWeight: 700 }}>{times(r.median)}</td>
                  <td className="r num">{times(r.high)}</td>
                </>
              ) : (
                <td className="r muted" colSpan={3}>not enough history</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Calibration() {
  const { data, error, reload } = useApi<CalibrationResponse>("/api/calibration", 300_000);
  if (error) return <div className="page"><ErrorBox error={error} retry={reload} /></div>;
  if (!data) return <div className="page"><Loading height={420} /></div>;
  const withGmp = data.rows.filter((r) => r.implied_pct !== null);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Calibration</h1>
          <p>The last GMP before each listing, as a % of the issue price, against the actual listing gain. Flagged listings are shown but left out of the figures.</p>
        </div>
      </div>

      <div className="stats">
        <BoardStats board="mainboard" data={data} />
        <BoardStats board="sme" data={data} />
      </div>

      <Card title="GMP-implied against actual" note={`${withGmp.length} of ${data.rows.length} listed IPOs have a GMP from before listing`}>
        <CalibrationScatter rows={data.rows} />
      </Card>

      <Card title="Listings" note="Newest first">
        <div className="table-wrap tall">
          <table className="t">
            <thead>
              <tr>
                <th>IPO</th>
                <th>Board</th>
                <th>Listed</th>
                <th className="r">Last GMP</th>
                <th className="r">Implied</th>
                <th className="r">Actual</th>
                <th className="r">Off by</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.id}>
                  <td className="name"><Link to={`/ipo/${r.id}`}>{r.name}</Link></td>
                  <td><BoardPill board={r.board} /></td>
                  <td className="num">{shortDate(r.listing_date)}</td>
                  <td className="r num">{r.gmp_rs === null ? <span className="muted">—</span> : inr(r.gmp_rs)}</td>
                  <td className="r num">{pct(r.implied_pct, 2)}</td>
                  <td className={`r num ${r.actual_pct >= 0 ? "good" : "bad"}`}>
                    {pct(r.actual_pct, 2)} {r.flagged && <span className="pill low">Check</span>}
                  </td>
                  <td className="r num">{r.error_pp === null ? "—" : `${r.error_pp > 0 ? "+" : ""}${r.error_pp.toFixed(1)}`}</td>
                  <td>{r.confidence ? <ConfidencePill confidence={r.confidence} /> : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title="Day-N to final subscription"
        note={`Final ÷ day-N times subscribed, from ${data.histories.mainboard} mainboard and ${data.histories.sme} SME listings with a full history. The calculator projects from these; it needs ${data.min_samples} IPOs per row.`}
      >
        <MultiplierTable rows={data.multipliers} minSamples={data.min_samples} />
      </Card>
    </div>
  );
}
