import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Currency } from "../lib/api";
import { BASE, money, monthLabel, shortDate, thisMonth, today } from "../lib/format";
import { useSettings } from "../lib/settings";
import { reportPeriod, transactionLink, type ReportTab, type Workspace } from "../lib/reporting";
import { useLookups } from "../lib/queries";
import {
  Empty,
  ErrorState,
  Field,
  FxWarning,
  PageHeader,
  Row,
  Section,
  Segmented,
  Select,
  Spinner,
  TextButton,
  dateCls,
} from "../components/ui";
import CashflowSection from "../components/Cashflow";

const tick = { fill: "var(--label-2)", fontSize: 11 };
const compact = (n: number) => (Math.abs(n) >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
const tooltip = { contentStyle: { background: "var(--card)", color: "var(--label)", border: "1px solid var(--sep)", borderRadius: 12 } };
const names: Record<string, string> = {
  income: "Income",
  expense: "Expenses",
  fixed: "Fixed",
  variable: "Variable",
  one_off: "One-off",
  current: "Selected month",
  previous: "Previous month",
  total: "Expenses",
  average: "Average per day",
};
const colors: Record<string, string> = {
  income: "var(--green)",
  expense: "var(--accent)",
  fixed: "var(--accent)",
  variable: "var(--orange)",
  one_off: "#AF52DE",
};

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-card p-3">
      <div className="text-[12px] text-label-2">{label}</div>
      <div className="tabular break-words text-[19px] font-semibold">{value}</div>
      {sub && <div className="text-[12px] text-label-2">{sub}</div>}
    </div>
  );
}
function Legend({ keys }: { keys: string[] }) {
  return (
    <div className="flex flex-wrap gap-3 px-4 pt-3 text-[12px]">
      {keys.map((k) => (
        <span key={k} className="flex items-center gap-1">
          <span className="h-2 w-3 rounded" style={{ background: colors[k] }} />
          {names[k]}
        </span>
      ))}
    </div>
  );
}

