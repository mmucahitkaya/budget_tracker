import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { useAdd } from "../App";
import TxRow from "../components/TxRow";
import { Empty, ErrorState, Icon, PageHeader, Row, Section, Segmented, Select, Sheet, Spinner, TextButton, toast } from "../components/ui";
import { api, type Tx } from "../lib/api";
import { BASE, LOCALE, money } from "../lib/format";
import { useInvalidate, useLookups } from "../lib/queries";

interface Drill {
  title: string;
  ids: number[];
}

interface InsightData {
  empty?: boolean;
  days: number;
  span: number;
  since: string;
  monthly_total: number;
  monthly_variable: number;
  monthly_fixed: number;
  monthly_installment: number;
  uncategorized_share: number;
  categories: { category_id: number | null; name: string; icon: string; total: number; monthly: number; share: number; count: number; tx_ids: number[] }[];
  merchants: { name: string; category_id: number | null; total: number; monthly: number; count: number; avg: number; tx_ids: number[] }[];
  subscriptions: { name: string; count: number; monthly: number; avg: number; tx_ids: number[] }[];
  subscriptions_monthly: number;
  frequent: { name: string; per_month: number; avg: number; monthly: number; tx_ids: number[] }[];
  tips: { kind: string; title: string; text: string; saving: number; tx_ids: number[] }[];
  saving_total: number;
}

// Validated categorical palette; color is tied to the category (not order), "Other" is gray
const PALETTE = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)", "var(--viz-4)", "var(--viz-5)", "var(--viz-6)", "var(--viz-7)", "var(--viz-8)"];
const OTHER_COLOR = "var(--label-3)";
const TIP_ICON: Record<string, string> = {
  categorize: "🏷",
  subscriptions: "🔁",
  eating: "🍽️",
  delivery: "🛵",
  frequent: "🛒",
  fees: "🏦",
  installments: "💳",
  top: "📊",
};
const tooltipStyle = {
  contentStyle: { background: "var(--card)", border: "none", borderRadius: 12, boxShadow: "0 4px 16px rgba(0,0,0,.15)", fontSize: 13 },
  itemStyle: { color: "var(--label)" },
};

export default function InsightsPage() {
  const nav = useNavigate();
  const [days, setDays] = useState<"30" | "90" | "180">("90");
  const q = useQuery({ queryKey: ["insights", days], queryFn: () => api.get<InsightData>(`reports/insights?days=${days}`) });
  const d = q.data;

  return (
    <div>
      <PageHeader
        title="Insights"
        left={
          <TextButton onClick={() => nav(-1)}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> Back
          </TextButton>
        }
      />
      <div className="mx-4 mt-3">
        <Segmented
          value={days}
          onChange={setDays}
          options={[
            { value: "30", label: "Last month" },
            { value: "90", label: "Last 3 months" },
            { value: "180", label: "Last 6 months" },
          ]}
        />
      </div>
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !d ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : d.empty ? (
        <Empty icon="chart" title="No spending in this period" text="As you upload statements, your biggest expenses and savings tips will appear here." />
      ) : (
        <InsightBody d={d} />
      )}
    </div>
  );
}

