// The market board: what is open, what closes or lists soon, how recent listings did.
import { Link, useNavigate } from "react-router-dom";
import type { IpoSummary, IposResponse } from "../api";
import { useApi } from "../api";
import { addDays, count, crore, daysBetween, inr, longDate, pct, shortDate, times, weekdayDate } from "../format";
import { BoardPill, CategoryPill, ErrorBox, GmpTag, Loading, SectionTitle, Stat, SubscriptionBars } from "../ui";

function median(values: number[]): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

function closingText(ipo: IpoSummary, today: string) {
  if (!ipo.bid_end) return "";
  const days = daysBetween(today, ipo.bid_end);
  if (days <= 0) return "Closes today";
  if (days === 1) return "Closes tomorrow";
  return `Closes in ${days} days`;
}

export function IpoCard({ ipo, today }: { ipo: IpoSummary; today: string }) {
  const navigate = useNavigate();
  const priceBand = ipo.price_min && ipo.price_max && ipo.price_min !== ipo.price_max ? `${inr(ipo.price_min)}–${ipo.price_max}` : inr(ipo.price_max);
  return (
    <article
      className="ipo-card"
      tabIndex={0}
      role="link"
      onClick={() => navigate(`/ipo/${ipo.id}`)}
      onKeyDown={(e) => e.key === "Enter" && navigate(`/ipo/${ipo.id}`)}
      style={{ cursor: "pointer" }}
    >
      <div className="ipo-card-top">
        <div style={{ minWidth: 0 }}>
          <h3>{ipo.name}</h3>
          <div className="pills">
            <BoardPill board={ipo.board} />
            <CategoryPill category={ipo.min_category} />
            {!ipo.symbol && <span className="pill">No GMP mapping</span>}
          </div>
        </div>
        <div className="closing">
          <div className={`small ${ipo.bid_end === today ? "warn" : "muted"}`}>{closingText(ipo, today)}</div>
          <div className="num">{shortDate(ipo.bid_end)}</div>
        </div>
      </div>
      <div className="facts">
        <div>
          <span className="k">Price band</span>
          <span className="v">{priceBand}</span>
        </div>
        <div>
          <span className="k">Minimum bid</span>
          <span className="v">{inr(ipo.min_investment)}</span>
        </div>
        <div>
          <span className="k">Issue size</span>
          <span className="v">{crore(ipo.issue_size_cr)}</span>
        </div>
      </div>
      <div>
        <div className="muted small" style={{ marginBottom: 6 }}>
          Subscription{ipo.subscription ? `, day ${ipo.subscription.day} (${shortDate(ipo.subscription.bid_date)})` : ""}
        </div>
        <SubscriptionBars sub={ipo.subscription} />
      </div>
      <div className="gmp-row">
        <GmpTag gmp={ipo.gmp} compact />
        <span className="muted small">Lists {shortDate(ipo.listing_date)}</span>
      </div>
    </article>
  );
}

