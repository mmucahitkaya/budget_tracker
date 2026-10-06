import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CatBadge, Field, Icon, MonthSwitcher, PageHeader, PrimaryButton, Progress, Row, Section, Sheet, TextButton, inputCls, toast, ErrorState } from "../components/ui";
import { api, type Category } from "../lib/api";
import { BASE, amountInput, money, monthLabel, parseAmount, shiftMonth, thisMonth } from "../lib/format";
import { useBudgets, useInvalidate, useLookups } from "../lib/queries";

export default function Budgets() {
  const [month, setMonth] = useState(thisMonth());
  const budgetsQ = useBudgets(month);
  const budgets = budgetsQ.data ?? [];
  const prevMonth = shiftMonth(month, -1);
  const prevBudgets = useBudgets(prevMonth).data ?? [];
  const invalidate = useInvalidate();
  async function copyPrev() {
    const r = await api.post<{ copied: number }>("budgets/copy", { from_month: prevMonth, to_month: month });
    invalidate();
    toast(`${r.copied} budgets copied`);
  }
  const { cats } = useLookups();
  const nav = useNavigate();
  const [edit, setEdit] = useState<Category | null>(null);
  const byCat = new Map(budgets.map((b) => [b.category_id, b]));
  const expenseCats = cats.filter((c) => c.kind === "expense" && !c.archived);
  const totalLimit = budgets.reduce((s, b) => s + b.limit, 0);
  const totalSpent = budgets.reduce((s, b) => s + b.spent, 0);

  return (
    <div>
      <PageHeader
        title="Budgets"
        left={
          <TextButton onClick={() => nav("/more")}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> More
          </TextButton>
        }
        right={<MonthSwitcher value={month} onChange={setMonth} />}
      />
      {budgetsQ.isError && <ErrorState error={budgetsQ.error} onRetry={() => budgetsQ.refetch()} />}
      {budgetsQ.isSuccess && budgets.length === 0 && prevBudgets.length > 0 && (
        <div className="mx-4 mt-4 flex items-center gap-3 rounded-2xl bg-card p-4">
          <div className="min-w-0 flex-1 text-[15px]">
            No budgets for {monthLabel(month)}. {monthLabel(prevMonth)} has limits in {prevBudgets.length} categories.
          </div>
          <button type="button" onClick={copyPrev} className="h-9 shrink-0 rounded-full bg-accent px-4 text-[15px] font-semibold text-white active:opacity-70">
            Copy
          </button>
        </div>
      )}
      {budgets.length > 0 && (
        <div className="mx-4 mt-4 rounded-2xl bg-card p-4">
          <div className="flex items-end justify-between">
            <div>
              <div className="text-[13px] text-label-2">Spent in budgeted categories</div>
              <div className="tabular text-[28px] font-bold">{money(totalSpent, BASE, true)}</div>
            </div>
            <div className="tabular text-[15px] text-label-2">/ {money(totalLimit, BASE, true)}</div>
          </div>
          <Progress ratio={totalLimit ? totalSpent / totalLimit : 0} className="mt-3 !h-2" />
        </div>
      )}
      <Section title="Categories" footer={`Limits apply only to the selected month; changing them does not affect past months. On the 1st of each month, the previous month's limits carry over to the new month. Calculated in ${BASE} equivalent; you get notified at 80% and 100%.`}>
        {expenseCats.map((c) => {
          const b = byCat.get(c.id);
          return (
            <Row key={c.id} onClick={() => setEdit(c)} chevron>
              <CatBadge cat={c} size={32} />
              <div className="min-w-0 flex-1">
                <div className="flex justify-between gap-2 text-[15px]">
                  <span className="truncate">{c.name}</span>
                  {b ? (
                    <span className={`tabular shrink-0 ${b.ratio >= 1 ? "text-red" : "text-label-2"}`}>
                      {money(b.spent, BASE, true)} / {money(b.limit, BASE, true)}
                    </span>
                  ) : (
                    <span className="shrink-0 text-label-3">No limit</span>
                  )}
                </div>
                {b && <Progress ratio={b.ratio} className="mt-1.5" />}
              </div>
            </Row>
          );
        })}
      </Section>
      {edit && <BudgetSheet cat={edit} month={month} current={byCat.get(edit.id)?.limit} onClose={() => setEdit(null)} />}
    </div>
  );
}

function BudgetSheet({ cat, month, current, onClose }: { cat: Category; month: string; current?: number; onClose: () => void }) {
  const invalidate = useInvalidate();
  const [limit, setLimit] = useState(amountInput(current));
  async function save(value?: number) {
    const v = value ?? (limit.trim() ? parseAmount(limit) : 0);
    if (Number.isNaN(v)) return toast("Invalid amount", "err");
    await api.put("budgets", { category_id: cat.id, limit: v, month });
    invalidate();
    toast(v ? "Budget saved" : "Budget removed");
    onClose();
  }
  return (
    <Sheet open onClose={onClose} title={`${cat.icon} ${cat.name} · ${monthLabel(month, true)}`} right={<TextButton onClick={() => save()}>Save</TextButton>}>
      <Section>
        <Field label={`Monthly limit (${BASE})`}>
          <input autoFocus className={inputCls} inputMode="decimal" placeholder="0" value={limit} onChange={(e) => setLimit(e.target.value)} />
        </Field>
      </Section>
      <div className="mx-4 mt-6 space-y-3">
        <PrimaryButton onClick={() => save()}>Save</PrimaryButton>
        {current !== undefined && (
          <PrimaryButton tone="red" onClick={() => save(0)}>
            Remove Budget
          </PrimaryButton>
        )}
      </div>
    </Sheet>
  );
}
