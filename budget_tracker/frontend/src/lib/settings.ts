import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Currency } from "./api";
import { setBase, setLocale } from "./format";

/** Household preferences, shape of GET /api/settings. */
export interface Settings {
  household_payday: number;
  spend_buffer: number;
  setup_done: boolean;
  base_currency: Currency;
  /** Enabled currencies, base first */
  currencies: Currency[];
  locale: string;
  /** Region pack: "" = none, "tr" = Turkey */
  region: string;
  investments: boolean;
  supported_currencies: Currency[];
  locales: string[];
  regions: { code: string; name: string }[];
}

export type SettingsPatch = Partial<
  Pick<Settings, "household_payday" | "spend_buffer" | "setup_done" | "base_currency" | "currencies" | "locale" | "region" | "investments">
>;

export const FALLBACK_SETTINGS: Settings = {
  household_payday: 1,
  spend_buffer: 0,
  setup_done: false,
  base_currency: "USD",
  currencies: ["USD", "EUR", "GBP"],
  locale: "en-US",
  region: "",
  investments: true,
  supported_currencies: [
    "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "IDR", "ILS", "INR", "ISK",
    "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "USD", "ZAR",
  ],
  locales: ["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "it-IT", "nl-NL", "pt-BR", "tr-TR"],
  regions: [
    { code: "", name: "None" },
    { code: "tr", name: "Turkey" },
  ],
};

/** Fills fields an older server may not send, and keeps the base currency first in the enabled list. */
export function normalizeSettings(raw: Partial<Settings> | null | undefined): Settings {
  const s = { ...FALLBACK_SETTINGS, ...(raw ?? {}) } as Settings;
  for (const k of ["supported_currencies", "locales", "regions"] as const) {
    if (!Array.isArray(s[k]) || !s[k].length) (s as unknown as Record<string, unknown>)[k] = FALLBACK_SETTINGS[k];
  }
  const cur = Array.isArray(s.currencies) ? s.currencies : [];
  s.currencies = [s.base_currency, ...cur.filter((c) => c !== s.base_currency)];
  return s;
}

/** Apply locale + base currency to the formatting helpers (money(), dates). */
export function applySettings(s: Pick<Settings, "locale" | "base_currency">) {
  setLocale(s.locale);
  setBase(s.base_currency);
  if (typeof document !== "undefined" && s.locale) document.documentElement.lang = s.locale;
}

export const SETTINGS_KEY = ["settings"] as const;

export const fetchSettings = async () => normalizeSettings(await api.get<Partial<Settings>>("settings"));

export const useSettings = () => useQuery({ queryKey: SETTINGS_KEY, queryFn: fetchSettings, staleTime: 60_000 });

/** Enabled currencies (base first); falls back to the base currency alone while loading. */
export function useCurrencies(): Currency[] {
  const s = useSettings().data;
  return s?.currencies ?? FALLBACK_SETTINGS.currencies;
}

/** PUT /api/settings, then apply and cache the returned settings. */
export function useSaveSettings() {
  const qc = useQueryClient();
  return async (patch: SettingsPatch) => {
    const s = normalizeSettings(await api.put<Partial<Settings>>("settings", patch));
    applySettings(s);
    qc.setQueryData(SETTINGS_KEY, s);
    return s;
  };
}

// ---- Labels -----------------------------------------------------------------------

const LOCALE_LABELS: Record<string, string> = {
  "en-US": "English (United States)",
  "en-GB": "English (United Kingdom)",
  "de-DE": "Deutsch (Deutschland)",
  "fr-FR": "Français (France)",
  "es-ES": "Español (España)",
  "it-IT": "Italiano (Italia)",
  "nl-NL": "Nederlands (Nederland)",
  "pt-BR": "Português (Brasil)",
  "tr-TR": "Türkçe (Türkiye)",
};

export function localeLabel(l: string): string {
  if (LOCALE_LABELS[l]) return LOCALE_LABELS[l];
  try {
    return new Intl.DisplayNames([l, "en"], { type: "language" }).of(l) ?? l;
  } catch {
    return l;
  }
}

/** Suggested base currency for a locale (de-DE → EUR, en-GB → GBP, tr-TR → TRY, otherwise USD). */
export function defaultCurrencyFor(locale: string): Currency {
  const region = locale.split("-")[1]?.toUpperCase() ?? "";
  if (["DE", "FR", "ES", "IT", "NL", "AT", "BE", "FI", "IE", "PT", "GR"].includes(region)) return "EUR";
  return ({ GB: "GBP", TR: "TRY", BR: "BRL", CH: "CHF", CA: "CAD", AU: "AUD", JP: "JPY" } as Record<string, Currency>)[region] ?? "USD";
}

/** Best supported locale for the browser language, e.g. "de-AT" → "de-DE". */
export function guessLocale(supported: string[]): string {
  const langs = typeof navigator !== "undefined" ? [...(navigator.languages ?? []), navigator.language] : [];
  for (const l of langs) if (l && supported.includes(l)) return l;
  for (const l of langs) {
    const lang = l?.split("-")[0];
    const m = supported.find((s) => s.split("-")[0] === lang);
    if (m) return m;
  }
  return supported.includes("en-US") ? "en-US" : supported[0] ?? "en-US";
}

/** Live format sample for a locale: "1,234.56 · Oct 6, 2026". */
export function localeSample(locale: string, d = new Date()): string {
  try {
    const n = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(1234.56);
    const date = d.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
    return `${n} · ${date}`;
  } catch {
    return "1,234.56";
  }
}

export const REGION_INFO: Record<string, string> = {
  "": "Generic setup: ECB exchange rates and a standard investment catalog.",
  tr: "Adds Turkish gold coins (çeyrek, yarım, tam…) with live prices, Borsa Istanbul tickers and central bank (TCMB) exchange rates.",
};
