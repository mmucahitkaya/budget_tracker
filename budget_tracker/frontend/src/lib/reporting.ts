import { today, toYM } from "./format";
import type { Currency } from "./api";
export type ReportTab = "summary" | "analysis" | "plan";
export function reportPeriod(month: string, count: number, payday = 1, now = today()) {
  const [y, m] = month.split("-").map(Number);
  const endOfMonth = new Date(y, m, 0).getDate();
  const clamp = (year: number, mon: number, day: number) =>
    `${year}-${String(mon + 1).padStart(2, "0")}-${String(Math.min(day, new Date(year, mon + 1, 0).getDate())).padStart(2, "0")}`;
  const first = new Date(y, m - count, 1);
  const start = clamp(first.getFullYear(), first.getMonth(), payday);
  let end = `${month}-${endOfMonth}`;
  if (payday > 1) {
    const next = new Date(y, m, 1);
    const boundary = clamp(next.getFullYear(), next.getMonth(), payday);
    const d = new Date(`${boundary}T12:00:00`);
    d.setDate(d.getDate() - 1);
    end = `${toYM(d)}-${String(d.getDate()).padStart(2, "0")}`;
  }
  return { start, end: end > now ? now : end };
}
export function transactionLink(start: string, end: string, filters: string, extra: Record<string, string | number> = {}) {
  const q = new URLSearchParams(filters);
  q.set("start", start);
  q.set("end", end);
  Object.entries(extra).forEach(([key, value]) => q.set(key, String(value)));
  return `/transactions?${q}`;
}
export interface Workspace {
  average_ticket: number;
  monthly_average: number;
  ytd: { year: number; income: number; expense: number; fx_missing: { currency: Currency; amount: number }[] };
  start: string;
  end: string;
  comparison_start: string;
  comparison_end: string;
  expense: number;
  income: number;
  net: number;
  surplus_rate: number | null;
  /** Reason when there is no rate (e.g. no income yet) */
  rate_reason?: string | null;
  /** Recurring income/expense expected for the rest of the period, and the projected rate including them */
  planned_income?: number;
  planned_expense?: number;
  expected_rate?: number | null;
  daily_avg: number;
  median: number;
  count: number;
  quality: { review_documents: number; uncategorized_count: number; uncategorized_amount: number; recorded_since: string | null };
  fx_missing: { currency: Currency; amount: number }[];
  comparison_fx_missing: { currency: Currency; amount: number }[];
  budget_fx_missing: { currency: Currency; amount: number }[];
  changes: {
    category_id: number | null;
    name: string;
    current: number;
    previous: number;
    delta: number;
    count: number;
    previous_count: number;
    average: number;
    previous_average: number;
  }[];
  series: { month: string; expense: number; income: number; fixed: number; variable: number; one_off: number }[];
  calendar: { date: string; total: number; count: number }[];
  merchants: { name: string; display: string; total: number; count: number }[];
  budgets: { category_id: number; name: string; spent: number; planned: number; limit: number }[];
  budget_month: string;
  subscriptions: {
    id: number;
    name: string;
    currency: Currency;
    amount: number;
    frequency: string;
    monthly: number;
    annual: number;
    previous: number | null;
    history: { date: string; amount: number }[];
  }[];
  insights: string[];
  weekday: { day: string; total: number; count: number; average: number }[];
  by_method: { key: string; total: number }[];
  by_card: { key: number | null; total: number }[];
  by_user: { key: number | null; total: number }[];
  income_categories: { category_id: number | null; name: string; total: number }[];
  largest: { id: number; merchant: string; date: string; amount: number; currency: Currency }[];
}
