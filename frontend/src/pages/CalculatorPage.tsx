// The calculator on its own: pick any IPO with a price band.
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { IposResponse } from "../api";
import { useApi } from "../api";
import { Calculator } from "../Calculator";
import { STAGE_LABEL, shortDate } from "../format";
import { BoardPill, Card, ErrorBox, Loading, StagePill } from "../ui";

const ORDER = { open: 0, closed: 1, pre_apply: 2, listed: 3 } as const;

export default function CalculatorPage() {
  const { data, error, reload } = useApi<IposResponse>("/api/ipos", 120_000);
  const [params, setParams] = useSearchParams();
  const choices = useMemo(
    () =>
      (data?.ipos ?? [])
        .filter((i) => i.lot_size && i.cutoff && (i.stage !== "listed" || (i.listing_date && data && i.listing_date >= data.today.slice(0, 8) + "01")))
        .sort((a, b) => ORDER[a.stage] - ORDER[b.stage] || (a.bid_end ?? "").localeCompare(b.bid_end ?? "")),
    [data],
  );
  if (error) return <div className="page"><ErrorBox error={error} retry={reload} /></div>;
  if (!data) return <div className="page"><Loading height={300} /></div>;
  const picked = choices.find((i) => i.id === params.get("ipo")) ?? choices[0];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Allotment calculator</h1>
          <p>Retail is a lottery for the minimum bid; NII is proportionate. Every figure comes from live subscription and the rules file.</p>
        </div>
      </div>
      <Card>
        <div className="field" style={{ maxWidth: 520 }}>
          <label htmlFor="calc-ipo">IPO</label>
          <select id="calc-ipo" className="input" value={picked?.id ?? ""} onChange={(e) => setParams({ ipo: e.target.value })}>
            {choices.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name} · {i.board === "sme" ? "SME" : "Mainboard"} · {STAGE_LABEL[i.stage]} (closes {shortDate(i.bid_end)})
              </option>
            ))}
          </select>
        </div>
        {picked && (
          <div className="toolbar" style={{ marginTop: 12 }}>
            <StagePill stage={picked.stage} />
            <BoardPill board={picked.board} />
            <Link to={`/ipo/${picked.id}`} className="link-out small">Open the IPO page</Link>
          </div>
        )}
      </Card>
      {picked ? (
        <Card title={picked.name}>
          <Calculator ipo={picked} />
        </Card>
      ) : (
        <div className="card empty">No IPO with a price band yet.</div>
      )}
    </div>
  );
}