function InsightBody({ d }: { d: InsightData }) {
  const nav = useNavigate();
  const { cats } = useLookups();
  const [drill, setDrill] = useState<Drill | null>(null);
  const open = (title: string, ids: number[]) => ids.length && setDrill({ title, ids });
  // Category → fixed palette slot (by category order, always the same color)
  const order = cats.filter((c) => c.kind === "expense").map((c) => c.id);
  const colorOf = (id: number | null) => (id === null ? OTHER_COLOR : PALETTE[Math.max(0, order.indexOf(id)) % PALETTE.length]);
  const top = d.categories.slice(0, 7);
  const rest = d.categories.slice(7);
  const slices = [
    ...top.map((c) => ({ ...c, color: colorOf(c.category_id) })),
    ...(rest.length ? [{ category_id: -1, name: "Other categories", icon: "•", total: 0, monthly: rest.reduce((s, c) => s + c.monthly, 0), share: rest.reduce((s, c) => s + c.share, 0), count: 0, color: OTHER_COLOR }] : []),
  ];
  const maxMerchant = Math.max(1, ...d.merchants.map((m) => m.monthly));
  const uncatPct = Math.round(d.uncategorized_share * 100);

  return (
    <>
      {uncatPct >= 10 && (
        <button type="button" onClick={() => nav("/categorize")} className="mx-4 mt-3 flex w-[calc(100%-2rem)] items-center gap-3 rounded-xl bg-orange/15 p-3 text-left">
          <span className="text-[20px]">🏷</span>
          <span className="min-w-0 flex-1 text-[13px]">
            <b>{uncatPct}%</b> of spending is in "Other". The analysis gets sharper as you categorize.
          </span>
          <span className="shrink-0 text-[14px] font-semibold text-accent">Categorize</span>
        </button>
      )}

      <div className="mx-4 mt-3 grid grid-cols-2 gap-3">
        <div className="rounded-xl bg-card p-3">
          <div className="text-[12px] text-label-2">Monthly spending</div>
          <div className="tabular text-[20px] font-semibold">{money(d.monthly_total, BASE, true)}</div>
          <div className="text-[12px] text-label-2">
            variable {money(d.monthly_variable, BASE, true)}
            {d.monthly_installment ? ` · installments ${money(d.monthly_installment, BASE, true)}` : ""}
            {d.monthly_fixed ? ` · fixed ${money(d.monthly_fixed, BASE, true)}` : ""}
          </div>
        </div>
        <div className="rounded-xl bg-card p-3">
          <div className="text-[12px] text-label-2">Potential savings</div>
          <div className="tabular text-[20px] font-semibold text-green">{money(d.saving_total, BASE, true)}</div>
          <div className="text-[12px] text-label-2">per month, based on tips</div>
        </div>
      </div>
      {d.span < d.days && <p className="mx-8 mt-1.5 text-[12px] text-label-3">Data covers {d.span} days; monthly amounts are scaled accordingly.</p>}

      {d.tips.length > 0 && (
        <Section title="Savings tips">
          {d.tips.map((t) => (
            <button
              type="button"
              key={t.kind}
              onClick={() => (t.kind === "categorize" ? nav("/categorize") : open(t.title, t.tx_ids))}
              className="flex w-full gap-3 border-b border-sep px-4 py-3 text-left last:border-b-0 active:bg-fill"
            >
              <span className="text-[22px] leading-none">{TIP_ICON[t.kind] ?? "💡"}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[15px] font-semibold">{t.title}</span>
                  {t.saving > 0 && <span className="tabular shrink-0 text-[13px] font-semibold text-green">~{money(t.saving, BASE, true)}/mo</span>}
                </div>
                <p className="mt-0.5 text-[13px] text-label-2">{t.text}</p>
                {(t.kind === "categorize" || t.tx_ids.length > 0) && (
                  <span className="mt-1 inline-block text-[13px] font-semibold text-accent">
                    {t.kind === "categorize" ? "Categorize →" : `Review ${t.tx_ids.length} transactions →`}
                  </span>
                )}
              </div>
              {t.kind !== "categorize" && t.tx_ids.length > 0 && <Icon name="chevronRight" size={16} className="mt-1 shrink-0 text-label-3" stroke={2.5} />}
            </button>
          ))}
        </Section>
      )}

      <Section title="Biggest expense categories" footer="Monthly average; includes recurring payments (rent, car loan…).">
        <div className="relative mx-auto mt-3 h-52 w-52">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={slices} dataKey="monthly" nameKey="name" innerRadius="60%" outerRadius="100%" stroke="var(--card)" strokeWidth={2} isAnimationActive={false}>
                {slices.map((s) => (
                  <Cell key={`${s.category_id}-${s.name}`} fill={s.color} />
                ))}
              </Pie>
              <Tooltip {...tooltipStyle} formatter={(v: number, n: string) => [`${money(v, BASE, true)}/mo`, n]} />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <div className="text-[11px] text-label-2">Monthly</div>
            <div className="tabular text-[18px] font-semibold">{money(d.monthly_total, BASE, true)}</div>
          </div>
        </div>
        <div className="mt-2 pb-1">
          {d.categories.map((c) => (
            <button
              type="button"
              key={`${c.category_id}-${c.name}`}
              onClick={() => (c.category_id === null ? nav("/categorize") : open(`${c.icon} ${c.name}`, c.tx_ids))}
              className="flex w-full items-center gap-2 border-t border-sep px-4 py-2 text-left text-[14px] active:bg-fill"
            >
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: top.includes(c) ? colorOf(c.category_id) : OTHER_COLOR }} />
              <span className="min-w-0 flex-1 truncate">
                {c.icon} {c.name}
              </span>
              <span className="tabular text-[12px] text-label-2">{Math.round(c.share * 100)}%</span>
              <span className="tabular w-24 text-right">{money(c.monthly, BASE, true)}</span>
              <Icon name="chevronRight" size={14} className="shrink-0 text-label-3" stroke={2.5} />
            </button>
          ))}
        </div>
      </Section>

      <Section title="Top merchants" footer="Monthly average; excludes recurring payments and installment purchases.">
        {d.merchants.map((m) => (
          <button type="button" key={m.name} onClick={() => open(m.name, m.tx_ids)} className="block w-full border-b border-sep px-4 py-2.5 text-left last:border-b-0 active:bg-fill">
            <div className="flex items-baseline justify-between gap-2 text-[14px]">
              <span className="min-w-0 truncate">{m.name}</span>
              <span className="tabular shrink-0 font-semibold">{money(m.monthly, BASE, true)}</span>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-fill">
                <div className="h-full rounded-full" style={{ width: `${(m.monthly / maxMerchant) * 100}%`, background: colorOf(m.category_id) }} />
              </div>
              <span className="shrink-0 text-[11px] text-label-2">
                {m.count}× · avg. {money(m.avg, BASE, true)}
              </span>
            </div>
          </button>
        ))}
      </Section>

      {d.subscriptions.length > 0 && (
        <Section title={`Subscriptions and recurring · ~${money(d.subscriptions_monthly, BASE, true)}/mo`}>
          {d.subscriptions.map((s) => (
            <Row key={s.name} chevron onClick={() => open(s.name, s.tx_ids)}>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[14px]">{s.name}</div>
                <div className="text-[12px] text-label-2">
                  {s.count} payments · avg. {money(s.avg, BASE, true)}
                </div>
              </div>
              <span className="tabular text-[14px]">{money(s.monthly, BASE, true)}/mo</span>
            </Row>
          ))}
        </Section>
      )}

      {d.frequent.length > 0 && (
        <Section title="Frequent small purchases" footer="Merchants visited 6 or more times a month with small baskets.">
          {d.frequent.map((f) => (
            <Row key={f.name} chevron onClick={() => open(f.name, f.tx_ids)}>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[14px]">{f.name}</div>
                <div className="text-[12px] text-label-2">
                  ~{f.per_month}× a month · basket {money(f.avg, BASE, true)}
                </div>
              </div>
              <span className="tabular text-[14px]">{money(f.monthly, BASE, true)}/mo</span>
            </Row>
          ))}
        </Section>
      )}
      <p className="mx-8 mt-3 text-[12px] text-label-3">Tips are rule-based estimates from your spending history. Tap a row to review its transactions.</p>
      {drill && <DrillSheet drill={drill} onClose={() => setDrill(null)} />}
    </>
  );
}

