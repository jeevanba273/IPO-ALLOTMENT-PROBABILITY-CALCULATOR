// IPO calendar: bidding opens and closes, allotment and listing days.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { IpoSummary, IposResponse } from "../api";
import { useApi } from "../api";
import { addDays, dateOnly, weekdayDate } from "../format";
import { Card, ErrorBox, Loading } from "../ui";

type Kind = "open" | "close" | "allot" | "list";
interface CalEvent {
  day: string;
  kind: Kind;
  ipo: IpoSummary;
}

const KIND_LABEL: Record<Kind, string> = { open: "Opens", close: "Closes", allot: "Allotment", list: "Lists" };
const KIND_CLASS: Record<Kind, string> = { open: "ev-open", close: "ev-close", allot: "ev-allot", list: "ev-list" };
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function eventsFor(ipos: IpoSummary[]): Map<string, CalEvent[]> {
  const map = new Map<string, CalEvent[]>();
  const add = (day: string | null, kind: Kind, ipo: IpoSummary) => {
    if (!day) return;
    if (!map.has(day)) map.set(day, []);
    map.get(day)!.push({ day, kind, ipo });
  };
  for (const ipo of ipos) {
    if (!ipo.bid_start || ipo.dormant) continue;
    add(ipo.bid_start, "open", ipo);
    add(ipo.bid_end, "close", ipo);
    add(ipo.allotment_date, "allot", ipo);
    add(ipo.listing_date, "list", ipo);
  }
  const order: Kind[] = ["list", "close", "open", "allot"];
  for (const list of map.values()) list.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || a.ipo.name.localeCompare(b.ipo.name));
  return map;
}

export default function CalendarPage() {
  const { data, error, reload } = useApi<IposResponse>("/api/ipos", 300_000);
  const [offset, setOffset] = useState(0);
  const events = useMemo(() => eventsFor(data?.ipos ?? []), [data]);
  if (error) return <div className="page"><ErrorBox error={error} retry={reload} /></div>;
  if (!data) return <div className="page"><Loading height={500} /></div>;

  const today = data.today;
  const base = dateOnly(today);
  const month = new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth() + offset, 1));
  const first = month.toISOString().slice(0, 10);
  const startPad = (month.getUTCDay() + 6) % 7; // weeks start on Monday
  const gridStart = addDays(first, -startPad);
  const cells = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
  const monthKey = first.slice(0, 7);
  const agenda = Array.from({ length: 21 }, (_, i) => addDays(today, i)).filter((d) => events.has(d));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>IPO calendar</h1>
          <p>Bidding windows, allotment and listing days, from each IPO’s timeline.</p>
        </div>
        <div className="legend">
          {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
            <span key={k}>
              <span className={`cal-ev ${KIND_CLASS[k]}`}>{KIND_LABEL[k]}</span>
            </span>
          ))}
        </div>
      </div>

      <Card
        title={`${MONTHS[month.getUTCMonth()]} ${month.getUTCFullYear()}`}
        action={
          <div className="toolbar">
            <button className="btn small" onClick={() => setOffset((o) => o - 1)} aria-label="Previous month">‹</button>
            <button className="btn small" onClick={() => setOffset(0)}>This month</button>
            <button className="btn small" onClick={() => setOffset((o) => o + 1)} aria-label="Next month">›</button>
          </div>
        }
      >
        <div className="cal-wrap">
          <div className="cal">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
              <div className="cal-dow" key={d}>{d}</div>
            ))}
            {cells.map((day) => (
              <div key={day} className={`cal-day ${day.slice(0, 7) !== monthKey ? "out" : ""} ${day === today ? "today" : ""}`}>
                <span className="d">{Number(day.slice(8))}</span>
                {(events.get(day) ?? []).slice(0, 5).map((ev) => (
                  <Link key={`${ev.kind}-${ev.ipo.id}`} to={`/ipo/${ev.ipo.id}`} className={`cal-ev ${KIND_CLASS[ev.kind]}`} title={`${KIND_LABEL[ev.kind]}: ${ev.ipo.name}`}>
                    {ev.ipo.name}
                  </Link>
                ))}
                {(events.get(day)?.length ?? 0) > 5 && <span className="muted small">+{events.get(day)!.length - 5} more</span>}
              </div>
            ))}
          </div>
        </div>
      </Card>

      <Card title="Next three weeks" note="Everything that happens from today">
        {agenda.length ? (
          <div className="agenda">
            {agenda.map((day) => (
              <div className="agenda-day" key={day}>
                <span className={`when ${day === today ? "today" : ""}`}>{day === today ? "Today" : weekdayDate(day)}</span>
                <div className="agenda-items">
                  {events.get(day)!.map((ev) => (
                    <div className="agenda-item" key={`${ev.kind}-${ev.ipo.id}`}>
                      <span className={`cal-ev ${KIND_CLASS[ev.kind]}`}>{KIND_LABEL[ev.kind]}</span>
                      <Link to={`/ipo/${ev.ipo.id}`} style={{ fontWeight: 700 }}>{ev.ipo.name}</Link>
                      <span className="muted small">{ev.ipo.board === "sme" ? "SME" : "Mainboard"}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty">Nothing scheduled in the next three weeks.</div>
        )}
      </Card>
    </div>
  );
}
