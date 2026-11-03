import { NavLink, Route, Routes } from "react-router-dom";
import type { Health, Session } from "./api";
import { useApi } from "./api";
import { count, istTime } from "./format";
import { Icon } from "./ui";
import Market from "./pages/Market";
import CalendarPage from "./pages/Calendar";
import IpoPage from "./pages/IpoPage";
import CalculatorPage from "./pages/CalculatorPage";
import Listings from "./pages/Listings";
import Calibration from "./pages/Calibration";
import Compare from "./pages/Compare";
import HealthPage from "./pages/Health";

const NAV = [
  { to: "/", label: "Market", icon: "market", end: true },
  { to: "/calendar", label: "Calendar", icon: "calendar" },
  { to: "/calculator", label: "Calculator", icon: "calculator" },
  { to: "/listings", label: "Listings", icon: "listings" },
  { to: "/calibration", label: "Calibration", icon: "calibration" },
  { to: "/compare", label: "Compare", icon: "compare" },
  { to: "/health", label: "Health", icon: "health" },
];

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f0a63a" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 17l6-7 5 4 7-9" />
        </svg>
      </span>
      <span className="brand-name">
        IPO Intelligence
        <span>India · IST</span>
      </span>
    </div>
  );
}

function Nav() {
  return (
    <nav className="nav" aria-label="Main">
      {NAV.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
          <Icon name={item.icon} />
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

function SideStatus() {
  const { data } = useApi<Health>("/api/health", 60_000);
  if (!data) return null;
  const lastRun = data.jobs
    .map((j) => j.finished_at)
    .filter((t): t is string => !!t)
    .sort()
    .pop();
  return (
    <div className="side-foot">
      <div className="row">
        <span className="muted">Schedule</span>
        <span className={data.scheduler_running || data.scheduler_elsewhere ? "good" : "warn"}>
          {data.scheduler_running ? "running" : data.scheduler_elsewhere ? "elsewhere" : "stopped"}
        </span>
      </div>
      <div className="row">
        <span className="muted">IndianAPI left</span>
        <span className="num">{count(data.indianapi.remaining)}</span>
      </div>
      <div className="row">
        <span className="muted">GMP calls today</span>
        <span className="num">
          {data.ipoalerts.used_today}/{data.ipoalerts.daily_cap}
        </span>
      </div>
      <div className="row">
        <span className="muted">Last update</span>
        <span className="num">{lastRun ? istTime(lastRun) : "—"}</span>
      </div>
    </div>
  );
}

/** Who is signed in, with a way out. Nothing when the dashboard has no sign-in (on this machine only). */
function Account({ session, compact = false }: { session: Session | null; compact?: boolean }) {
  if (!session?.sign_in || !session.user) return null;
  return (
    <form method="post" action="/logout" className={compact ? "account compact" : "account"}>
      {!compact && (
        <>
          <span className="avatar" aria-hidden="true">
            {session.user.slice(0, 1).toUpperCase()}
          </span>
          <span className="account-name">
            {session.user}
            <span>Signed in</span>
          </span>
        </>
      )}
      <button type="submit" className="btn ghost icon" title="Sign out" aria-label="Sign out">
        <Icon name="signout" size={15} />
      </button>
    </form>
  );
}

function NotFound() {
  return (
    <div className="page">
      <div className="page-head">
        <h1>Page not found</h1>
      </div>
      <NavLink to="/" className="link-out">Back to the market board</NavLink>
    </div>
  );
}

export default function App() {
  const { data: session } = useApi<Session>("/api/session");
  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <Nav />
        <div className="side-bottom">
          <SideStatus />
          <Account session={session} />
        </div>
      </aside>
      <div style={{ minWidth: 0 }}>
        <header className="topbar">
          <div className="topbar-row">
            <Brand />
            <Account session={session} compact />
          </div>
          <Nav />
        </header>
        <main className="main">
          <Routes>
            <Route path="/" element={<Market />} />
            <Route path="/calendar" element={<CalendarPage />} />
            <Route path="/ipo/:id" element={<IpoPage />} />
            <Route path="/calculator" element={<CalculatorPage />} />
            <Route path="/listings" element={<Listings />} />
            <Route path="/calibration" element={<Calibration />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
