import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAdd } from "../App";
import SpendableCard from "../components/Spendable";
import { usePendingCategories } from "../lib/categorize";
import TxRow from "../components/TxRow";
import { CatBadge, Empty, FxWarning, Icon, MonthSwitcher, PageHeader, Progress, Row, Section, Spinner, ErrorState } from "../components/ui";
import type { Currency } from "../lib/api";
import { BASE, daysUntil, dayLabel, money, monthLabel, thisMonth } from "../lib/format";
import { useBudgets, useDocuments, useLookups, useSummary, useTransactions } from "../lib/queries";

export default function Dashboard() {
  const [month, setMonth] = useState(thisMonth());
  const sumQ = useSummary(month);
  const { data: s, isLoading } = sumQ;
  const budgets = useBudgets(month).data ?? [];
  const recent = useTransactions({ month, limit: 6 }).data ?? [];
  const docs = useDocuments().data ?? [];
  const { catById, cards, userById, me } = useLookups();
  const { openAdd, editTx } = useAdd();
  const nav = useNavigate();
  const review = docs.filter((d) => d.status === "review");
  const pendingCats = usePendingCategories().data?.count ?? 0;
  const processing = docs.filter((d) => d.status === "pending" || d.status === "processing");

  const diff = s && s.prev_expense > 0 ? (s.expense - s.prev_expense) / s.prev_expense : null;
  const foreign = s ? (Object.entries(s.by_currency) as [Currency, { income: number; expense: number }][]).filter(([c]) => c !== BASE) : [];

  return (
    <div>
      <PageHeader title="Summary" left={<MonthSwitcher value={month} onChange={setMonth} />} />

      {sumQ.isError ? (
        <ErrorState error={sumQ.error} onRetry={() => sumQ.refetch()} />
      ) : isLoading || !s ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : (
        <>
          {/* Main card: expense / income / net */}
          <div className="mx-4 mt-4 rounded-2xl bg-card p-4">
            <div className="text-[13px] font-medium uppercase tracking-wide text-label-2">{monthLabel(month)} expenses</div>
            <div className="tabular mt-1 text-[34px] font-bold leading-tight">{money(s.expense)}</div>
            {diff !== null && (
              <div className={`mt-0.5 text-[13px] ${diff > 0 ? "text-red" : "text-green"}`}>
                {month === thisMonth() ? "vs. same day last month" : "vs. last month"} {diff > 0 ? "▲" : "▼"} {Math.abs(diff * 100).toFixed(0)}%
              </div>
            )}
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-card-2 p-3">
                <div className="text-[13px] text-label-2">Income</div>
                <div className="tabular truncate text-[17px] font-semibold text-green">{money(s.income, BASE, true)}</div>
              </div>
              <div className="rounded-xl bg-card-2 p-3">
                <div className="text-[13px] text-label-2">Net</div>
                <div className={`tabular truncate text-[17px] font-semibold ${s.net < 0 ? "text-red" : ""}`}>{money(s.net, BASE, true)}</div>
              </div>
            </div>
            {foreign.length > 0 && (
              <div className="mt-3 text-[13px] text-label-2">
                Foreign currency spending:{" "}
                {foreign.map(([c, v]) => money(v.expense, c)).join(" · ")} <span className="text-label-3">({BASE} equivalent included in total)</span>
              </div>
            )}
          </div>

          <FxWarning missing={s.fx_missing} />

          {month === thisMonth() && <SpendableCard />}

          {pendingCats > 0 && (
            <button
              type="button"
              onClick={() => nav("/categorize")}
              className="mx-4 mt-3 flex w-[calc(100%-2rem)] items-center gap-3 rounded-2xl bg-card p-3.5 text-left active:opacity-70"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red/15 text-[18px]">🏷</span>
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] font-semibold">{pendingCats} expenses need a category</span>
                <span className="block truncate text-[13px] text-label-2">Statement items filed under "Other" · fix with one tap</span>
              </span>
              <Icon name="chevronRight" size={16} className="text-label-3" stroke={2.5} />
            </button>
          )}

          {/* Quick actions */}
          <div className="mx-4 mt-3 grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => openAdd("upload")}
              className="flex h-12 items-center justify-center gap-2 rounded-xl bg-card text-[15px] font-semibold text-accent active:opacity-60"
            >
              <Icon name="camera" size={20} /> Receipt / Statement
            </button>
            <button
              type="button"
              onClick={() => openAdd("manual")}
              className="flex h-12 items-center justify-center gap-2 rounded-xl bg-card text-[15px] font-semibold text-accent active:opacity-60"
            >
              <Icon name="pencil" size={20} /> Add Manually
            </button>
          </div>

          {s.planned.count > 0 && (
            <Section>
              <Row chevron onClick={() => nav(`/transactions?month=${month}`)}>
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-green/15 text-green">
                  <Icon name="repeat" size={19} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[17px]">
                    {month === thisMonth() ? "Remaining recurring payments" : "Planned payments"}
                  </div>
                  <div className="text-[13px] text-label-2">
                    {s.planned.count} items{s.planned.income > 0 ? ` · income ${money(s.planned.income, BASE, true)}` : ""}
                  </div>
                </div>
                <div className="tabular text-[17px]">{money(s.planned.expense, BASE, true)}</div>
              </Row>
            </Section>
          )}

          {(review.length > 0 || processing.length > 0) && (
            <Section>
              {review.length > 0 && (
                <Row chevron onClick={() => nav(review.length === 1 ? `/documents/${review[0].id}` : "/documents")}>
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-orange/15 text-orange">
                    <Icon name="doc" size={20} />
                  </span>
                  <div className="flex-1 text-[17px]">{review.length} {review.length === 1 ? "document needs" : "documents need"} review</div>
                </Row>
              )}
              {processing.length > 0 && (
                <Row onClick={() => nav("/documents")}>
                  <span className="flex h-9 w-9 items-center justify-center">
                    <Spinner />
                  </span>
                  <div className="flex-1 text-[17px] text-label-2">Reading {processing.length} {processing.length === 1 ? "document" : "documents"}…</div>
                </Row>
              )}
            </Section>
          )}

          {s.upcoming.length > 0 && (
            <Section title="Upcoming payments">
              {s.upcoming.map((u) => {
                const d = daysUntil(u.date);
                return (
                  <Row key={`${u.type}-${u.id}`} chevron onClick={() => nav(u.type === "card" ? `/cards/${u.id}` : u.kind === "income" ? "/recurring/income" : "/recurring/expenses")}>
                    <span
                      className={`flex h-9 w-9 items-center justify-center rounded-full ${
                        u.type === "card" ? "bg-accent/15 text-accent" : "bg-green/15 text-green"
                      }`}
                    >
                      <Icon name={u.type === "card" ? "card" : "repeat"} size={19} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[17px]">{u.title}</div>
                      <div className={`text-[13px] ${d < 0 ? "text-red" : d <= 2 ? "text-orange" : "text-label-2"}`}>
                        {d < 0 ? `${-d} ${-d === 1 ? "day" : "days"} overdue` : dayLabel(u.date)}
                        {u.min_payment ? ` · min. ${money(u.min_payment, u.currency, true)}` : ""}
                      </div>
                    </div>
                    <div className="tabular text-[17px]">{money(u.amount, u.currency, true)}</div>
                  </Row>
                );
              })}
            </Section>
          )}

          {budgets.length > 0 && (
            <Section title="Budgets" action={<button className="text-[15px] text-accent" onClick={() => nav("/budgets")}>All</button>}>
              {budgets.slice(0, 4).map((b) => {
                const cat = catById.get(b.category_id);
                return (
                  <Row key={b.id}>
                    <CatBadge cat={cat} size={32} />
                    <div className="min-w-0 flex-1">
                      <div className="flex justify-between gap-2 text-[15px]">
                        <span className="truncate">{cat?.name}</span>
                        <span className="tabular shrink-0 text-label-2">
                          {money(b.spent, BASE, true)} / {money(b.limit, BASE, true)}
                        </span>
                      </div>
                      <Progress ratio={b.ratio} className="mt-1.5" />
                    </div>
                  </Row>
                );
              })}
            </Section>
          )}

          {s.by_category.length > 0 && (
            <Section title="Categories" action={<button className="text-[15px] text-accent" onClick={() => nav("/reports")}>Report</button>}>
              {s.by_category.slice(0, 6).map((c) => {
                const cat = c.category_id ? catById.get(c.category_id) : null;
                const ratio = s.expense ? c.total / s.expense : 0;
                return (
                  <Row key={c.category_id ?? "none"} onClick={() => nav(`/transactions?category=${c.category_id ?? ""}&month=${month}`)}>
                    <CatBadge cat={cat} size={32} />
                    <div className="min-w-0 flex-1">
                      <div className="flex justify-between gap-2 text-[15px]">
                        <span className="truncate">{cat?.name ?? "Uncategorized"}</span>
                        <span className="tabular shrink-0">{money(c.total, BASE, true)}</span>
                      </div>
                      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-fill">
                        <div className="h-full rounded-full" style={{ width: `${ratio * 100}%`, background: cat?.color ?? "#8E8E93" }} />
                      </div>
                    </div>
                  </Row>
                );
              })}
            </Section>
          )}

          {(me?.users.length ?? 0) > 1 && s.by_user.length > 0 && (
            <Section title="Who spent">
              <div className="flex gap-2 p-3">
                {s.by_user.map((u) => (
                  <div key={u.user_id ?? 0} className="min-w-0 flex-1 rounded-xl bg-card-2 p-3">
                    <div className="truncate text-[13px] text-label-2">{(u.user_id && userById.get(u.user_id)?.name) || "Shared"}</div>
                    <div className="tabular truncate text-[17px] font-semibold">{money(u.total, BASE, true)}</div>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {cards.length > 0 && (
            <Section title="Card balances" action={<button className="text-[15px] text-accent" onClick={() => nav("/cards")}>Cards</button>}>
              <Row>
                <div className="flex-1 text-[17px]">Total</div>
                <div className="tabular text-right text-[17px] font-semibold">
                  {Object.entries(s.card_debt ?? {})
                    .filter(([, v]) => Math.abs(v) >= 0.005)
                    .sort(([a], [b]) => (a === BASE ? -1 : b === BASE ? 1 : a.localeCompare(b)))
                    .map(([c, v], i) => (
                      <div key={c} className={i ? "text-[13px] font-normal text-label-2" : undefined}>
                        {money(v, c)}
                      </div>
                    ))}
                  {!Object.values(s.card_debt ?? {}).some((v) => Math.abs(v) >= 0.005) && money(0)}
                </div>
              </Row>
            </Section>
          )}

          <Section title="Recent transactions" action={<button className="text-[15px] text-accent" onClick={() => nav(`/transactions?month=${month}`)}>All</button>}>
            {recent.length ? (
              recent.map((t) => <TxRow key={t.id} tx={t} onClick={() => editTx(t)} />)
            ) : (
              <Empty icon="list" title="No transactions yet" text="Add your first expense with the + button." />
            )}
          </Section>
        </>
      )}
    </div>
  );
}
