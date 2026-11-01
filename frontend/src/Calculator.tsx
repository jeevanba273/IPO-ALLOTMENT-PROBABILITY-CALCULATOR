// The allotment calculator: "if I apply N lots from M PANs". Reads only; a GMP typed
// here is a what-if and is never stored.
import { useEffect, useState } from "react";
import type { Analysis, IpoSummary, OddsResult } from "./api";
import { useApi, useDebounced } from "./api";
import { BOOK_LABEL, CATEGORY_LABEL, count, inr, probRange, timesRange } from "./format";
import { CategoryPill, ConfidencePill, ErrorBox, Icon, Loading } from "./ui";

function Stepper({ value, min, max, onChange, label }: { value: number; min: number; max: number; onChange: (n: number) => void; label: string }) {
  const clamp = (n: number) => Math.min(max, Math.max(min, Number.isFinite(n) ? Math.round(n) : min));
  return (
    <div className="stepper" role="group" aria-label={label}>
      <button type="button" onClick={() => onChange(clamp(value - 1))} aria-label={`Fewer ${label}`}>−</button>
      <input
        id={`stepper-${label}`}
        inputMode="numeric"
        value={value}
        onChange={(e) => onChange(clamp(Number(e.target.value)))}
        aria-label={label}
      />
      <button type="button" onClick={() => onChange(clamp(value + 1))} aria-label={`More ${label}`}>+</button>
    </div>
  );
}

function Meter({ low, high }: { low: number; high: number }) {
  return (
    <div className="meter" aria-hidden="true">
      <span className="lo" style={{ left: 0, width: `${high * 100}%` }} />
      <span style={{ left: 0, width: `${low * 100}%` }} />
    </div>
  );
}

function OddsBlock({ odds, title, applications, lotSize }: { odds: OddsResult; title: string; applications: number; lotSize: number }) {
  const retail = odds.category === "retail";
  return (
    <div className="result">
      <div className="result-head">
        <span className="t">{title}</span>
        <span className="muted small num">at {timesRange(odds.times_low, odds.times_high)} subscribed</span>
      </div>
      <div className="split" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="muted small">{retail ? "Chance of an allotment, per application" : "Chance of at least 1 lot, per application"}</div>
          <div className="big-prob accent">{probRange(odds.p_low, odds.p_high)}</div>
        </div>
        {applications > 1 && (
          <div style={{ textAlign: "right" }}>
            <div className="muted small">Any of your {applications} applications</div>
            <div className="big-prob">{probRange(odds.any_low, odds.any_high)}</div>
          </div>
        )}
      </div>
      <Meter low={applications > 1 ? odds.any_low : odds.p_low} high={applications > 1 ? odds.any_high : odds.p_high} />
      <div className="muted small">
        Expected shares <span className="num fg2">{count(odds.shares_low)}{Math.abs(odds.shares_high - odds.shares_low) >= 0.5 ? ` – ${count(odds.shares_high)}` : ""}</span>
        {" "}({(odds.shares_low / lotSize).toFixed(2)}{Math.abs(odds.shares_high - odds.shares_low) >= 0.5 ? ` – ${(odds.shares_high / lotSize).toFixed(2)}` : ""} lots)
        {retail && odds.p_low < odds.p_high && " · the low end assumes every applicant bid the minimum"}
      </div>
    </div>
  );
}

