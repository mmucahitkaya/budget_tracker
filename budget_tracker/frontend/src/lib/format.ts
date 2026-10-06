import type { Currency } from "./api";

export let LOCALE = "en-US";
export function setLocale(l: string) {
  if (!l || l === LOCALE) return;
  LOCALE = l;
  fmtCache.clear();
}

/** Household base currency (reports, budgets and totals are in this currency). Set from /api/settings. */
export let BASE: Currency = "USD";
export function setBase(c: string) {
  if (c && /^[A-Z]{3}$/.test(c)) BASE = c;
}

const fmtCache = new Map<string, Intl.NumberFormat>();

function nf(currency: Currency, compact: boolean) {
  const key = `${LOCALE}-${currency}-${compact}`;
  let f = fmtCache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(LOCALE, {
      style: "currency",
      currency,
      minimumFractionDigits: compact ? 0 : 2,
      maximumFractionDigits: compact ? 0 : 2,
    });
    fmtCache.set(key, f);
  }
  return f;
}

export function money(amount: number, currency: Currency = BASE, compact = false) {
  try {
    return nf(currency || BASE, compact).format(amount);
  } catch {
    // Unknown currency code: plain number + code
    return `${new Intl.NumberFormat(LOCALE, { maximumFractionDigits: compact ? 0 : 2 }).format(amount)} ${currency}`;
  }
}

/** Narrow symbol for a currency in the current locale ("$", "€", "£", "₺"); falls back to the code. */
export function currencySymbol(currency: Currency = BASE): string {
  try {
    const part = new Intl.NumberFormat(LOCALE, { style: "currency", currency, currencyDisplay: "narrowSymbol" })
      .formatToParts(0)
      .find((p) => p.type === "currency");
    return part?.value || currency;
  } catch {
    return currency;
  }
}

/** Human currency name ("Euro"), falls back to the code. */
export function currencyName(currency: Currency): string {
  try {
    return new Intl.DisplayNames([LOCALE, "en"], { type: "currency" }).of(currency) ?? currency;
  } catch {
    return currency;
  }
}

/** @deprecated use currencySymbol(); kept so older call sites still compile. */
export const CURRENCY_SYMBOL: Record<Currency, string> = new Proxy({} as Record<Currency, string>, {
  get: (_t, k) => currencySymbol(String(k)),
});

/** Decimal separator of the current locale ("." or ","). */
export function decimalSeparator(locale = LOCALE): string {
  try {
    return new Intl.NumberFormat(locale).formatToParts(1.5).find((p) => p.type === "decimal")?.value ?? ".";
  } catch {
    return ".";
  }
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function monthLabel(ym: string, short = false) {
  const [y, m] = ym.split("-").map(Number);
  return short ? `${MONTHS_SHORT[m - 1]} ${String(y).slice(2)}` : `${MONTHS[m - 1]} ${y}`;
}

export function thisMonth() {
  return toYM(new Date());
}

export function toYM(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function shiftMonth(ym: string, n: number) {
  const [y, m] = ym.split("-").map(Number);
  return toYM(new Date(y, m - 1 + n, 1));
}

export function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function parseDate(s: string) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function dayLabel(s: string) {
  const d = parseDate(s);
  const t = parseDate(today());
  const diff = Math.round((d.getTime() - t.getTime()) / 86400000);
  if (diff === 0) return "Today";
  if (diff === -1) return "Yesterday";
  if (diff === 1) return "Tomorrow";
  return d.toLocaleDateString(LOCALE, { day: "numeric", month: "long", weekday: diff > -7 && diff < 0 ? "long" : undefined });
}

export function shortDate(s: string) {
  return parseDate(s).toLocaleDateString(LOCALE, { day: "numeric", month: "short" });
}

export function daysUntil(s: string) {
  return Math.round((parseDate(s).getTime() - parseDate(today()).getTime()) / 86400000);
}

/**
 * Amount parsing that accepts both separator styles: "1.250" → 1250 (exactly 3 digits after a dot means thousands),
 * "1,25" → 1.25, "1.250,50" → 1250.5, "12.5" → 12.5 (same cases as the server's finance.parse_amount). In addition,
 * when both separators appear the last one is the decimal ("1,234.56" → 1234.56), and in dot-decimal locales
 * (en-US, en-GB…) "1,250" / "12,500,000" are grouped thousands.
 */
export function parseAmount(s: string): number {
  const t = s.replace(/[\s\u00a0'’]/g, "").replace(/[^\d.,+-]/g, "");
  if (!t) return NaN;
  const lastDot = t.lastIndexOf(".");
  const lastComma = t.lastIndexOf(",");
  if (lastDot >= 0 && lastComma >= 0 && lastDot > lastComma) return Number(t.replace(/,/g, ""));
  if (lastComma >= 0 && lastDot < 0 && decimalSeparator() === "." && /^[+-]?\d{1,3}(,\d{3})+$/.test(t)) {
    return Number(t.replace(/,/g, ""));
  }
  if (t.includes(",")) return Number(t.replace(/\./g, "").replace(",", "."));
  if (/^[+-]?\d{1,3}(\.\d{3})+$/.test(t)) return Number(t.replace(/\./g, ""));
  return Number(t);
}

/** Number → editable text using the locale's decimal separator (no grouping, so it parses back unambiguously). */
export function amountInput(n: number | null | undefined) {
  if (n === null || n === undefined || Number.isNaN(n)) return "";
  const s = Number.isInteger(n) ? String(n) : n.toFixed(2);
  return decimalSeparator() === "," ? s.replace(".", ",") : s;
}

/** Unique id. crypto.randomUUID is unavailable when HA is accessed over plain HTTP (not a secure context). */
export function uid(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (c?.getRandomValues) c.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
