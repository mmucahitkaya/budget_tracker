// All requests are relative so the HA ingress /api/hassio_ingress/<token>/ prefix is preserved
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(`api/${path}`, init);
  if (!res.ok) {
    // HA ingress returns 502-504 while the add-on restarts/updates
    if (res.status >= 502 && res.status <= 504) {
      throw new ApiError(res.status, "The app is restarting. Try again in a few seconds.");
    }
    let msg = `Error (${res.status})`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg: string }) => d.msg).join(", ");
    } catch {
      /* body is not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export const fileUrl = (docId: number) => `api/documents/${docId}/file`;

// ---- Types -----------------------------------------------------------------

/** ISO 4217 code, e.g. "USD". Enabled currencies come from /api/settings (see lib/settings.ts). */
export type Currency = string;
/** Amounts per currency, e.g. { USD: 120.5, EUR: 30 } */
export type CurrencyAmounts = Record<Currency, number>;
export type Kind = "expense" | "income";
export type Method = "cash" | "bank" | "card" | "voucher";

export interface UserT {
  id: number;
  name: string;
}
export interface Me {
  me: UserT;
  users: UserT[];
  ai_enabled: boolean;
  notify_enabled: boolean;
  telegram: { enabled: boolean; bot: string | null; linked: boolean; linked_users: string[] };
}
export interface Category {
  essential?: boolean;
  id: number;
  name: string;
  kind: Kind;
  icon: string;
  color: string;
  archived: boolean;
}
export interface Tx {
  spending_type?: "variable" | "fixed" | "one_off";
  id: number;
  kind: Kind;
  amount: number;
  currency: Currency;
  date: string;
  category_id: number | null;
  user_id: number | null;
  payment_method: Method;
  card_id: number | null;
  merchant: string;
  note: string;
  items: { description: string; amount: number }[] | null;
  document_id: number | null;
  recurring_id: number | null;
  time: string | null;
  last_change?: { by: string; at: string } | null;
  installment_count: number | null;
  installment_monthly: number | null;
  /** Month number within an installment series (each month is a separate transaction) */
  installment_no: number | null;
  /** Note written by the user (excluding system notes), e.g. "Headphones" */
  user_note: string;
}
export interface Statement {
  id: number;
  card_id: number;
  period_end: string;
  due_date: string;
  /** Primary statement currency (minimum payment is in this currency) */
  currency: Currency;
  totals: CurrencyAmounts;
  min_payment: number;
  /** Paid so far per currency (older servers sent a boolean "settled" flag here) */
  paid: CurrencyAmounts | boolean;
  /** Settled flag, if the server sends it separately */
  is_paid?: boolean;
  remaining: CurrencyAmounts;
  document_id: number | null;
}
export interface Payment {
  id: number;
  card_id: number;
  statement_id: number | null;
  amount: number;
  currency: Currency;
  date: string;
  user_id: number | null;
  note: string;
}
export interface Plan {
  id: number;
  card_id: number;
  description: string;
  monthly: number;
  currency: Currency;
  count: number;
  first_month: string;
  remaining: number;
  remaining_total: number;
}
export interface Card {
  id: number;
  name: string;
  bank: string;
  last4: string;
  limit: number | null;
  available: number | null;
  statement_day: number;
  due_day: number;
  owner_id: number | null;
  holder_id: number | null;
  color: string;
  archived: boolean;
  /** Current balance per currency, e.g. { USD: 1200, EUR: 35 } */
  debts: CurrencyAmounts;
  debt_updated_at: string | null;
  last_cut: string;
  next_due_date: string;
  next_statement_date: string;
  period_spend: Partial<Record<Currency, number>>;
  unpaid_statement: Statement | null;
  statements: Statement[];
  payments: Payment[];
  installments: Plan[];
  schedule: { month: string; totals: Partial<Record<Currency, number>> }[];
}
export interface Recurring {
  id: number;
  name: string;
  kind: Kind;
  amount: number;
  currency: Currency;
  frequency: "weekly" | "monthly" | "yearly";
  next_date: string;
  category_id: number | null;
  payment_method: Method;
  card_id: number | null;
  user_id: number | null;
  auto_create: boolean;
  active: boolean;
  note: string;
  end_date: string | null;
  remaining_count: number | null;
  remaining_total: number | null;
  last_business_day: boolean;
  raise_months: number[];
  next_amount: number;
  /** For items paid in gold, e.g. "2 quarter gold coins"; the base-currency amount uses the current price */
  asset_label: string | null;
  /** Upcoming payments when the amount varies by month */
  schedule: { date: string; amount: number }[];
}
export interface Planned {
  recurring_id: number;
  name: string;
  date: string;
  kind: Kind;
  amount: number;
  currency: Currency;
  category_id: number | null;
  payment_method: Method;
  card_id: number | null;
  user_id: number | null;
}
export interface BudgetRow {
  id: number;
  category_id: number;
  limit: number;
  spent: number;
  ratio: number;
}
export interface Upcoming {
  type: "recurring" | "card";
  id: number;
  statement_id?: number;
  title: string;
  date: string;
  amount: number;
  currency: Currency;
  kind: Kind;
  min_payment?: number;
}
export interface Summary {
  month: string;
  income: number;
  expense: number;
  net: number;
  prev_expense: number;
  by_currency: Partial<Record<Currency, { income: number; expense: number }>>;
  by_category: { category_id: number | null; total: number }[];
  by_user: { user_id: number | null; total: number }[];
  card_debt: Record<Currency, number>;
  upcoming: Upcoming[];
  planned: { expense: number; income: number; count: number };
  fx_missing: FxMissing[];
}
export interface FxMissing {
  currency: Currency;
  amount: number;
}
export interface DraftRow {
  line_amount?: number;
  include: boolean;
  mode: "transaction" | "installment_only";
  kind: Kind;
  date: string;
  amount: number;
  currency: Currency;
  merchant: string;
  category_id: number | null;
  payment_method: Method;
  card_id: number | null;
  installment_count: number | null;
  installment_no: number | null;
  duplicate_of: number | null;
  match?: number | null;
  match_candidates?: MatchCandidate[];
  items: { description: string; amount: number }[] | null;
  note?: string;
}
export interface MatchCandidate {
  tx_id: number;
  merchant: string;
  date: string;
  amount: number;
  currency: Currency;
  score: number;
  strong: boolean;
}
export interface Draft {
  doc_type: "receipt" | "statement" | "other";
  card_id: number | null;
  statement: {
    period_end: string;
    due_date: string;
    /** Primary statement currency */
    currency: Currency;
    totals: CurrencyAmounts;
    min_payment: number;
    period_spending?: number | null;
  } | null;
  rows: DraftRow[];
}
export interface Doc {
  id: number;
  kind: string;
  filename: string;
  mime: string;
  status: "pending" | "processing" | "review" | "done" | "error" | "discarded";
  error: string;
  card_id: number | null;
  uploaded_by: number | null;
  created_at: string | null;
  doc_type: string | null;
  row_count: number;
  draft?: Draft | null;
}

// ---- Per-currency helpers ----------------------------------------------------

/** Non-zero entries of a currency dict, with `first` (e.g. the base or statement currency) leading. */
export function amountEntries(d: CurrencyAmounts | null | undefined, first?: Currency): [Currency, number][] {
  const e = Object.entries(d ?? {}).filter(([, v]) => typeof v === "number" && Math.abs(v) >= 0.005);
  return e.sort(([a], [b]) => (a === first ? -1 : b === first ? 1 : a.localeCompare(b)));
}

/** Amount paid so far on a statement, per currency. */
export function statementPaid(s: Statement): CurrencyAmounts {
  return typeof s.paid === "object" && s.paid ? s.paid : {};
}

/** Whether nothing is left to pay on the statement. */
export function statementSettled(s: Statement): boolean {
  if (typeof s.is_paid === "boolean") return s.is_paid;
  if (typeof s.paid === "boolean") return s.paid;
  return amountEntries(s.remaining).every(([, v]) => v <= 0.005);
}