export function Calculator({ ipo }: { ipo: IpoSummary }) {
  const minLots = ipo.min_lots ?? 1;
  const [lots, setLots] = useState(minLots);
  const [apps, setApps] = useState(1);
  const [gmp, setGmp] = useState("");
  useEffect(() => {
    setLots(minLots);
    setApps(1);
    setGmp("");
  }, [ipo.id, minLots]);

  const params = new URLSearchParams({ lots: String(lots), apps: String(apps) });
  const gmpValue = gmp.trim() === "" ? null : Number(gmp);
  if (gmpValue !== null && Number.isFinite(gmpValue)) params.set("gmp", String(gmpValue));
  const query = useDebounced(params.toString(), 200);
  const ready = ipo.lot_size && ipo.cutoff;
  const { data, error, loading, reload } = useApi<Analysis>(ready ? `/api/ipos/${encodeURIComponent(ipo.id)}/odds?${query}` : null);

  if (!ready) return <div className="empty">The price band and lot size aren’t announced yet, so there is nothing to calculate.</div>;

  const presets: { label: string; lots: number }[] = [{ label: `Minimum (${minLots})`, lots: minLots }];
  if (ipo.max_retail_lots && ipo.max_retail_lots > minLots) presets.push({ label: `Max retail (${ipo.max_retail_lots})`, lots: ipo.max_retail_lots });
  if (ipo.min_snii_lots && ipo.min_snii_lots > minLots) presets.push({ label: `Small NII (${ipo.min_snii_lots})`, lots: ipo.min_snii_lots });
  if (ipo.min_bnii_lots && ipo.min_bnii_lots > minLots) presets.push({ label: `Big NII (${ipo.min_bnii_lots})`, lots: ipo.min_bnii_lots });

  return (
    <div className="calc">
      <div className="calc-inputs">
        <div className="field">
          <label htmlFor="stepper-lots">Lots per application ({ipo.lot_size} shares each)</label>
          <Stepper value={lots} min={minLots} max={100000} onChange={setLots} label="lots" />
          <div className="chips">
            {presets.map((p) => (
              <button key={p.label} type="button" className={`chip ${lots === p.lots ? "on" : ""}`} onClick={() => setLots(p.lots)}>
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <div className="field">
          <label htmlFor="stepper-applications">Applications (one per PAN)</label>
          <Stepper value={apps} min={1} max={20} onChange={setApps} label="applications" />
        </div>
        <div className="field">
          <label htmlFor="calc-gmp">What-if GMP in ₹ (leave empty to use the latest)</label>
          <input
            id="calc-gmp"
            className="input"
            inputMode="decimal"
            placeholder={ipo.gmp ? `Latest: ${inr(ipo.gmp.gmp_rs)}` : "No GMP yet"}
            value={gmp}
            onChange={(e) => setGmp(e.target.value)}
          />
        </div>
        {data && (
          <div className="kv" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <div>
              <span className="k">You bid</span>
              <span className="v num">{inr(data.amount_rs)}</span>
            </div>
            <div>
              <span className="k">Category</span>
              <span className="v">
                <CategoryPill category={data.category} />
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="calc-results">
        {error && <ErrorBox error={error} retry={reload} />}
        {!data && !error && loading && <Loading height={220} />}
        {data && (
          <>
            {data.min_category !== "retail" && (
              <div className="notice">
                <Icon name="info" />
                <span>
                  <b>Your minimum bid is NII for this IPO.</b> Even the smallest application ({inr(data.min_investment)}) is above ₹2 lakh, so it is allotted as {CATEGORY_LABEL[data.min_category]}, not retail.
                </span>
              </div>
            )}
            {data.odds_now ? (
              <OddsBlock
                odds={data.odds_now}
                title={data.odds_now.basis === "final" ? "Final odds" : `Odds now (day ${data.latest?.day ?? "?"}${data.latest && !data.latest.complete ? ", still bidding" : ""})`}
                applications={data.applications}
                lotSize={data.lot_size}
              />
            ) : (
              <div className="result">
                <span className="muted">No subscription figures yet, so no odds. They appear once bidding opens.</span>
              </div>
            )}
            {data.odds_final && (
              <OddsBlock odds={data.odds_final} title="Odds at the projected final subscription" applications={data.applications} lotSize={data.lot_size} />
            )}
            {data.odds_now && !data.odds_final && data.odds_now.basis !== "final" && (
              <div className="muted small">
                Projected final: {data.projections[data.category === "retail" ? "retail" : "nii"]?.message ?? "not available"}.
              </div>
            )}
            <div className="result">
              <div className="result-head">
                <span className="t">Expected value</span>
                {data.ev && <ConfidencePill confidence={data.ev.confidence} />}
              </div>
              {data.ev ? (
                <>
                  <div className="big-prob">
                    {inr(data.ev.low)} – {inr(data.ev.high)}
                  </div>
                  <div className="muted small">
                    {data.ev.point !== null ? (
                      <>Most likely about <span className="num fg2">{inr(data.ev.point)}</span>. </>
                    ) : (
                      <>Low-confidence GMP, so no single figure. </>
                    )}
                    At GMP {inr(data.gmp_used)}{gmpValue !== null ? " (what-if)" : ""}, across all applications.
                  </div>
                  <div className="ev-bands">
                    {data.ev.bands.map((band) => (
                      <div className="ev-band" key={band.multiplier}>
                        <span className="muted num">{band.multiplier} × GMP</span>
                        <span className="num">
                          {inr(band.low)} – {inr(band.high)}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <span className="muted small">Needs a GMP. It arrives automatically from the 10:30 and 15:30 IST polls, or type a what-if above.</span>
              )}
            </div>
            {data.notes.filter((n) => !n.startsWith("your minimum bid")).map((note) => (
              <div className="muted small" key={note}>· {note}</div>
            ))}
            {data.latest && (
              <div className="muted small">
                Using {Object.entries(data.latest.figures)
                  .filter(([k]) => k !== "overall")
                  .map(([k, f]) => `${BOOK_LABEL[k]} ${f.times.toFixed(2)}×${f.source === "manual" ? " (RHP)" : ""}`)
                  .join(" · ")}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
