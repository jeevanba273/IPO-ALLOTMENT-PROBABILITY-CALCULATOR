// Types and fetch helpers for the dashboard API. The browser only ever talks to
// this app's own /api; it never calls IndianAPI or ipoalerts.
import { useCallback, useEffect, useRef, useState } from "react";

export type Stage = "pre_apply" | "open" | "closed" | "listed";
export type Board = "mainboard" | "sme";
export type Confidence = "high" | "low";
export type CategoryKey = "retail" | "nii" | "qib" | "overall";
export type InvestorCategory = "retail" | "snii" | "bnii";

export interface Gmp {
  gmp_rs: number;
  gmp_pct: number | null;
  confidence: Confidence;
  source: "ipoalerts" | "manual";
  ts: string;
  recent_points: number;
  age_hours: number;
}

export interface SubDay {
  day: number;
  bid_date: string;
  retail?: number;
  nii?: number;
  qib?: number;
  overall?: number;
}

export interface Listing {
  price: number;
  gain_pct: number | null;
  flagged: boolean;
  source: string | null;
}

export interface IpoSummary {
  id: string;
  name: string;
  board: Board;
  industry: string | null;
  stage: Stage;
  dormant: boolean;
  pre_apply_start: string | null;
  bid_start: string | null;
  bid_end: string | null;
  allotment_date: string | null;
  listing_date: string | null;
  price_min: number | null;
  price_max: number | null;
  cutoff: number | null;
  face_value: number | null;
  issue_size_cr: number | null;
  lot_size: number | null;
  min_qty: number | null;
  min_lots: number | null;
  max_retail_lots: number | null;
  min_snii_lots: number | null;
  min_bnii_lots: number | null;
  min_investment: number | null;
  min_category: InvestorCategory | null;
  symbol: string | null;
  symbol_source: string | null;
  registrar_name: string | null;
  allotment_link: string | null;
  subscription: SubDay | null;
  gmp: Gmp | null;
  listing: Listing | null;
  detail_fetched_at: string | null;
}

export interface IposResponse {
  today: string;
  now: string;
  ipos: IpoSummary[];
}

export interface OddsResult {
  category: InvestorCategory;
  basis: "current" | "final" | "projected";
  times_low: number;
  times_high: number;
  p_low: number;
  p_high: number;
  any_low: number;
  any_high: number;
  shares_low: number;
  shares_high: number;
}

export interface Projection {
  status: "ok" | "final" | "not_enough_history" | "no_bids_yet";
  message: string;
  current: number;
  day: number;
  samples: number;
  low: number | null;
  q1: number | null;
  median: number | null;
  q3: number | null;
  high: number | null;
}

export interface DayFigures {
  day: number;
  bid_date: string;
  complete: boolean;
  figures: Record<string, { applied: number; reserved: number; times: number; source: string }>;
}

export interface Ev {
  low: number;
  high: number;
  point: number | null;
  confidence: Confidence;
  bands: { multiplier: number; low: number; high: number }[];
}

export interface Analysis {
  ipo_id: string;
  stage: Stage;
  board: Board;
  lot_size: number;
  min_qty: number;
  min_lots: number;
  cutoff: number;
  min_investment: number;
  min_category: InvestorCategory;
  lots: number;
  applications: number;
  amount_rs: number;
  category: InvestorCategory;
  category_label: string;
  method: "lottery" | "proportionate";
  latest: DayFigures | null;
  base: DayFigures | null;
  projections: Record<string, Projection>;
  odds_now: OddsResult | null;
  odds_final: OddsResult | null;
  gmp: Gmp | null;
  gmp_used: number | null;
  ev: Ev | null;
  notes: string[];
  warnings: string[];
}

export interface GmpPoint {
  ts: string;
  gmp_rs: number;
  source: "ipoalerts" | "manual";
  interval: string;
}

export interface ReservationRow {
  category: "retail" | "nii" | "qib";
  indianapi: number | null;
  manual: number | null;
  note: string | null;
}

export interface IpoDetail extends IpoSummary {
  subscription_history: SubDay[];
  gmp_series: GmpPoint[];
  symbols: { symbol: string; source: string }[];
  reservation: ReservationRow[];
  reservation_warning: string | null;
  analysis: Analysis | null;
}

export interface CalibrationRow {
  id: string;
  name: string;
  board: Board;
  listing_date: string;
  cutoff: number;
  listing_price: number | null;
  actual_pct: number;
  flagged: boolean;
  subscription: number | null;
  gmp_rs: number | null;
  gmp_ts: string | null;
  gmp_source: string | null;
  confidence: Confidence | null;
  implied_pct: number | null;
  error_pp: number | null;
}

export interface BoardSummary {
  listings: number;
  with_gmp: number;
  median_error_pp: number | null;
  median_abs_error_pp: number | null;
  within_5pp: number | null;
}

export interface MultiplierRow {
  board: Board;
  category: CategoryKey;
  day: number;
  samples: number;
  low: number | null;
  median: number | null;
  high: number | null;
}

export interface CalibrationResponse {
  rows: CalibrationRow[];
  summary: Record<Board, BoardSummary>;
  histories: Record<Board, number>;
  min_samples: number;
  multipliers: MultiplierRow[];
}

export interface JobInfo {
  job: string;
  budgeted: boolean;
  scheduled: boolean;
  next_run: string | null;
  status: "running" | "ok" | "partial" | "skipped" | "failed" | null;
  trigger: string | null;
  started_at: string | null;
  finished_at: string | null;
  summary: string | null;
}

export interface CallInfo {
  source: string;
  endpoint: string;
  purpose: string | null;
  status: string;
  http_status: number | null;
  duration_ms: number;
  attempt: number;
  ran_at: string;
}

export interface Health {
  now: string;
  today: string;
  scheduler_running: boolean;
  scheduler_elsewhere: boolean;
  telegram_configured: boolean;
  indianapi: {
    remaining: number | null;
    hard_limit: number | null;
    level: string;
    description: string;
    checked_at: string | null;
    pause_backfill_below: number;
    essential_only_below: number;
  };
  ipoalerts: {
    configured: boolean;
    day: string;
    used_today: number;
    daily_cap: number;
    live_today: number;
    backfill_today: number;
    backfill_cap: number;
    used_month: number;
    monthly_cap: number;
    blocked_until: string | null;
    block_reason: string | null;
  };
  jobs: JobInfo[];
  recent_calls: CallInfo[];
  counts: { ipos: number; with_details: number; subscription_rows: number; gmp_points: number };
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
      else if (Array.isArray(body?.detail)) message = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch `path` (null = wait), refetching every `refreshMs` while the page is visible. */
export function useApi<T>(path: string | null, refreshMs = 0): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const latest = useRef(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (path === null) return;
    const id = ++latest.current;
    setLoading(true);
    api<T>(path)
      .then((result) => {
        if (id !== latest.current) return;
        setData(result);
        setError(null);
      })
      .catch((err: unknown) => {
        if (id !== latest.current) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (id === latest.current) setLoading(false);
      });
  }, [path, tick]);

  useEffect(() => {
    if (!refreshMs) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick((t) => t + 1);
    }, refreshMs);
    return () => window.clearInterval(timer);
  }, [refreshMs]);

  return { data, error, loading, reload };
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
