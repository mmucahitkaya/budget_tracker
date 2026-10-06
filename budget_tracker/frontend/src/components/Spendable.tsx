import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { BASE, amountInput, money, monthLabel, parseAmount, shortDate } from "../lib/format";
import { useInvalidate } from "../lib/queries";
import { Field, Icon, PrimaryButton, Row, Section, Sheet, inputCls, toast } from "./ui";

interface MonthSpend {
  month: string;
  income: number;
  fixed: number;
  cards: number;
  spent: number;
  savings: number;
  buffer: number;
  available: number;
  per_day?: number;
  days_left?: number;
  items: { date: string; title: string; amount: number; direction: "in" | "out"; kind: string; estimated: boolean }[];
}
interface SpendableData {
  this_month: MonthSpend;
  next_month: MonthSpend;
  fx_missing: unknown[];
}

/** Home: remaining this month and the most you can spend next month. */
export default function SpendableCard() {
  const q = useQuery({ queryKey: ["spendable"], queryFn: () => api.get<SpendableData>("reports/spendable") });
  const [open, setOpen] = useState(false);
  const d = q.data;
  if (!d) return null;
  const m0 = d.this_month;
  const m1 = d.next_month;
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="mx-4 mt-3 block w-[calc(100%-2rem)] rounded-2xl bg-card p-4 text-left active:opacity-70">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[13px] font-medium uppercase tracking-wide text-label-2">{monthLabel(m0.month)} left</div>
            <div className={`tabular text-[26px] font-bold leading-tight ${m0.available < 0 ? "text-red" : "text-green"}`}>
              {money(m0.available, BASE, true)}
            </div>
            {m0.available > 0 && m0.per_day !== undefined && (
              <div className="text-[13px] text-label-2">
                ~{money(m0.per_day, BASE, true)}/day · {m0.days_left} days
              </div>
            )}
          </div>
          <div className="shrink-0 text-right">
            <div className="text-[13px] text-label-2">{monthLabel(m1.month)} max</div>
            <div className={`tabular text-[17px] font-semibold ${m1.available < 0 ? "text-red" : ""}`}>{money(m1.available, BASE, true)}</div>
            <div className="mt-1 flex items-center justify-end gap-0.5 text-[12px] text-accent">
              Breakdown <Icon name="chevronRight" size={14} />
            </div>
          </div>
        </div>
      </button>
      {open && <SpendableSheet d={d} onClose={() => setOpen(false)} />}
    </>
  );
}

function Breakdown({ m, next }: { m: MonthSpend; next?: boolean }) {
  const rows: [string, number, boolean][] = [
    ["Income", m.income, true],
    ["Recurring expenses (cash/bank)", -m.fixed, false],
    [next ? "Card statements (estimated)" : "Card payments", -m.cards, false],
    ...(next ? [] : ([["Cash/bank spending", -m.spent, false]] as [string, number, boolean][])),
    ["Savings plan", -m.savings, false],
    ["Buffer", -m.buffer, false],
  ];
  return (
    <>
      {rows
        .filter(([label, v]) => v !== 0 || label === "Income")
        .map(([label, v, inc]) => (
          <Row key={label}>
            <span className="flex-1 text-[15px]">{label}</span>
            <span className={`tabular text-[15px] ${inc ? "text-green" : ""}`}>{money(v, BASE, true)}</span>
          </Row>
        ))}
      <Row>
        <span className="flex-1 text-[15px] font-semibold">{next ? "Max spendable" : "Remaining"}</span>
        <span className={`tabular text-[17px] font-semibold ${m.available < 0 ? "text-red" : ""}`}>{money(m.available, BASE, true)}</span>
      </Row>
    </>
  );
}

function SpendableSheet({ d, onClose }: { d: SpendableData; onClose: () => void }) {
  const invalidate = useInvalidate();
  const [buffer, setBuffer] = useState(amountInput(d.this_month.buffer || null));
  const [busy, setBusy] = useState(false);
  const upcoming = [...d.this_month.items, ...d.next_month.items].filter((e) => e.direction === "out");

  async function saveBuffer() {
    setBusy(true);
    try {
      await api.put("settings", { spend_buffer: buffer.trim() ? parseAmount(buffer) || 0 : 0 });
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
    <Sheet open onClose={onClose} title="Spendable">
      <p className="mx-8 mt-1 text-[13px] text-label-2">
        Card spending counts in the month it is paid: statements due this month come out of this month, and what you spend by card this month goes on next month's statement.
        Bank balance (carried over from previous months) is not included; vouchers are not counted.
      </p>
      <Section title={`${monthLabel(d.this_month.month)} (to date + remaining days)`}>
        <Breakdown m={d.this_month} />
      </Section>
      <Section title={`${monthLabel(d.next_month.month)} (estimated)`} footer="Next month's card statements are estimated from your card spending to date and installments; it goes down as you spend by card.">
        <Breakdown m={d.next_month} next />
      </Section>
      {upcoming.length > 0 && (
        <Section title="Expected outflows">
          {upcoming.map((e, i) => (
            <Row key={`${e.date}-${i}`}>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[15px]">{e.title}</div>
                <div className="text-[12px] text-label-2">
                  {shortDate(e.date)}
                  {e.estimated ? " · estimated" : ""}
                </div>
              </div>
              <span className="tabular text-[15px]">{money(e.amount, BASE, true)}</span>
            </Row>
          ))}
        </Section>
      )}
      <Section title="Buffer" footer="Amount set aside each month for unexpected expenses; deducted from spendable. The savings plan comes from monthly contributions in Savings & Goals.">
        <Field label={`Monthly buffer (${BASE})`}>
          <input className={inputCls} inputMode="decimal" placeholder="0" value={buffer} onChange={(e) => setBuffer(e.target.value)} />
        </Field>
      </Section>
      <div className="mx-4 mt-6">
        <PrimaryButton onClick={saveBuffer} disabled={busy}>
          Save
        </PrimaryButton>
      </div>
    </Sheet>
  );
}
