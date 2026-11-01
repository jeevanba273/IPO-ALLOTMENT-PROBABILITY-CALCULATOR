// Data health: API quotas, the schedule, recent calls. IndianAPI jobs can be started here;
// GMP jobs cannot, because only their schedule may spend the ipoalerts budget.
import { useState } from "react";
import type { Health } from "../api";
import { api, useApi } from "../api";
import { ago, count, istDateTime, untilText } from "../format";
import { Card, ErrorBox, Icon, Loading } from "../ui";

const JOB_LABEL: Record<string, string> = {
  usage: "IndianAPI quota check",
  master: "IPO lists",
  "open-details": "Open IPO details",
  listed: "Listing gains and symbols",
  backfill: "History backfill (IndianAPI)",
  "gmp-live": "GMP polls",
  "gmp-backfill": "GMP history backfill",
  alerts: "Telegram alerts",
};

function Bar({ used, total, tone }: { used: number; total: number; tone?: "warn" | "bad" }) {
  const share = total ? Math.min(1, used / total) : 0;
  const color = tone === "bad" ? "var(--bad)" : tone === "warn" ? "var(--warn)" : "var(--accent)";
  return (
    <div className="meter" style={{ height: 10 }}>
      <span style={{ left: 0, width: `${share * 100}%`, background: color }} />
    </div>
  );
}

export default function HealthPage() {
  const { data, error, reload } = useApi<Health>("/api/health", 30_000);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (error) return <div className="page"><ErrorBox error={error} retry={reload} /></div>;
  if (!data) return <div className="page"><Loading height={420} /></div>;

  const runJob = async (job: string) => {
    setBusy(job);
    setMessage(null);
    try {
      await api(`/api/jobs/${job}/run`, { method: "POST" });
      setMessage(`${JOB_LABEL[job] ?? job} started. Results appear here in a moment.`);
      window.setTimeout(reload, 4000);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const ia = data.indianapi;
  const ga = data.ipoalerts;
  const iaTone = ia.level === "essential_only" || ia.level === "exhausted" ? "bad" : ia.level === "backfill_paused" ? "warn" : undefined;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Data health</h1>
          <p>Everything runs by itself on an IST schedule. This page shows what ran, what is next and how much quota is left.</p>
        </div>
        <div className="toolbar">
          <span className={`pill ${data.telegram_configured ? "ok" : "skipped"}`}>
            <span className="dot" /> Telegram {data.telegram_configured ? "on" : "not set up"}
          </span>
          <span className={`pill ${data.scheduler_running || data.scheduler_elsewhere ? "ok" : "failed"}`}>
            <span className="dot" /> Schedule {data.scheduler_running ? "running here" : data.scheduler_elsewhere ? "running in another copy" : "stopped"}
          </span>
        </div>
      </div>

      <div className="grid grid-2">
        <Card title="IndianAPI" note="Lists, subscription, listing gains · 10,000 requests per key, shared with your other apps">
          <div className="stack">
            <div className="split">
              <span className="num" style={{ fontSize: 24, fontWeight: 600 }}>{count(ia.remaining)}</span>
              <span className="muted small">left of {count(ia.hard_limit)}</span>
            </div>
            <Bar used={(ia.hard_limit ?? 0) - (ia.remaining ?? 0)} total={ia.hard_limit ?? 0} tone={iaTone} />
            <span className="muted small">
              {ia.level === "normal" ? "Normal." : ia.description}. Backfill pauses below {count(ia.pause_backfill_below)}; only essential calls below {count(ia.essential_only_below)}. Last checked {ago(ia.checked_at, data.now)}.
            </span>
          </div>
        </Card>
        <Card title="ipoalerts" note="GMP only · hard caps protect the free plan">
          {ga.configured ? (
            <div className="stack">
              <div className="split">
                <span className="num" style={{ fontSize: 24, fontWeight: 600 }}>{ga.used_today} / {ga.daily_cap}</span>
                <span className="muted small">calls today · resets 00:00 IST</span>
              </div>
              <Bar used={ga.used_today} total={ga.daily_cap} tone={ga.used_today >= ga.daily_cap ? "warn" : undefined} />
              <span className="muted small">
                Live {ga.live_today}, backfill {ga.backfill_today} of {ga.backfill_cap}. This month {ga.used_month} of {ga.monthly_cap}.
              </span>
              <Bar used={ga.used_month} total={ga.monthly_cap} />
              {ga.blocked_until && (
                <div className="notice bad">
                  <Icon name="alert" />
                  <span>Paused until {istDateTime(ga.blocked_until)}: {ga.block_reason}</span>
                </div>
              )}
            </div>
          ) : (
            <div className="notice"><Icon name="info" /><span>No ipoalerts key yet: add IPOALERTS_KEY to the .env file.</span></div>
          )}
        </Card>
      </div>

      <Card title="Jobs" note="The last run and the next scheduled run of each job" action={message ? <span className="muted small">{message}</span> : undefined}>
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>Job</th>
                <th>Last result</th>
                <th>Ran</th>
                <th>Next</th>
                <th>What it did</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((job) => (
                <tr key={job.job}>
                  <td style={{ fontWeight: 700 }}>{JOB_LABEL[job.job] ?? job.job}</td>
                  <td>{job.status ? <span className={`pill ${job.status}`}>{job.status}</span> : <span className="muted">never</span>}</td>
                  <td className="num" title={job.started_at ?? ""}>{job.started_at ? ago(job.started_at, data.now) : "—"}</td>
                  <td className="num">{job.next_run ? untilText(job.next_run, data.now) : <span className="muted">by hand</span>}</td>
                  <td className="wrap">{job.summary ?? "—"}</td>
                  <td>
                    {job.budgeted ? (
                      <span className="muted small" title="Only the schedule spends ipoalerts calls">automatic</span>
                    ) : (
                      <button className="btn small" disabled={busy !== null} onClick={() => runJob(job.job)}>
                        {busy === job.job ? "Starting…" : "Run now"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Recent API calls" note={`Every attempt is logged, skipped ones too · ${count(data.counts.ipos)} IPOs, ${count(data.counts.gmp_points)} GMP points, ${count(data.counts.subscription_rows)} subscription rows stored`}>
        <div className="table-wrap tall">
          <table className="t">
            <thead>
              <tr>
                <th>When</th>
                <th>Source</th>
                <th>Result</th>
                <th className="r">HTTP</th>
                <th className="r">Time</th>
                <th>Request</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_calls.map((call, i) => (
                <tr key={i}>
                  <td className="num">{istDateTime(call.ran_at)}</td>
                  <td>{call.source}</td>
                  <td><span className={`pill ${call.status === "ok" ? "ok" : call.status.startsWith("skipped") ? "skipped" : "failed"}`}>{call.status}</span></td>
                  <td className="r num">{call.http_status ?? "—"}</td>
                  <td className="r num">{call.duration_ms} ms</td>
                  <td className="num small" style={{ whiteSpace: "normal", wordBreak: "break-all", minWidth: 240 }}>{call.endpoint}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
