import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ErrorState, Field, Icon, PageHeader, PrimaryButton, Row, Section, Sheet, Spinner, TextButton, inputCls, toast } from "../components/ui";
import { api } from "../lib/api";
import { BASE, amountInput, money, monthLabel, parseAmount } from "../lib/format";
import { useInvalidate } from "../lib/queries";

interface PlanMonth {
  month: string;
  income: number;
  fixed: number;
  debt: number;
  installments: number;
  card_backlog: number;
  variable: number;
  variable_actual: number | null;
  buffer: number;
  savings: number;
  release: number;
  free: number;
  net: number;
  balance: number;
  saved: number;
  debt_left: number;
  top_items: { name: string; amount: number }[];
}
interface PlanData {
  months: PlanMonth[];
  debts: { name: string; kind: string; remaining: number; end: string }[];
  debt_total: number;
  debt_free: string | null;
  goals: { id: number; name: string; target: number; remaining: number; target_month: string | null; planned: number; monthly: number; auto: boolean; shortfall: number; plan: { month: string; amount: number }[] }[];
  settings: { start_balance: number; variable: number; buffer: number; variable_auto: number; variable_source: string };
  tight_months: string[];
  hobby_spent: { month: string; amount: number }[];
}

// Fixed order: color is tied to the category, not the sort order
const SERIES = [
  { key: "fixed", label: "Fixed expenses", color: "var(--viz-1)" },
  { key: "debt", label: "Debt payments", color: "var(--viz-2)" },
  { key: "variable", label: "Spending", color: "var(--viz-3)" },
  { key: "savings", label: "Savings", color: "var(--viz-5)" },
  { key: "free", label: "Free", color: "var(--viz-6)" },
] as const;

const tooltipStyle = {
  contentStyle: { background: "var(--card)", border: "none", borderRadius: 12, boxShadow: "0 4px 16px rgba(0,0,0,.15)", fontSize: 13 },
  labelStyle: { color: "var(--label)", fontWeight: 600 },
  itemStyle: { color: "var(--label)" },
};
const axisTick = { fill: "var(--label-2)", fontSize: 11 };

function compact(n: number) {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1000) return `${Math.round(n / 1000)}k`;
  return String(Math.round(n));
}
const short = (ym: string) => monthLabel(ym, true);
// Legend text uses the text color; color only on the marker
const legendText = (v: string) => <span style={{ color: "var(--label)" }}>{v}</span>;

export default function PlanPage() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["plan"], queryFn: () => api.get<PlanData>("reports/plan") });
  const [selected, setSelected] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [goalOpen, setGoalOpen] = useState(false);
  const p = q.data;

  return (
    <div>
      <PageHeader
        title="Plan"
        left={
          <TextButton onClick={() => nav("/more")}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> More
          </TextButton>
        }
        right={<TextButton onClick={() => setSettingsOpen(true)}>Settings</TextButton>}
      />
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !p ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : (
        <PlanBody p={p} selected={selected ?? p.months[0].month} setSelected={setSelected} onGoal={() => setGoalOpen(true)} onSettings={() => setSettingsOpen(true)} />
      )}
      {settingsOpen && p && <SettingsSheet s={p.settings} onClose={() => setSettingsOpen(false)} />}
      {goalOpen && <GoalSheet onClose={() => setGoalOpen(false)} />}
    </div>
  );
}

