import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ErrorState, Field, Icon, PrimaryButton, Row, Section, Spinner, Toggle, toast } from "../components/ui";
import type { Currency } from "../lib/api";
import { currencyName } from "../lib/format";
import { useMe } from "../lib/queries";
import { REGION_INFO, defaultCurrencyFor, guessLocale, localeLabel, localeSample, useSaveSettings, useSettings, type Settings } from "../lib/settings";
import { BaseCurrencyField, CurrencyChips, IntegrationStatus, LocaleField, RegionChooser } from "./Settings";

const STEPS = ["Welcome", "Region & format", "Currency", "Features", "Done"] as const;

interface Draft {
  region: string;
  locale: string;
  base_currency: Currency;
  currencies: Currency[];
  investments: boolean;
}

/** First run: pick locale/region before anything else. A returning user (setup_done) starts from saved values. */
function initialDraft(s: Settings): Draft {
  if (s.setup_done) {
    return { region: s.region, locale: s.locale, base_currency: s.base_currency, currencies: s.currencies.filter((c) => c !== s.base_currency), investments: s.investments };
  }
  const locale = guessLocale(s.locales);
  const base = defaultCurrencyFor(locale);
  const supported = new Set(s.supported_currencies);
  const extras = ["USD", "EUR", "GBP"].filter((c) => c !== base && supported.has(c)).slice(0, 2);
  return {
    region: locale === "tr-TR" ? "tr" : "",
    locale,
    base_currency: supported.has(base) ? base : "USD",
    currencies: extras,
    investments: s.investments,
  };
}