/** Transactions behind a tip/category/merchant: grouped by merchant; tap a transaction → edit, assign a category to a group. */
function DrillSheet({ drill, onClose }: { drill: Drill; onClose: () => void }) {
  const { editTx } = useAdd();
  const invalidate = useInvalidate();
  const { cats } = useLookups();
  const q = useQuery({ queryKey: ["txids", drill.ids], queryFn: () => api.post<Tx[]>("transactions/by-ids", { ids: drill.ids }) });
  const rows = q.data ?? [];
  const groups = new Map<string, Tx[]>();
  for (const t of rows) {
    const key = (t.merchant || "No description").toLocaleLowerCase(LOCALE).split(/[^\p{L}\p{N}]+/u).filter(Boolean).slice(0, 2).join(" ");
    groups.set(key, [...(groups.get(key) ?? []), t]);
  }
  const sorted = [...groups.values()].sort((a, b) => sum(b) - sum(a));
  const total = rows.reduce((s, t) => s + (t.kind === "expense" ? t.amount : 0), 0);
  const options = cats.filter((c) => c.kind === "expense" && !c.archived).map((c) => ({ value: c.id, label: `${c.icon} ${c.name}` }));

  async function recategorize(ts: Tx[], categoryId: number) {
    try {
      await api.post("transactions/categorize", { tx_ids: ts.map((t) => t.id), category_id: categoryId });
      invalidate();
      toast("Category changed; merchant learned");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }

  return (
    <Sheet open onClose={onClose} title={drill.title}>
      {!q.data ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : (
        <>
          <p className="mx-8 mt-1 text-[13px] text-label-2">
            {rows.length} transactions · total {money(total, BASE, true)}. Tap a transaction to edit it; use the picker on the right to categorize all of a merchant's transactions.
          </p>
          {sorted.map((ts) => (
            <Section
              key={ts[0].id}
              title={`${ts[0].merchant || "No description"} · ${money(sum(ts), BASE, true)}`}
              action={
                <div className="w-28 shrink-0 rounded-md bg-fill text-[12px]">
                  <Select value={null} placeholder="Category…" options={options} onChange={(id: number) => recategorize(ts, id)} />
                </div>
              }
            >
              {ts.map((t) => (
                <TxRow key={t.id} tx={t} onClick={() => editTx(t)} />
              ))}
            </Section>
          ))}
        </>
      )}
    </Sheet>
  );
}

function sum(ts: Tx[]) {
  return ts.reduce((s, t) => s + (t.kind === "expense" ? t.amount : -t.amount), 0);
}