function PlanBody({ p, selected, setSelected, onGoal, onSettings }: { p: PlanData; selected: string; setSelected: (m: string) => void; onGoal: () => void; onSettings: () => void }) {
  const months = p.months;
  const sel = months.find((m) => m.month === selected) ?? months[0];
  const worst = months.reduce((a, b) => (b.net < a.net ? b : a), months[0]);
  const deficits = months.filter((m) => m.net < 0);
  const negatives = months.filter((m) => m.balance < 0);
  const totalSaved = months.reduce((s, m) => s + m.savings, 0);
  const hobbyNow = p.hobby_spent.find((h) => h.month === months[0].month)?.amount ?? 0;
  const hobbyPrev = p.hobby_spent.filter((h) => h.month < months[0].month);
  const hobbyAvg = hobbyPrev.length ? hobbyPrev.reduce((s, h) => s + h.amount, 0) / hobbyPrev.length : 0;
  // The buffer is shown together with fixed expenses
  const bars = months.map((m) => ({ ...m, fixed: m.fixed + m.buffer, label: short(m.month), deficit: m.net < 0 ? m.net : 0 }));

  return (
    <>
      {/* Summary */}
      <div className="mx-4 mt-4 grid grid-cols-2 gap-3">
        <Tile label="Total debt" value={money(p.debt_total, BASE, true)} sub={p.debt_free ? `ends ${monthLabel(p.debt_free)}` : undefined} />
        <Tile label={`Savings over ${months.length} months`} value={money(totalSaved, BASE, true)} sub={p.goals.length ? `${p.goals.length} goals` : "no goals"} />
        <Tile
          label="Hobbies (this month)"
          value={money(hobbyNow, BASE, true)}
          sub={hobbyAvg ? `avg. ${money(hobbyAvg, BASE, true)}/mo` : "items marked 'Hobbies' on statements"}
        />
        <Tile
          label="Tightest month"
          value={money(worst.net, BASE, true)}
          sub={monthLabel(worst.month)}
          tone={worst.net < 0 ? "neg" : undefined}
        />
      </div>
      {(deficits.length > 0 || negatives.length > 0) && (
        <div className="mx-4 mt-3 flex gap-2 rounded-xl bg-orange/15 p-3 text-[13px]">
          <Icon name="warning" size={18} className="shrink-0 text-orange" />
          <span>
            {deficits.length > 0 && <>Months where income falls short: <b>{deficits.map((m) => short(m.month)).join(", ")}</b>. </>}
            {negatives.length > 0 && <>Balance goes negative: {short(negatives[0].month)}–{short(negatives[negatives.length - 1].month)}. </>}
            {p.settings.start_balance === 0 ? (
              <button type="button" className="inline text-left text-[13px] font-semibold text-accent" onClick={onSettings}>
                Enter the money in your bank as the starting balance
              </button>
            ) : (
              "Lower the spending estimate, reduce savings in these months, or set bonuses aside for them."
            )}
          </span>
        </div>
      )}

      {/* Where income goes */}
      <Section title="Where income goes" footer="Segments show where income goes; green: remaining free money. Red below zero: the shortfall. Tap a bar for that month's details.">
        <div className="h-64 px-1 pt-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bars} stackOffset="sign" margin={{ top: 4, right: 8, left: -6, bottom: 0 }} onClick={(e) => e?.activeLabel && setSelected(bars.find((b) => b.label === e.activeLabel)?.month ?? selected)}>
              <CartesianGrid vertical={false} stroke="var(--sep)" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={axisTick} interval={1} />
              <YAxis tickLine={false} axisLine={false} tick={axisTick} tickFormatter={compact} width={48} />
              <ReferenceLine y={0} stroke="var(--label-3)" />
              <Tooltip {...tooltipStyle} cursor={{ fill: "var(--fill)" }} formatter={(v: number, n: string) => [money(v, BASE, true), n]} />
              <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11, paddingTop: 4 }} formatter={legendText} />
              {SERIES.map((s) => (
                <Bar key={s.key} dataKey={s.key} name={s.label} stackId="a" fill={s.color} stroke="var(--card)" strokeWidth={2} maxBarSize={22} />
              ))}
              <Bar dataKey="deficit" name="Shortfall" stackId="a" fill="var(--viz-neg)" stroke="var(--card)" strokeWidth={2} maxBarSize={22} radius={[0, 0, 4, 4]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Section>

      {/* Selected month: pie */}
      <Section title={`${monthLabel(sel.month)} breakdown`}>
        <MonthChips months={months} selected={sel.month} onSelect={setSelected} />
        <MonthDonut m={sel} />
      </Section>

      {/* Cumulative balance and savings */}
      <Section
        title="Cumulative balance"
        footer={`Start ${money(p.settings.start_balance, BASE, true)} + each month's free amount. Savings: total set aside for goals (used on the target date).`}
      >
        <div className="h-52 px-1 pt-3">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={bars} margin={{ top: 4, right: 8, left: -6, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--sep)" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={axisTick} interval={1} />
              <YAxis tickLine={false} axisLine={false} tick={axisTick} tickFormatter={compact} width={48} />
              <ReferenceLine y={0} stroke="var(--label-3)" />
              <Tooltip {...tooltipStyle} formatter={(v: number, n: string) => [money(v, BASE, true), n]} />
              <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11, paddingTop: 4 }} formatter={legendText} />
              <Area dataKey="balance" name="Free balance" stroke="var(--viz-1)" fill="var(--viz-1)" fillOpacity={0.12} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
              <Area dataKey="saved" name="Savings" stroke="var(--viz-5)" fill="var(--viz-5)" fillOpacity={0.12} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Section>

      {/* Debts */}
      <Section title="Debts" footer="Remaining amounts: remaining recurring payments, card installments and unpaid statements. If a statement is paid, mark it 'Paid' on the card screen.">
        <div className="h-40 px-1 pt-3">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={bars} margin={{ top: 4, right: 8, left: -6, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--sep)" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={axisTick} interval={1} />
              <YAxis tickLine={false} axisLine={false} tick={axisTick} tickFormatter={compact} width={48} />
              <Tooltip {...tooltipStyle} formatter={(v: number) => [money(v, BASE, true), "Remaining debt"]} />
              <Area dataKey="debt_left" name="Remaining debt" stroke="var(--viz-2)" fill="var(--viz-2)" fillOpacity={0.15} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        {p.debts.map((d) => (
          <Row key={d.name}>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px]">{d.name}</div>
              <div className="text-[12px] text-label-2">ends {monthLabel(d.end)}</div>
            </div>
            <span className="tabular text-[15px]">{money(d.remaining, BASE, true)}</span>
          </Row>
        ))}
      </Section>

      {/* Savings goals */}
      <Section
        title="Savings goals"
        action={
          <button type="button" className="text-[15px] text-accent" onClick={onGoal}>
            Add goal
          </button>
        }
        footer="Dated goals with no monthly amount are spread proportionally across months with more free money until the target date."
      >
        {p.goals.length === 0 ? (
          <Row onClick={onGoal}>
            <span className="flex-1 text-[15px] text-accent">Add your first goal (e.g. November 2027 car loan payment)</span>
          </Row>
        ) : (
          p.goals.map((g) => (
            <Row key={g.id}>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[15px]">{g.name}</div>
                <div className="truncate text-[12px] text-label-2">
                  {g.target_month ? `${monthLabel(g.target_month)} · ` : ""}
                  {g.auto ? "auto distribution" : `${money(g.monthly, BASE, true)}/mo`}
                  {g.plan.length ? ` · over ${g.plan.filter((x) => x.amount > 0).length} months` : ""}
                </div>
                {g.shortfall > 0 && <div className="text-[12px] text-red">{money(g.shortfall, BASE, true)} short</div>}
              </div>
              <div className="text-right">
                <div className="tabular text-[15px]">{money(g.target, BASE, true)}</div>
                <div className="text-[12px] text-label-2">planned {money(g.planned, BASE, true)}</div>
              </div>
            </Row>
          ))
        )}
      </Section>

      {/* Table */}
      <Section title="Months" footer={`Spending estimate: ${p.settings.variable_source}. You can change it in Settings.`}>
        <div className="flex gap-2 px-4 pb-1 pt-2 text-[11px] uppercase tracking-wide text-label-2">
          <span className="w-16">Month</span>
          <span className="flex-1 text-right">Income</span>
          <span className="flex-1 text-right">Expenses</span>
          <span className="flex-1 text-right">Net</span>
          <span className="flex-1 text-right">Balance</span>
        </div>
        {months.map((m) => {
          const out = m.fixed + m.debt + m.variable + m.buffer + m.savings;
          return (
            <button
              key={m.month}
              type="button"
              onClick={() => setSelected(m.month)}
              className={`flex w-full gap-2 border-t border-sep px-4 py-2.5 text-left text-[13px] active:bg-fill ${m.month === sel.month ? "bg-fill" : ""}`}
            >
              <span className="w-16 font-medium">{short(m.month)}</span>
              <span className="tabular flex-1 text-right">{compact(m.income + m.release)}</span>
              <span className="tabular flex-1 text-right">{compact(out)}</span>
              <span className={`tabular flex-1 text-right font-semibold ${m.net < 0 ? "text-red" : "text-green"}`}>{compact(m.net)}</span>
              <span className={`tabular flex-1 text-right ${m.balance < 0 ? "text-red" : ""}`}>{compact(m.balance)}</span>
            </button>
          );
        })}
      </Section>
      <p className="mx-8 mt-3 text-[12px] text-label-3">
        Based on spending date: card purchases count in the month they were made. This month also deducts unpaid statements and past card purchases
        not yet on a statement as debt. Amounts are in {BASE}; foreign-currency items use today's rate.
      </p>
    </>
  );
}