export default function Setup() {
  const q = useSettings();
  const me = useMe().data;
  const nav = useNavigate();
  const qc = useQueryClient();
  const save = useSaveSettings();
  const [step, setStep] = useState(0);
  const [d, setD] = useState<Draft | null>(null);
  // Once the user picks a base currency by hand, locale/region changes stop suggesting one
  const [baseTouched, setBaseTouched] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (q.data && !d) {
      setD(initialDraft(q.data));
      if (q.data.setup_done) setBaseTouched(true);
    }
  }, [q.data, d]);
  useEffect(() => {
    document.getElementById("scroller")?.scrollTo(0, 0);
  }, [step]);

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data || !d) {
    return (
      <div className="flex justify-center py-32">
        <Spinner />
      </div>
    );
  }
  const s = q.data;
  const supported = new Set(s.supported_currencies);

  function suggestBase(next: Draft): Draft {
    if (baseTouched) return next;
    const c = next.region === "tr" ? "TRY" : defaultCurrencyFor(next.locale);
    if (!supported.has(c)) return next;
    return { ...next, base_currency: c, currencies: next.currencies.filter((x) => x !== c) };
  }
  const update = (patch: Partial<Draft>) => setD((x) => (x ? suggestBase({ ...x, ...patch }) : x));

  async function finish() {
    if (!d || busy) return;
    setBusy(true);
    try {
      await save({
        ...d,
        currencies: [d.base_currency, ...d.currencies.filter((c) => c !== d.base_currency)],
        setup_done: true,
      });
      qc.invalidateQueries();
      nav("/", { replace: true });
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const last = step === STEPS.length - 1;
  const regionName = s.regions.find((r) => r.code === d.region)?.name ?? "None";

  return (
    <div className="pt-safe flex min-h-full flex-col">
      {/* Progress */}
      <div className="px-4 pt-4">
        <div className="flex gap-1.5" aria-hidden>
          {STEPS.map((_, i) => (
            <div key={i} className={`h-1 flex-1 rounded-full transition-colors ${i <= step ? "bg-accent" : "bg-fill"}`} />
          ))}
        </div>
        <div className="mt-2 text-[13px] text-label-2">
          Step {step + 1} of {STEPS.length} · {STEPS[step]}
        </div>
      </div>

      <div className="flex-1 pb-6">
        {step === 0 && (
          <>
            <Hero emoji="👋" title="Welcome to Budget" text="A household budget that lives in your Home Assistant. Let's set it up in under a minute." />
            <Section title="What it does">
              <Feature icon="list" color="#007AFF" title="Track spending & income" text="Add expenses in seconds; categories are suggested for you." />
              <Feature icon="card" color="#5E5CE6" title="Credit cards & statements" text="Balances, due dates, installments and payments in any currency." />
              <Feature icon="repeat" color="#FF3B30" title="Recurring bills & income" text="Rent, subscriptions and salary are planned ahead automatically." />
              <Feature icon="target" color="#34C759" title="Budgets, plans & savings" text="See what you can still spend this month and where the money goes." />
              <Feature icon="camera" color="#FF9500" title="Receipt reading (optional)" text="Snap a receipt or upload a statement; a local AI model reads it." />
            </Section>
            <Section title="Privacy">
              <div className="flex gap-3 px-4 py-3">
                <span className="text-[22px]" aria-hidden>
                  🔒
                </span>
                <p className="text-[15px] text-label-2">
                  Your data stays on your Home Assistant server. There is no account and no cloud sync; only exchange rates and (optionally) market prices are
                  fetched from the internet.
                </p>
              </div>
            </Section>
          </>
        )}

        {step === 1 && (
          <>
            <Hero emoji="🌍" title="Region & format" text="Choose how numbers and dates look, and whether to add a local region pack." />
            <Section title="Number & date format">
              <LocaleField locales={s.locales} value={d.locale} onChange={(locale) => update({ locale })} />
            </Section>
            <Section title="Region pack" footer="You can change this later in More → Settings.">
              <RegionChooser regions={s.regions} value={d.region} onChange={(region) => update({ region })} />
            </Section>
          </>
        )}

        {step === 2 && (
          <>
            <Hero emoji="💱" title="Currency" text="Pick the currency you think in. You can still record amounts in any enabled currency." />
            <Section footer="Reports, budgets and totals use the base currency. Other amounts are converted with daily exchange rates.">
              <BaseCurrencyField
                supported={s.supported_currencies}
                value={d.base_currency}
                onChange={(c) => {
                  setBaseTouched(true);
                  setD((x) => (x ? { ...x, base_currency: c, currencies: x.currencies.filter((y) => y !== c) } : x));
                }}
              />
            </Section>
            <Section title="Also use" footer={d.currencies.length ? `Enabled: ${[d.base_currency, ...d.currencies].join(", ")}` : "Only the base currency is enabled."}>
              <CurrencyChips supported={s.supported_currencies} base={d.base_currency} value={d.currencies} onChange={(currencies) => setD((x) => (x ? { ...x, currencies } : x))} />
            </Section>
          </>
        )}

        {step === 3 && (
          <>
            <Hero emoji="🧩" title="Features" text="Turn on what you need. Optional integrations are configured in the add-on settings." />
            <Section footer="Track gold, currencies, stocks and funds alongside your budget.">
              <Field label="Investments">
                <Toggle checked={d.investments} onChange={(investments) => setD((x) => (x ? { ...x, investments } : x))} />
              </Field>
            </Section>
            <IntegrationStatus me={me} />
          </>
        )}

        {step === 4 && (
          <>
            <Hero emoji="🎉" title="All set" text="Here is your setup. You can change any of it later in More → Settings." />
            <Section>
              <Summary label="Format" value={localeLabel(d.locale)} />
              <Summary label="Sample" value={localeSample(d.locale)} />
              <Summary label="Region pack" value={d.region ? regionName : "None"} />
              <Summary label="Base currency" value={`${d.base_currency} · ${currencyName(d.base_currency)}`} />
              <Summary label="Other currencies" value={d.currencies.join(", ") || "—"} />
              <Summary label="Investments" value={d.investments ? "On" : "Off"} />
            </Section>
            {d.region && REGION_INFO[d.region] && <p className="mx-8 mt-2 text-[13px] text-label-2">{REGION_INFO[d.region]}</p>}
          </>
        )}
      </div>

      {/* Navigation */}
      <div className="pb-safe sticky bottom-0 z-10 border-t border-sep" style={{ background: "var(--bg)" }}>
        <div className="mx-auto flex max-w-lg gap-3 px-4 py-3">
          {step > 0 && (
            <div className="w-1/3">
              <PrimaryButton tone="plain" onClick={() => setStep(step - 1)} disabled={busy}>
                Back
              </PrimaryButton>
            </div>
          )}
          <div className="flex-1">
            <PrimaryButton onClick={() => (last ? finish() : setStep(step + 1))} disabled={busy}>
              {busy ? <Spinner className="border-white/40 border-t-white" /> : last ? "Start using Budget" : step === 0 ? "Get started" : "Continue"}
            </PrimaryButton>
          </div>
        </div>
      </div>
    </div>
  );
}

function Hero({ emoji, title, text }: { emoji: string; title: string; text: string }) {
  return (
    <div className="px-6 pt-8 text-center">
      <div className="text-[52px] leading-none" aria-hidden>
        {emoji}
      </div>
      <h1 className="mt-3 text-[28px] font-bold leading-tight tracking-tight">{title}</h1>
      <p className="mx-auto mt-2 max-w-sm text-[15px] text-label-2">{text}</p>
    </div>
  );
}

function Feature({ icon, color, title, text }: { icon: string; color: string; title: string; text: ReactNode }) {
  return (
    <Row>
      <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[7px] text-white" style={{ background: color }}>
        <Icon name={icon} size={18} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[17px]">{title}</div>
        <div className="text-[13px] text-label-2">{text}</div>
      </div>
    </Row>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <Row>
      <span className="flex-1 text-[17px]">{label}</span>
      <span className="truncate text-right text-[15px] text-label-2">{value}</span>
    </Row>
  );
}