export default function Market() {
  const { data, error, reload } = useApi<IposResponse>("/api/ipos", 120_000);
  if (error) return <div className="page"><ErrorBox error={error} retry={reload} /></div>;
  if (!data) return <div className="page"><Loading height={90} /><Loading height={320} /></div>;

  const { today, ipos } = data;
  const open = ipos.filter((i) => i.stage === "open").sort((a, b) => (a.bid_end ?? "").localeCompare(b.bid_end ?? ""));
  const closingToday = open.filter((i) => i.bid_end === today);
  const upcoming = ipos
    .filter((i) => i.stage === "pre_apply" && i.bid_start && !i.dormant)
    .sort((a, b) => (a.bid_start ?? "").localeCompare(b.bid_start ?? ""));
  const waiting = ipos.filter((i) => i.stage === "closed").sort((a, b) => (a.listing_date ?? "9").localeCompare(b.listing_date ?? "9"));
  const weekEnd = addDays(today, 7);
  const listingSoon = ipos.filter((i) => i.listing_date && i.listing_date >= today && i.listing_date <= weekEnd && i.stage !== "pre_apply");
  const recent = ipos
    .filter((i) => i.stage === "listed" && i.listing_date && daysBetween(i.listing_date, today) <= 30 && i.listing?.gain_pct !== null && i.listing?.gain_pct !== undefined)
    .sort((a, b) => (b.listing_date ?? "").localeCompare(a.listing_date ?? ""));
  const gains = recent.filter((i) => !i.listing!.flagged).map((i) => i.listing!.gain_pct as number);
  const medianGain = median(gains);
  const positive = gains.filter((g) => g > 0).length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Market board</h1>
          <p>
            {longDate(today)} · stages come from each IPO’s dates. Updates itself through the day.
          </p>
        </div>
      </div>

      <div className="stats">
        <Stat label="Open for bidding" value={open.length} sub={`${open.filter((i) => i.board === "mainboard").length} mainboard · ${open.filter((i) => i.board === "sme").length} SME`} />
        <Stat label="Closing today" value={closingToday.length} tone={closingToday.length ? "warn" : undefined} sub={closingToday.map((i) => i.name).slice(0, 2).join(", ") || "None"} />
        <Stat label="Waiting to list" value={waiting.length} sub="Bidding closed, not listed yet" />
        <Stat label="Listing in 7 days" value={listingSoon.length} sub={`Through ${shortDate(weekEnd)}`} />
        <Stat
          label="Median listing gain, 30 days"
          value={medianGain === null ? "—" : pct(medianGain)}
          tone={medianGain === null ? undefined : medianGain >= 0 ? "good" : "bad"}
          sub={gains.length ? `${positive} of ${gains.length} listed above the issue price` : "No listings yet"}
        />
      </div>

      <SectionTitle title="Open now" count={open.length} />
      {open.length ? (
        <div className="grid grid-cards">
          {open.map((ipo) => (
            <IpoCard ipo={ipo} today={today} key={ipo.id} />
          ))}
        </div>
      ) : (
        <div className="card empty">No IPO is open for bidding today.</div>
      )}

      <div className="grid grid-2">
        <section className="card">
          <div className="card-head">
            <div>
              <h2>Waiting to list</h2>
              <p>Bidding is over: allotment, then listing</p>
            </div>
          </div>
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>IPO</th>
                  <th>Allotment</th>
                  <th>Listing</th>
                  <th className="r">Final subs</th>
                  <th className="r">GMP</th>
                </tr>
              </thead>
              <tbody>
                {waiting.map((ipo) => (
                  <tr key={ipo.id}>
                    <td className="name">
                      <Link to={`/ipo/${ipo.id}`}>{ipo.name}</Link> <span className="muted small">{ipo.board === "sme" ? "SME" : ""}</span>
                    </td>
                    <td className="num">{shortDate(ipo.allotment_date)}</td>
                    <td className="num">{shortDate(ipo.listing_date)}</td>
                    <td className="r num">{times(ipo.subscription?.overall)}</td>
                    <td className="r">
                      {ipo.gmp ? (
                        <span className={`num ${ipo.gmp.gmp_rs >= 0 ? "good" : "bad"}`}>{ipo.gmp.gmp_pct !== null ? pct(ipo.gmp.gmp_pct * 100) : inr(ipo.gmp.gmp_rs)}</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
                {!waiting.length && (
                  <tr>
                    <td colSpan={5} className="muted">Nothing waiting to list.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <div>
              <h2>Opening soon</h2>
              <p>Dates announced, bidding not started</p>
            </div>
          </div>
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>IPO</th>
                  <th>Opens</th>
                  <th>Closes</th>
                  <th className="r">Minimum bid</th>
                </tr>
              </thead>
              <tbody>
                {upcoming.map((ipo) => (
                  <tr key={ipo.id}>
                    <td className="name">
                      <Link to={`/ipo/${ipo.id}`}>{ipo.name}</Link> <span className="muted small">{ipo.board === "sme" ? "SME" : ""}</span>
                    </td>
                    <td className="num">{weekdayDate(ipo.bid_start)}</td>
                    <td className="num">{shortDate(ipo.bid_end)}</td>
                    <td className="r num">{inr(ipo.min_investment)}</td>
                  </tr>
                ))}
                {!upcoming.length && (
                  <tr>
                    <td colSpan={4} className="muted">No dated IPO is coming up yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>Recently listed</h2>
            <p>Last 30 days, newest first. A gain of exactly 0% or −20% is flagged: it may be a default value.</p>
          </div>
          <Link to="/listings" className="link-out small">All listings</Link>
        </div>
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>IPO</th>
                <th>Board</th>
                <th>Listed</th>
                <th className="r">Subscribed</th>
                <th className="r">Issue price</th>
                <th className="r">Listing price</th>
                <th className="r">Gain</th>
              </tr>
            </thead>
            <tbody>
              {recent.slice(0, 12).map((ipo) => (
                <tr key={ipo.id}>
                  <td className="name">
                    <Link to={`/ipo/${ipo.id}`}>{ipo.name}</Link>
                  </td>
                  <td>
                    <BoardPill board={ipo.board} />
                  </td>
                  <td className="num">{shortDate(ipo.listing_date)}</td>
                  <td className="r num">{times(ipo.subscription?.overall)}</td>
                  <td className="r num">{inr(ipo.cutoff)}</td>
                  <td className="r num">{inr(ipo.listing?.price, true)}</td>
                  <td className={`r num ${(ipo.listing?.gain_pct ?? 0) >= 0 ? "good" : "bad"}`}>
                    {pct(ipo.listing?.gain_pct, 2)}
                    {ipo.listing?.flagged && <span className="pill low" style={{ marginLeft: 6 }}>Check</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">{count(ipos.length)} IPOs tracked in all.</p>
      </section>
    </div>
  );
}