function Tile({ label, value, sub, tone, onClick }: { label: string; value: string; sub?: string; tone?: "neg"; onClick?: () => void }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag type={onClick ? "button" : undefined} onClick={onClick} className="rounded-xl bg-card p-3 text-left">
      <div className="text-[12px] text-label-2">{label}</div>
      <div className={`tabular truncate text-[18px] font-semibold ${tone === "neg" ? "text-red" : ""}`}>{value}</div>
      {sub && <div className={`truncate text-[12px] ${onClick ? "text-accent" : "text-label-2"}`}>{sub}</div>}
    </Tag>
  );
}

function MonthChips({ months, selected, onSelect }: { months: PlanMonth[]; selected: string; onSelect: (m: string) => void }) {
  return (
    <div className="flex gap-1.5 overflow-x-auto px-3 pt-3 [scrollbar-width:none]">
      {months.map((m) => (
        <button
          key={m.month}
          type="button"
          onClick={() => onSelect(m.month)}
          className={`shrink-0 rounded-full px-3 py-1 text-[13px] font-medium ${m.month === selected ? "bg-accent text-white" : "bg-fill text-label"}`}
        >
          {short(m.month)}
        </button>
      ))}
    </div>
  );
}

function MonthDonut({ m }: { m: PlanMonth }) {
  const slices = SERIES.map((s) => ({ ...s, value: Math.max(0, (m as unknown as Record<string, number>)[s.key] + (s.key === "fixed" ? m.buffer : 0)) })).filter((s) => s.value > 0);
  const total = slices.reduce((t, s) => t + s.value, 0);
  const income = m.income + m.release;
  return (
    <div className="px-4 pb-3">
      <div className="relative mx-auto h-48 w-48">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={slices} dataKey="value" nameKey="label" innerRadius="62%" outerRadius="100%" stroke="var(--card)" strokeWidth={2} isAnimationActive={false}>
              {slices.map((s) => (
                <Cell key={s.key} fill={s.color} />
              ))}
            </Pie>
            <Tooltip {...tooltipStyle} formatter={(v: number, n: string) => [`${money(v, BASE, true)} · ${total ? Math.round((v / total) * 100) : 0}%`, n]} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-[11px] text-label-2">Income</div>
          <div className="tabular text-[17px] font-semibold">{money(income, BASE, true)}</div>
          {m.net < 0 && <div className="tabular text-[12px] text-red">shortfall {money(-m.net, BASE, true)}</div>}
        </div>
      </div>
      {/* Legend = table view: color alone carries no meaning */}
      <div className="mt-2">
        {slices.map((s) => (
          <div key={s.key} className="flex items-center gap-2 border-t border-sep py-1.5 text-[13px]">
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: s.color }} />
            <span className="flex-1">{s.label}{s.key === "fixed" && m.buffer ? " (incl. buffer)" : ""}</span>
            <span className="tabular text-label-2">{total ? Math.round((s.value / total) * 100) : 0}%</span>
            <span className="tabular w-24 text-right">{money(s.value, BASE, true)}</span>
          </div>
        ))}
      </div>
      {m.top_items.length > 0 && (
        <div className="mt-3">
          <div className="text-[12px] uppercase tracking-wide text-label-2">Items this month</div>
          {m.top_items.map((it) => (
            <div key={it.name} className="flex justify-between gap-2 py-1 text-[13px]">
              <span className="truncate">{it.name}</span>
              <span className="tabular shrink-0">{money(it.amount, BASE, true)}</span>
            </div>
          ))}
          {m.installments > 0 && (
            <div className="flex justify-between gap-2 py-1 text-[13px]">
              <span>Card installments</span>
              <span className="tabular">{money(m.installments, BASE, true)}</span>
            </div>
          )}
          {m.card_backlog > 0 && (
            <div className="flex justify-between gap-2 py-1 text-[13px]">
              <span>Unpaid / pending card statements</span>
              <span className="tabular">{money(m.card_backlog, BASE, true)}</span>
            </div>
          )}
          {m.variable_actual !== null && (
            <div className="flex justify-between gap-2 py-1 text-[13px] text-label-2">
              <span>Actual spending this month</span>
              <span className="tabular">{money(m.variable_actual, BASE, true)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SettingsSheet({ s, onClose }: { s: PlanData["settings"]; onClose: () => void }) {
  const invalidate = useInvalidate();
  const [start, setStart] = useState(amountInput(s.start_balance || null));
  const [variable, setVariable] = useState(amountInput(s.variable || null));
  const [buffer, setBuffer] = useState(amountInput(s.buffer || null));
  const [busy, setBusy] = useState(false);
  const num = (v: string) => (v.trim() ? parseAmount(v) || 0 : 0);

  async function save() {
    setBusy(true);
    try {
      await api.put("reports/plan/settings", { start_balance: num(start), variable: num(variable), buffer: num(buffer) });
      invalidate();
      toast("Saved");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open onClose={onClose} title="Plan Settings" right={<TextButton onClick={save} disabled={busy}>Save</TextButton>}>
      <Section footer="Total free money in your bank accounts today. The cumulative balance starts from here.">
        <Field label={`Starting balance (${BASE})`}>
          <input className={inputCls} inputMode="decimal" placeholder="0" value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
      </Section>
      <Section footer={`If left blank, ${s.variable_source === "category budgets" ? "category budgets are" : "the average of your recent spending is"} used (currently ${money(s.variable_auto, BASE, true)}). Excludes recurring payments and installments; includes hobby spending.`}>
        <Field label={`Monthly spending (${BASE})`}>
          <input className={inputCls} inputMode="decimal" placeholder={String(Math.round(s.variable_auto))} value={variable} onChange={(e) => setVariable(e.target.value)} />
        </Field>
      </Section>
      <Section footer="Amount set aside each month for unexpected expenses (also deducted from the spendable amount on the home screen).">
        <Field label={`Monthly buffer (${BASE})`}>
          <input className={inputCls} inputMode="decimal" placeholder="0" value={buffer} onChange={(e) => setBuffer(e.target.value)} />
        </Field>
      </Section>
      <div className="mx-4 mt-6">
        <PrimaryButton onClick={save} disabled={busy}>
          Save
        </PrimaryButton>
      </div>
    </Sheet>
  );
}

function GoalSheet({ onClose }: { onClose: () => void }) {
  const invalidate = useInvalidate();
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [date, setDate] = useState("");
  const [monthly, setMonthly] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    const t = parseAmount(target);
    if (!name.trim() || !(t > 0)) return toast("Name and target amount are required", "err");
    setBusy(true);
    try {
      await api.post("savings/goals", { name: name.trim(), target: t, target_date: date || null, monthly: monthly.trim() ? parseAmount(monthly) || 0 : 0 });
      invalidate();
      toast("Goal added");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open onClose={onClose} title="Savings Goal" right={<TextButton onClick={save} disabled={busy}>Save</TextButton>}>
      <Section>
        <Field label="Name">
          <input className={inputCls} placeholder="e.g. Vacation, Emergency" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={`Target amount (${BASE})`}>
          <input className={inputCls} inputMode="decimal" placeholder="0" value={target} onChange={(e) => setTarget(e.target.value)} />
        </Field>
        <Field label="Target date">
          <input type="date" className={inputCls} value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label={`Monthly (${BASE})`}>
          <input className={inputCls} inputMode="decimal" placeholder="Blank: automatic" value={monthly} onChange={(e) => setMonthly(e.target.value)} />
        </Field>
      </Section>
      <p className="mx-8 mt-1.5 text-[13px] text-label-2">
        If you leave the monthly amount blank, it is spread across months with more free money until the target date. You can also manage it on the Savings screen.
      </p>
      <div className="mx-4 mt-6">
        <PrimaryButton onClick={save} disabled={busy}>
          Save
        </PrimaryButton>
      </div>
    </Sheet>
  );
}
