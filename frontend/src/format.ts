// Formatting. Calendar dates ('YYYY-MM-DD') are Indian market days and are shown
// as they are; instants (ISO with Z) are converted to IST.
import type { InvestorCategory, Stage } from "./api";

const IST = "Asia/Kolkata";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const rupees = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const rupeesPaise = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const grouped = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function inr(value: number | null | undefined, paise = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const withPaise = paise || (Math.abs(value) < 1000 && !Number.isInteger(Math.round(value * 100) / 100));
  return (withPaise ? rupeesPaise : rupees).format(value);
}

export function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : grouped.format(value);
}

export function crore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: value < 100 ? 2 : 0 }).format(value)} Cr`;
}

export function times(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 100) return `${grouped.format(value)}×`;
  return `${value.toFixed(2)}×`;
}

export function pct(value: number | null | undefined, digits = 1, signed = true): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const text = `${Math.abs(value).toFixed(digits)}%`;
  if (!signed) return text;
  return value > 0 ? `+${text}` : value < 0 ? `−${text}` : text;
}

export function prob(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 0.9995) return "100%";
  if (value > 0 && value < 0.001) return "<0.1%";
  return `${(value * 100).toFixed(1)}%`;
}

export function probRange(low: number, high: number): string {
  const a = prob(low);
  const b = prob(high);
  return a === b ? a : `${a} – ${b}`;
}

export function timesRange(low: number, high: number): string {
  return Math.abs(high - low) < 0.005 ? times(low) : `${times(low)} – ${times(high)}`;
}

function parts(day: string): [number, number, number] {
  const [y, m, d] = day.split("-").map(Number);
  return [y, m, d];
}

export function dateOnly(day: string): Date {
  const [y, m, d] = parts(day);
  return new Date(Date.UTC(y, m - 1, d));
}

export function shortDate(day: string | null | undefined): string {
  if (!day) return "—";
  const [, m, d] = parts(day);
  return `${String(d).padStart(2, "0")} ${MONTHS[m - 1]}`;
}

export function longDate(day: string | null | undefined): string {
  if (!day) return "—";
  const date = dateOnly(day);
  return `${WEEKDAYS[date.getUTCDay()]} ${shortDate(day)} ${date.getUTCFullYear()}`;
}

export function weekdayDate(day: string | null | undefined): string {
  if (!day) return "—";
  return `${WEEKDAYS[dateOnly(day).getUTCDay()]} ${shortDate(day)}`;
}

export function istDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: IST, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(new Date(iso)) + " IST";
}

export function istTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", { timeZone: IST, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(iso));
}

export function ago(iso: string | null | undefined, nowIso?: string): string {
  if (!iso) return "never";
  const now = nowIso ? new Date(nowIso).getTime() : Date.now();
  const minutes = Math.round((now - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function untilText(iso: string | null | undefined, nowIso?: string): string {
  if (!iso) return "—";
  const now = nowIso ? new Date(nowIso).getTime() : Date.now();
  const minutes = Math.round((new Date(iso).getTime() - now) / 60000);
  if (minutes <= 0) return "now";
  if (minutes < 60) return `in ${minutes} min`;
  const hours = minutes / 60;
  if (hours < 36) return `in ${Math.round(hours)} h`;
  return `in ${Math.round(hours / 24)} days`;
}

/** Whole days from `from` to `to` (both 'YYYY-MM-DD'). */
export function daysBetween(from: string, to: string): number {
  return Math.round((dateOnly(to).getTime() - dateOnly(from).getTime()) / 86_400_000);
}

export function addDays(day: string, n: number): string {
  const date = dateOnly(day);
  date.setUTCDate(date.getUTCDate() + n);
  return date.toISOString().slice(0, 10);
}

export const STAGE_LABEL: Record<Stage, string> = {
  pre_apply: "Upcoming",
  open: "Open",
  closed: "Closed",
  listed: "Listed",
};

export const CATEGORY_LABEL: Record<InvestorCategory, string> = {
  retail: "Retail",
  snii: "sNII",
  bnii: "bNII",
};

export const BOOK_LABEL: Record<string, string> = {
  retail: "Retail",
  nii: "NII",
  qib: "QIB",
  overall: "Overall",
};
