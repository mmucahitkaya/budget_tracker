import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ErrorState, Field, Icon, PageHeader, PrimaryButton, Row, Section, Select, Spinner, TextButton, Toggle, toast } from "../components/ui";
import type { Currency, Me } from "../lib/api";
import { currencyName } from "../lib/format";
import { useMe } from "../lib/queries";
import { REGION_INFO, localeLabel, localeSample, useSaveSettings, useSettings, type Settings, type SettingsPatch } from "../lib/settings";

// ---- Shared building blocks (also used by the first-run wizard) -------------------------------

/** Radio-style list of region packs with a one-line explanation each. */
export function RegionChooser({ regions, value, onChange }: { regions: Settings["regions"]; value: string; onChange: (code: string) => void }) {
  return (
    <>
      {regions.map((r) => {
        const on = r.code === value;
        return (
          <Row key={r.code || "none"} onClick={() => onChange(r.code)}>
            <span className="text-[22px]" aria-hidden>
              {r.code === "tr" ? "🇹🇷" : "🌍"}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[17px]">{r.code ? r.name : "No region pack"}</div>
              <div className="text-[13px] text-label-2">{REGION_INFO[r.code] ?? `Extras for ${r.name}.`}</div>
            </div>
            <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${on ? "bg-accent text-white" : "border-2 border-label-3"}`} aria-hidden>
              {on && <Icon name="check" size={15} stroke={3} />}
            </span>
            <span className="sr-only">{on ? "selected" : ""}</span>
          </Row>
        );
      })}
    </>
  );
}

/** Locale picker with a live "1,234.56 · Oct 6, 2026" sample. */
export function LocaleField({ locales, value, onChange }: { locales: string[]; value: string; onChange: (l: string) => void }) {
  return (
    <>
      <Field label="Language & format">
        <Select value={value} onChange={onChange} options={locales.map((l) => ({ value: l, label: localeLabel(l) }))} />
      </Field>
      <Row>
        <span className="flex-1 text-[15px] text-label-2">Sample</span>
        <span className="tabular text-[17px] font-medium">{localeSample(value)}</span>
      </Row>
    </>
  );
}

/** Base currency picker (code · name). */
export function BaseCurrencyField({ supported, value, onChange }: { supported: Currency[]; value: Currency; onChange: (c: Currency) => void }) {
  const options = useMemo(() => supported.map((c) => ({ value: c, label: `${c} · ${currencyName(c)}` })), [supported]);
  return (
    <Field label="Base currency">
      <Select value={value} onChange={onChange} options={options} />
    </Field>
  );
}

const POPULAR = ["USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "TRY"];

/** Multi-toggle chips for extra currencies (the base currency is always on and not shown). */
export function CurrencyChips({ supported, base, value, onChange }: { supported: Currency[]; base: Currency; value: Currency[]; onChange: (c: Currency[]) => void }) {
  const [query, setQuery] = useState("");
  const list = supported
    .filter((c) => c !== base)
    .sort((a, b) => {
      const pa = POPULAR.indexOf(a);
      const pb = POPULAR.indexOf(b);
      return (pa < 0 ? 99 : pa) - (pb < 0 ? 99 : pb) || a.localeCompare(b);
    });
  const q = query.trim().toLowerCase();
  const shown = q ? list.filter((c) => c.toLowerCase().includes(q) || currencyName(c).toLowerCase().includes(q)) : list;
  const toggle = (c: Currency) => onChange(value.includes(c) ? value.filter((x) => x !== c) : [...value, c]);
  return (
    <div className="p-3">
      <div className="mb-3 flex items-center gap-2 rounded-[10px] bg-fill px-3">
        <Icon name="search" size={17} className="text-label-2" />
        <input
          className="h-9 min-w-0 flex-1 bg-transparent text-[17px] outline-none placeholder:text-label-3"
          placeholder="Search currencies"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search currencies"
        />
      </div>
      <div className="flex flex-wrap gap-2">
        {shown.map((c) => {
          const on = value.includes(c);
          return (
            <button
              key={c}
              type="button"
              aria-pressed={on}
              title={currencyName(c)}
              onClick={() => toggle(c)}
              className={`flex h-9 items-center gap-1 rounded-full px-3 text-[15px] font-medium transition-colors active:opacity-70 ${
                on ? "bg-accent text-white" : "bg-fill text-label"
              }`}
            >
              {on && <Icon name="check" size={14} stroke={3} />}
              {c}
            </button>
          );
        })}
        {!shown.length && <span className="px-1 text-[15px] text-label-2">No match</span>}
      </div>
    </div>
  );
}

const CONFIG_PATH = "Home Assistant → Settings → Add-ons → Budget Tracker → Configuration";

/** Status of the optional integrations (document reading, Telegram) with how-to text. */
export function IntegrationStatus({ me }: { me: Me | undefined }) {
  return (
    <>
      <Section title="Document reading" footer={me?.ai_enabled ? "Receipts and card statements are read by your own local model; nothing leaves your network." : `To read receipts and statements automatically, run Ollama with a vision model and enter its address in the ollama_url field (${CONFIG_PATH}), then restart the add-on.`}>
        <Row>
          <span className="flex h-[30px] w-[30px] items-center justify-center rounded-[7px] bg-[#FF9500] text-white">
            <Icon name="camera" size={18} />
          </span>
          <span className="flex-1 text-[17px]">Receipt & statement reading</span>
          <span className={`text-[15px] ${me?.ai_enabled ? "text-green" : "text-orange"}`}>{!me ? "…" : me.ai_enabled ? "On" : "Not set up"}</span>
        </Row>
      </Section>
      <Section title="Telegram" footer={me?.telegram.enabled ? "Link your account under More → Telegram bot to add expenses and get reminders in Telegram." : `Optional. Create a bot with @BotFather on Telegram and paste its token into telegram_bot_token (${CONFIG_PATH}), then restart the add-on.`}>
        <Row>
          <span className="flex h-[30px] w-[30px] items-center justify-center rounded-[7px] bg-[#2AABEE] text-white">
            <Icon name="send" size={18} />
          </span>
          <span className="flex-1 text-[17px]">Telegram bot</span>
          <span className={`text-[15px] ${me?.telegram.enabled ? "text-green" : "text-label-2"}`}>
            {!me ? "…" : me.telegram.enabled ? (me.telegram.bot ? `@${me.telegram.bot}` : "Configured") : "Not configured"}
          </span>
        </Row>
      </Section>
    </>
  );
}

// ---- Settings page ---------------------------------------------------------------------

type Form = Required<Pick<SettingsPatch, "region" | "locale" | "base_currency" | "currencies" | "investments" | "household_payday">>;

function toForm(s: Settings): Form {
  return {
    region: s.region,
    locale: s.locale,
    base_currency: s.base_currency,
    currencies: s.currencies.filter((c) => c !== s.base_currency),
    investments: s.investments,
    household_payday: s.household_payday || 1,
  };
}

const DAYS = Array.from({ length: 28 }, (_, i) => ({ value: i + 1, label: i === 0 ? "1st (calendar month)" : `Day ${i + 1}` }));

export default function SettingsPage() {
  const q = useSettings();
  const me = useMe().data;
  const nav = useNavigate();
  const qc = useQueryClient();
  const save = useSaveSettings();
  const [f, setF] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (q.data && !f) setF(toForm(q.data));
  }, [q.data, f]);

  const back = (
    <TextButton onClick={() => nav("/more")}>
      <Icon name="chevronLeft" size={20} stroke={2.5} /> More
    </TextButton>
  );
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data || !f) {
    return (
      <div>
        <PageHeader title="Settings" left={back} />
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      </div>
    );
  }
  const s = q.data;
  const initial = toForm(s);
  const dirty = JSON.stringify(initial) !== JSON.stringify(f);
  const baseChanged = f.base_currency !== s.base_currency;
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => (x ? { ...x, [k]: v } : x));

  async function submit() {
    if (!f || busy) return;
    if (baseChanged && !confirm(`Switch the base currency from ${s.base_currency} to ${f.base_currency}? Reports, budgets and totals will be re-valued in ${f.base_currency}.`)) return;
    setBusy(true);
    try {
      const next = await save({ ...f, currencies: [f.base_currency, ...f.currencies.filter((c) => c !== f.base_currency)] });
      setF(toForm(next));
      // Amounts, totals and formats all depend on these settings
      qc.invalidateQueries();
      toast("Settings saved");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pb-6">
      <PageHeader title="Settings" left={back} right={<TextButton onClick={submit} disabled={!dirty || busy}>Save</TextButton>} />

      <Section title="Region" footer="Region packs add local extras; everything else works the same everywhere.">
        <RegionChooser regions={s.regions} value={f.region} onChange={(v) => set("region", v)} />
      </Section>

      <Section title="Format" footer="How numbers, amounts and dates are shown in the app.">
        <LocaleField locales={s.locales} value={f.locale} onChange={(v) => set("locale", v)} />
      </Section>

      <Section
        title="Currency"
        footer={
          baseChanged ? undefined : "Reports, budgets, plans and totals are shown in the base currency. Foreign amounts are converted with daily exchange rates."
        }
      >
        <BaseCurrencyField
          supported={s.supported_currencies}
          value={f.base_currency}
          onChange={(c) => setF((x) => (x ? { ...x, base_currency: c, currencies: x.currencies.filter((y) => y !== c) } : x))}
        />
      </Section>
      {baseChanged && (
        <div className="mx-4 mt-2 flex gap-2 rounded-xl bg-orange/15 p-3 text-[13px]">
          <Icon name="warning" size={18} className="text-orange" />
          <span>
            Changing the base currency re-values all reports, budgets and totals in {f.base_currency} using exchange rates. Every transaction keeps its own
            currency and amount; budget limits and plan amounts are not converted, so review them afterwards.
          </span>
        </div>
      )}

      <Section title="Other currencies" footer="These appear in currency pickers when you add transactions, cards and savings.">
        <CurrencyChips supported={s.supported_currencies} base={f.base_currency} value={f.currencies} onChange={(v) => set("currencies", v)} />
      </Section>

      <Section title="Features">
        <Field label="Investments">
          <Toggle checked={f.investments} onChange={(v) => set("investments", v)} />
        </Field>
        <Field label="Report month starts on">
          <Select value={f.household_payday} onChange={(v) => set("household_payday", v)} options={DAYS} />
        </Field>
      </Section>

      <IntegrationStatus me={me} />

      <div className="mx-4 mt-6 space-y-3">
        <PrimaryButton onClick={submit} disabled={!dirty || busy}>
          {busy ? <Spinner className="border-white/40 border-t-white" /> : "Save"}
        </PrimaryButton>
        <PrimaryButton tone="plain" onClick={() => nav("/setup")}>
          Run setup assistant again
        </PrimaryButton>
      </div>
    </div>
  );
}