export default function Reports() {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const { cards, me, cardById, userById } = useLookups();
  const set = (key: string, value: string) => {
    setParams(
      (p) => {
        const n = new URLSearchParams(p);
        if (value) n.set(key, value);
        else n.delete(key);
        return n;
      },
      { replace: true },
    );
  };
  // Household-wide period start day (payday); remembered once selected
  const settings = useSettings();
  const tab = (params.get("tab") || "summary") as ReportTab;
  const range = params.get("range") || "1",
    month = params.get("month") || thisMonth(),
    payday = Number(params.get("payday") || settings.data?.household_payday || 1);
  const period = reportPeriod(month, Number(range) || 1, payday);
  const start = params.get("start") || period.start,
    end = params.get("end") || period.end;
  const fq = new URLSearchParams();
  ["user_id", "card_id", "currency"].forEach((k) => {
    if (params.get(k)) fq.set(k, params.get(k)!);
  });
  const filters = fq.toString();
  const valid = !!start && !!end && start <= end && start <= today();
  const q = useQuery({
    queryKey: ["report-workspace", start, end, filters],
    queryFn: () => api.get<Workspace>(`reports/workspace?start=${start}&end=${end}&${filters}`),
    enabled: valid,
  });
  const s = q.data;
  const [trendCategory, setTrendCategory] = useState(0);
  const detail = (extra: Record<string, string | number> = {}, from = start, to = end) => nav(transactionLink(from, to, filters, extra));
  function changePeriod(key: string, value: string) {
    setParams(
      (p) => {
        const n = new URLSearchParams(p);
        n.set(key, value);
        n.delete("start");
        n.delete("end");
        return n;
      },
      { replace: true },
    );
  }
  return (
    <div>
      <PageHeader title="Reports" left={<TextButton onClick={() => nav("/more")}>‹ More</TextButton>} />
      <div className="mx-4 mt-3">
        <Segmented
          value={tab}
          onChange={(v) => set("tab", v)}
          options={[
            { value: "summary", label: "Summary" },
            { value: "analysis", label: "Spending analysis" },
            { value: "plan", label: "Payment plan" },
          ]}
        />
      </div>
      <Section title="Period and filters">
        {tab !== "plan" && (
          <>
            <Field label="Last month">
              <input
                aria-label="Report month"
                type="month"
                className={dateCls}
                value={month}
                max={thisMonth()}
                onChange={(e) => changePeriod("month", e.target.value)}
              />
            </Field>
            <div className="px-4 py-2">
              <Segmented
                value={range}
                onChange={(v) => changePeriod("range", v)}
                options={[
                  { value: "1", label: "1 mo" },
                  { value: "3", label: "3 mo" },
                  { value: "6", label: "6 mo" },
                  { value: "12", label: "12 mo" },
                ]}
              />
            </div>
            <Field label="Period start (household)">
              <Select
                value={payday}
                onChange={(v) => {
                  changePeriod("payday", String(v));
                  api.put("settings", { household_payday: v }).then(() => settings.refetch()).catch(() => undefined);
                }}
                options={Array.from({ length: 28 }, (_, i) => ({ value: i + 1, label: i === 0 ? "Calendar month" : `Payday: ${i + 1}` }))}
              />
            </Field>
            <Field label="Start">
              <input type="date" className={dateCls} value={start} max={end} onChange={(e) => set("start", e.target.value)} />
            </Field>
            <Field label="End">
              <input type="date" className={dateCls} value={end} min={start} max={today()} onChange={(e) => set("end", e.target.value)} />
            </Field>
          </>
        )}
        <Field label="Person">
          <Select
            value={Number(params.get("user_id") || 0)}
            onChange={(v) => set("user_id", v ? String(v) : "")}
            options={[{ value: 0, label: "Everyone" }, ...(me?.users ?? []).map((u) => ({ value: u.id, label: u.name }))]}
          />
        </Field>
        <Field label="Card">
          <Select
            value={Number(params.get("card_id") || 0)}
            onChange={(v) => set("card_id", v ? String(v) : "")}
            options={[{ value: 0, label: "All methods" }, ...cards.map((c) => ({ value: c.id, label: c.name }))]}
          />
        </Field>
        <Field label="Transaction currency">
          <Select
            value={params.get("currency") || ""}
            onChange={(v) => set("currency", v)}
            options={[{ value: "", label: `All · ${BASE} equivalent` }, ...(settings.data?.currencies ?? [BASE]).map((value) => ({ value, label: value }))]}
          />
        </Field>
      </Section>
      {tab === "plan" ? (
        <>
          <p className="mx-5 mt-3 text-[13px] text-label-2">
            Expected cash movements from today on. The person filter uses the card owner and the person assigned to each recurring payment.
          </p>
          <CashflowSection filters={filters} />
        </>
      ) : !valid ? (
        <Empty icon="warning" title="Select a valid date range" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !s ? (
        <div className="p-10 text-center">
          <Spinner />
        </div>
      ) : (
        <>
          <p className="mx-5 mt-3 text-[13px] text-label-2">
            {s.start} – {s.end} · comparison {s.comparison_start} – {s.comparison_end}
          </p>
          <FxWarning missing={s.fx_missing} />
          <FxWarning missing={s.comparison_fx_missing} />
          <Section title="Data status">
            <Row onClick={() => nav("/documents")} chevron>
              <span className="flex-1">Documents awaiting review (household)</span>
              <b>{s.quality.review_documents}</b>
            </Row>
            <Row onClick={() => detail({ category: 0 })} chevron>
              <span className="flex-1">Uncategorized</span>
              <span>
                {s.quality.uncategorized_count} transactions · {money(s.quality.uncategorized_amount)}
              </span>
            </Row>
            {s.quality.recorded_since && (
              <p className="px-4 py-2 text-[12px] text-label-2">
                First record: {s.quality.recorded_since}. Days without records are assumed to have no spending.
              </p>
            )}
          </Section>
          {tab === "summary" && (
            <>
              <div className="mx-4 mt-4 grid grid-cols-2 gap-3">
                <Tile label="Expenses" value={money(s.expense)} />
                <Tile label="Income" value={money(s.income)} />
                <Tile
                  label="Income − expenses"
                  value={money(s.net)}
                  sub={
                    s.surplus_rate !== null
                      ? `${Math.round(s.surplus_rate * 100)}% of income`
                      : s.expected_rate !== null && s.expected_rate !== undefined
                        ? `${s.rate_reason ?? "Rate unavailable"}; ~${Math.round(s.expected_rate * 100)}% with expected ${money(s.planned_income ?? 0, BASE, true)} salary/income`
                        : s.rate_reason ?? "Rate unavailable"
                  }
                />
                <Tile label="Actual savings" value="In accounts" sub="Net difference is not a bank balance" />
              </div>
              <div className="mx-4">
                <TextButton onClick={() => nav("/savings")}>Open savings accounts and goals →</TextButton>
              </div>
              {!!s.insights.length && (
                <Section title="Highlights this period">
                  {s.insights.map((x, i) => (
                    <Row key={i}>
                      <p className="text-[14px]">{x}</p>
                    </Row>
                  ))}
                </Section>
              )}
              <Section title={`Income and expenses · ${BASE} equivalent`} footer="Tap a month below to review its records.">
                <Legend keys={["income", "expense"]} />
                <Bars data={s.series} keys={["income", "expense"]} />
                <div className="flex flex-wrap gap-2 p-3">
                  {s.series.map((m) => (
                    <button
                      className="rounded-lg bg-fill px-3 py-2 text-[13px]"
                      key={m.month}
                      onClick={() => {
                        const p = reportPeriod(m.month, 1);
                        detail({}, p.start < start ? start : p.start, p.end > end ? end : p.end);
                      }}
                    >
                      {monthLabel(m.month, true)}
                    </button>
                  ))}
                </div>
              </Section>
              <Section title={`${s.ytd.year} year to date (through selected end)`}>
                <FxWarning missing={s.ytd.fx_missing} />
                <div className="grid grid-cols-2 gap-2 p-3">
                  <Tile label="Income" value={money(s.ytd.income)} />
                  <Tile label="Expenses" value={money(s.ytd.expense)} />
                </div>
              </Section>
              <Pace month={end.slice(0, 7)} filters={filters} />
              <Section
                title={`${monthLabel(s.budget_month)} budget view`}
                footer="Solid: actual, hatched: remaining recurring payments. The limit is the household limit; selected filters apply to spending and planned amounts. Future variable spending is not included in the hatched amount."
              >
                <FxWarning missing={s.budget_fx_missing} />
                {s.budgets.map((b) => {
                  const max = Math.max(b.limit, b.spent + b.planned, 1);
                  return (
                    <Row
                      key={b.category_id}
                      onClick={() => detail({ category: b.category_id }, `${s.budget_month}-01`, reportPeriod(s.budget_month, 1).end)}
                    >
                      <div className="w-full">
                        <div className="flex justify-between gap-2 text-[14px]">
                          <span>{b.name}</span>
                          <span>
                            {money(b.spent, BASE, true)} + {money(b.planned, BASE, true)} / {money(b.limit, BASE, true)}
                          </span>
                        </div>
                        <div className="relative my-2 flex h-3 rounded bg-fill">
                          <div className="h-full rounded-l bg-accent" style={{ width: `${(100 * b.spent) / max}%` }} />
                          <div
                            className="h-full"
                            style={{
                              width: `${(100 * b.planned) / max}%`,
                              background: "repeating-linear-gradient(45deg,var(--accent) 0 2px,transparent 2px 5px)",
                            }}
                          />
                          <div
                            className="absolute -top-1 h-5 border-r-2 border-label"
                            style={{ left: `${Math.min(99.5, (100 * b.limit) / max)}%` }}
                          />
                        </div>
                        {b.spent + b.planned > b.limit && (
                          <p className="text-[12px] text-red">Over limit: {money(b.spent + b.planned - b.limit)}</p>
                        )}
                      </div>
                    </Row>
                  );
                })}
                {!s.budgets.length && (
                  <Empty icon="target" title="No budget for this month" text="Set category limits on the Budgets screen." />
                )}
              </Section>
            </>
          )}
          {tab === "analysis" && (
            <>
              <div className="mx-4 mt-4 grid grid-cols-3 gap-2">
                <Tile label="Expense count" value={String(s.count)} />
                <Tile label="Daily average" value={money(s.daily_avg, BASE, true)} />
                <Tile label="Median purchase" value={money(s.median, BASE, true)} />
                <Tile label="Average purchase" value={money(s.average_ticket, BASE, true)} />
                <Tile label="Calendar month average" value={money(s.monthly_average, BASE, true)} sub="Includes partial and zero months" />
              </div>
              <Section title="Expense breakdown">
                <Legend keys={["fixed", "variable", "one_off"]} />
                <Bars data={s.series} keys={["fixed", "variable", "one_off"]} stacked />
                <div className="flex flex-wrap gap-2 p-3">
                  {["fixed", "variable", "one_off"].map((k) => (
                    <button
                      key={k}
                      className="rounded-lg bg-fill px-3 py-2 text-[13px]"
                      onClick={() => detail({ spending_type: k, kind: "expense" })}
                    >
                      {names[k]} records
                    </button>
                  ))}
                </div>
              </Section>
              <Section
                title="Categories and drivers of change"
                footer="Equal-length period comparison. Tap a category to open its transactions for the selected period."
              >
                {s.changes.map((c) => (
                  <Row key={c.category_id ?? 0} chevron onClick={() => detail({ category: c.category_id ?? 0, kind: "expense" })}>
                    <div className="min-w-0 flex-1">
                      <div className="flex justify-between gap-2">
                        <span>{c.name}</span>
                        <b className="tabular text-[14px]">{money(c.current, BASE, true)}</b>
                      </div>
                      <div className="my-1 h-1.5 rounded bg-fill">
                        <div
                          className="h-full rounded bg-accent"
                          style={{ width: `${s.expense ? Math.min(100, (c.current / s.expense) * 100) : 0}%` }}
                        />
                      </div>
                      <div className="text-[12px] text-label-2">
                        Share {s.expense ? Math.round((c.current / s.expense) * 100) : 0}% · previous {money(c.previous, BASE, true)} · change{" "}
                        {c.delta >= 0 ? "+" : ""}
                        {money(c.delta, BASE, true)}
                      </div>
                      <div className="text-[12px] text-label-2">
                        Transactions {c.previous_count} → {c.count} · average {money(c.previous_average, BASE, true)} →{" "}
                        {money(c.average, BASE, true)}
                      </div>
                    </div>
                  </Row>
                ))}
                {!s.changes.length && <Empty icon="chart" title="No records" />}
              </Section>
              <Section title="12-month category trend" footer="The average covers 12 calendar months, including months with zero spending.">
                <Field label="Category">
                  <Select
                    value={trendCategory}
                    onChange={setTrendCategory}
                    options={[
                      { value: 0, label: "Uncategorized" },
                      ...s.changes.filter((c) => c.category_id).map((c) => ({ value: c.category_id!, label: c.name })),
                    ]}
                  />
                </Field>
                <CategoryTrend
                  category={trendCategory}
                  month={end.slice(0, 7)}
                  filters={filters}
                  onMonth={(m) => {
                    const p = reportPeriod(m, 1);
                    detail({ category: trendCategory, kind: "expense" }, p.start, p.end);
                  }}
                />
              </Section>
              <Section title="Income categories">
                {s.income_categories.map((c) => (
                  <Row key={c.category_id ?? 0} chevron onClick={() => detail({ category: c.category_id ?? 0, kind: "income" })}>
                    <span className="flex-1">{c.name}</span>
                    <span>{money(c.total)}</span>
                  </Row>
                ))}
              </Section>
              <Calendar days={s.calendar} onDay={(day) => detail({}, day, day)} />
              <Section title="By day of week" footer="Each day's total is divided by how many of that weekday fall in the selected period.">
                <div className="h-44 p-2">
                  <ResponsiveContainer>
                    <BarChart data={s.weekday}>
                      <XAxis dataKey="day" tick={tick} />
                      <YAxis tickFormatter={compact} tick={tick} width={45} />
                      <Tooltip {...tooltip} formatter={(v: number) => [money(v), "Average per day"]} />
                      <Bar dataKey="average" fill="var(--accent)" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Section>
              <Section title="Top merchants">
                {s.merchants.map((m) => (
                  <Row key={m.name} chevron onClick={() => detail({ merchant_key: m.name, kind: "expense" })}>
                    <div className="min-w-0 flex-1">
                      <div>{m.display}</div>
                      <div className="text-[12px] text-label-2">{m.count} transactions</div>
                    </div>
                    <b>{money(m.total, BASE, true)}</b>
                  </Row>
                ))}
              </Section>
              <Section title="Largest expenses">
                {s.largest.map((t) => (
                  <Row key={t.id} chevron onClick={() => detail({ q: t.merchant }, t.date, t.date)}>
                    <div className="flex-1">
                      {t.merchant || "Transaction"}
                      <div className="text-[12px] text-label-2">{shortDate(t.date)}</div>
                    </div>
                    <span>{money(t.amount, t.currency)}</span>
                  </Row>
                ))}
              </Section>
              <Section title="Payment methods">
                {s.by_method.map((x) => (
                  <Row key={x.key}>
                    <span className="flex-1">{{ card: "Credit card", cash: "Cash", bank: "Bank", voucher: "Voucher" }[x.key] ?? x.key}</span>
                    <span>{money(x.total)}</span>
                  </Row>
                ))}
              </Section>
              <Section title="By card">
                {s.by_card
                  .filter((x) => x.key)
                  .map((x) => (
                    <Row key={x.key} chevron onClick={() => detail({ card_id: x.key! })}>
                      <span className="flex-1">{cardById.get(x.key!)?.name ?? "Card"}</span>
                      <span>{money(x.total)}</span>
                    </Row>
                  ))}
              </Section>
              <Section title="By person">
                {s.by_user.map((x) => (
                  <Row key={x.key ?? 0}>
                    <span className="flex-1">{x.key ? userById.get(x.key)?.name : "Shared"}</span>
                    <span>{money(x.total)}</span>
                  </Row>
                ))}
              </Section>
              <Section
                title="Active subscriptions"
                footer="Recurring payments in the Subscriptions category. The annual figure assumes 52 weekly or 12 monthly payments; it is not actual spending."
              >
                {s.subscriptions.map((x) => (
                  <Row key={x.id} chevron onClick={() => nav("/recurring/expenses")}>
                    <div className="w-full">
                      <div className="flex justify-between">
                        <b>{x.name}</b>
                        <span>{money(x.amount, x.currency)}</span>
                      </div>
                      <div className="text-[13px] text-label-2">
                        Monthly equivalent {money(x.monthly, x.currency)} · annual {money(x.annual, x.currency)}
                      </div>
                      {x.previous !== null && <p className="text-[13px]">Previous amount {money(x.previous, x.currency)}</p>}
                      {x.history.length > 1 && (
                        <div className="h-20">
                          <ResponsiveContainer>
                            <LineChart data={x.history}>
                              <Tooltip
                                {...tooltip}
                                labelFormatter={(_, p) => p[0]?.payload.date}
                                formatter={(v: number) => [money(v, x.currency), "Amount"]}
                              />
                              <Line dataKey="amount" stroke="var(--accent)" dot={false} />
                            </LineChart>
                          </ResponsiveContainer>
                        </div>
                      )}
                    </div>
                  </Row>
                ))}
                {!s.subscriptions.length && <Empty icon="repeat" title="No subscriptions recorded" />}
              </Section>
            </>
          )}
        </>
      )}
    </div>
  );
}

function Bars({ data, keys, stacked = false }: { data: Workspace["series"]; keys: string[]; stacked?: boolean }) {
  return (
    <div className="h-56 p-2">
      <ResponsiveContainer>
        <BarChart data={data}>
          <CartesianGrid vertical={false} stroke="var(--sep)" />
          <XAxis dataKey="month" tickFormatter={(v) => monthLabel(v, true)} tick={tick} />
          <YAxis tickFormatter={compact} width={50} tick={tick} />
          <Tooltip {...tooltip} formatter={(v: number, name: string) => [money(v), names[name]]} />
          {keys.map((k) => (
            <Bar
              key={k}
              dataKey={k}
              fill={colors[k]}
              stackId={stacked ? "expenses" : undefined}
              radius={stacked ? undefined : [3, 3, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
interface Daily {
  days: { day: number; current: number | null; previous: number | null }[];
  current_total: number;
  previous_same_day: number;
  projection: number | null;
  projection_low: number | null;
  projection_high: number | null;
  projection_basis: string;
  fx_missing: { currency: Currency; amount: number }[];
}
function Pace({ month, filters }: { month: string; filters: string }) {
  const q = useQuery({ queryKey: ["daily", month, filters], queryFn: () => api.get<Daily>(`reports/daily?month=${month}&${filters}`) });
  const d = q.data;
  return (
    <Section
      title={`${monthLabel(month)} · month-to-date pace`}
      footer="Cumulative spending since the start of the month. The dashed line is the previous month. The forecast range reflects past weeks' distribution; it is not a guarantee."
    >
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !d ? (
        <div className="p-8">
          <Spinner />
        </div>
      ) : (
        <>
          <FxWarning missing={d.fx_missing} />
          <div className="grid grid-cols-2 gap-2 p-3">
            <Tile label="Actual" value={money(d.current_total, BASE, true)} />
            <Tile label="Previous month, same day" value={money(d.previous_same_day, BASE, true)} />
          </div>
          <div className="h-52 p-2">
            <ResponsiveContainer>
              <LineChart data={d.days}>
                <CartesianGrid stroke="var(--sep)" vertical={false} />
                <XAxis dataKey="day" tick={tick} />
                <YAxis tickFormatter={compact} tick={tick} width={45} />
                <Tooltip {...tooltip} formatter={(v: number, name: string) => [money(v), names[name]]} />
                <Line dataKey="current" stroke="var(--accent)" strokeWidth={2} dot={false} />
                <Line dataKey="previous" stroke="var(--label-2)" strokeDasharray="4 4" dot={false} />
                {d.projection !== null && !d.fx_missing.length && (
                  <ReferenceLine y={d.projection} stroke="var(--orange)" strokeDasharray="3 3" />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
          {d.projection !== null && !d.fx_missing.length && (
            <div className="p-4 text-[14px]">
              <b>Month-end forecast {money(d.projection)}</b>
              {d.projection_low !== null && (
                <p>
                  Range: {money(d.projection_low)} – {money(d.projection_high ?? d.projection)}
                </p>
              )}
              <p className="text-[12px] text-label-2">
                {d.projection_basis} + remaining recurring payments. One-off expenses are only in the actual total.
              </p>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
function Calendar({ days, onDay }: { days: Workspace["calendar"]; onDay: (day: string) => void }) {
  const months = [...new Set(days.map((d) => d.date.slice(0, 7)))];
  const [selected, setSelected] = useState("");
  const month = months.includes(selected) ? selected : (months.at(-1) ?? "");
  const shown = days.filter((d) => d.date.startsWith(month));
  const max = Math.max(1, ...shown.map((d) => d.total));
  const pad = shown.length ? (new Date(`${shown[0].date}T12:00:00`).getDay() + 6) % 7 : 0;
  return (
    <Section title="Spending calendar" footer="Tap a day to open its transactions. 0 means no records.">
      <Field label="Month">
        <Select value={month} onChange={setSelected} options={months.map((value) => ({ value, label: monthLabel(value) }))} />
      </Field>
      <div className="grid grid-cols-7 gap-1 p-3">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
          <span key={d} className="text-center text-[11px] text-label-2">
            {d}
          </span>
        ))}
        {Array.from({ length: pad }, (_, i) => (
          <span key={`pad-${i}`} />
        ))}
        {shown.map((d) => (
          <button
            key={d.date}
            aria-label={`${d.date}: ${money(d.total)}, ${d.count} transactions`}
            onClick={() => onDay(d.date)}
            className="min-h-12 rounded-lg px-0.5 py-2 text-center"
            style={{
              background: d.total ? `color-mix(in srgb, var(--accent) ${15 + (50 * d.total) / max}%, var(--card))` : "var(--card-2)",
            }}
          >
            <span className="block text-[13px]">{Number(d.date.slice(-2))}</span>
            <span className="block text-[9px]">{compact(d.total)}</span>
          </button>
        ))}
      </div>
    </Section>
  );
}

function CategoryTrend({
  category,
  month,
  filters,
  onMonth,
}: {
  category: number;
  month: string;
  filters: string;
  onMonth: (month: string) => void;
}) {
  const q = useQuery({
    queryKey: ["cat-trend", category, month, filters],
    queryFn: () =>
      api.get<{ series: { month: string; total: number }[]; fx_missing: { currency: Currency; amount: number }[] }>(
        `reports/category-trend?with_meta=true&category_id=${category}&end=${month}&months=12&${filters}`,
      ),
  });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data)
    return (
      <div className="p-8">
        <Spinner />
      </div>
    );
  const rows = q.data.series;
  const avg = rows.reduce((sum, row) => sum + row.total, 0) / Math.max(1, rows.length);
  return (
    <>
      <FxWarning missing={q.data.fx_missing} />
      <p className="px-4 py-2 text-[13px]">Monthly average: {money(avg)}</p>
      <div className="h-44 p-2">
        <ResponsiveContainer>
          <BarChart data={rows}>
            <XAxis dataKey="month" tickFormatter={(v) => monthLabel(v, true)} tick={tick} interval={2} />
            <YAxis tickFormatter={compact} tick={tick} width={45} />
            <Tooltip {...tooltip} formatter={(v: number) => [money(v), "Expenses"]} />
            <Bar dataKey="total" fill="var(--accent)" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-wrap gap-2 p-3">
        {rows.map((r) => (
          <button key={r.month} onClick={() => onMonth(r.month)} className="rounded-lg bg-fill px-2 py-2 text-[12px]">
            {monthLabel(r.month, true)} · {money(r.total, BASE, true)}
          </button>
        ))}
      </div>
    </>
  );
}
