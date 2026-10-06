import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAdd } from "../App";
import CardForm from "../components/CardForm";
import CardTile from "../components/CardTile";
import TxRow from "../components/TxRow";
import {
  AmountsList,
  CurrencyAmountsEditor,
  CurrencyPicker,
  Field,
  Icon,
  IconButton,
  PageHeader,
  PrimaryButton,
  Row,
  Section,
  Segmented,
  Select,
  Sheet,
  Spinner,
  TextButton,
  Toggle,
  dateCls,
  inputCls,
  toast,
  ErrorState,
  amountRows,
  rowsToAmounts,
  type AmountRow,
} from "../components/ui";
import { amountEntries, api, fileUrl, statementPaid, statementSettled, type Card, type Currency, type Payment, type Statement } from "../lib/api";
import { BASE, amountInput, money, monthLabel, parseAmount, shortDate, thisMonth, today } from "../lib/format";
import { useCards, useInvalidate, useTransactions } from "../lib/queries";

export default function CardDetail() {
  const { id } = useParams();
  const { data: cards, isLoading, isError, error, refetch } = useCards();
  const card = cards?.find((c) => c.id === Number(id));
  const nav = useNavigate();
  const { editTx } = useAdd();
  const invalidate = useInvalidate();
  const [editing, setEditing] = useState(false);
  const [stmtOpen, setStmtOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [payFor, setPayFor] = useState<{ statement: Statement | null } | null>(null);
  const txs = useTransactions({ card_id: Number(id), limit: 30 }).data ?? [];

  if (isError) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (isLoading) return <div className="flex justify-center py-20"><Spinner /></div>;
  if (!card) return <PageHeader title="Card not found" left={<TextButton onClick={() => nav("/cards")}>‹ Cards</TextButton>} />;

  async function deletePayment(p: Payment) {
    if (!confirm(`Delete the ${money(p.amount, p.currency)} payment? It will be added back to the card balance.`)) return;
    await api.del(`cards/payments/${p.id}`);
    invalidate();
    toast("Payment deleted");
  }
  async function deletePlan(planId: number) {
    if (!confirm("Delete this installment plan?")) return;
    await api.del(`cards/installments/${planId}`);
    invalidate();
  }

  const spend = Object.entries(card.period_spend) as [Currency, number][];
  const schedule = card.schedule.filter((s) => Object.keys(s.totals).length);

  return (
    <div>
      <PageHeader
        title={card.name}
        left={
          <TextButton onClick={() => nav("/cards")}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> Cards
          </TextButton>
        }
        right={<TextButton onClick={() => setEditing(true)}>Edit</TextButton>}
      />
      <div className="mx-4 mt-3">
        <CardTile card={card} />
      </div>

      <Section>
        {card.limit !== null && (
          <Row>
            <span className="flex-1 text-[17px]">Available credit</span>
            <span className="tabular text-[17px] text-label-2">{money(card.available ?? 0)}</span>
          </Row>
        )}
        <Row>
          <span className="flex-1 text-[17px]">Spending this period</span>
          <span className="tabular text-right text-[17px] text-label-2">
            {spend.length ? spend.map(([c, v]) => <div key={c}>{money(v, c)}</div>) : money(0)}
          </span>
        </Row>
        <Row>
          <span className="flex-1 text-[17px]">Next statement date</span>
          <span className="text-[17px] text-label-2">{shortDate(card.next_statement_date)}</span>
        </Row>
        <Row>
          <span className="flex-1 text-[17px]">Next due date</span>
          <span className="text-[17px] text-label-2">{shortDate(card.unpaid_statement?.due_date ?? card.next_due_date)}</span>
        </Row>
      </Section>
      <p className="mx-8 mt-1.5 text-[13px] text-label-2">
        Dates are calculated each month from the card settings (day {card.statement_day} / day {card.due_day} of the month); a due date
        falling on a weekend is moved to the next business day. When you upload a statement, its actual dates are used.
      </p>

      <Section title="Statements" action={<button className="text-[15px] text-accent" onClick={() => setStmtOpen(true)}>Add</button>}>
        {card.statements.length === 0 ? (
          <div className="px-4 py-3 text-[15px] text-label-2">Upload a statement or add one manually.</div>
        ) : (
          card.statements.map((s) => {
            const settled = statementSettled(s);
            const paidSoFar = amountEntries(statementPaid(s), s.currency);
            return (
            <Row key={s.id}>
              <div className="min-w-0 flex-1">
                <div className="text-[17px]">{monthLabel(s.period_end.slice(0, 7))}</div>
                <div className="text-[13px] text-label-2">
                  Due {shortDate(s.due_date)}
                  {s.min_payment > 0 && ` · minimum ${money(s.min_payment, s.currency || BASE, true)}`}
                  {paidSoFar.length > 0 && !settled && (
                    <span className="text-orange">
                      {" "}
                      · paid <AmountsList amounts={statementPaid(s)} first={s.currency} compact inline />, remaining{" "}
                      <AmountsList amounts={s.remaining} first={s.currency} compact inline />
                    </span>
                  )}
                  {s.document_id && (
                    <>
                      {" · "}
                      <a href={fileUrl(s.document_id)} target="_blank" rel="noreferrer" className="text-accent">document</a>
                    </>
                  )}
                </div>
              </div>
              <div className="tabular text-right text-[15px]">
                <AmountsList amounts={s.totals} first={s.currency} empty={money(0, s.currency || BASE)} />
              </div>
              {settled ? (
                <span className="flex h-8 shrink-0 items-center rounded-full bg-green/15 px-3 text-[13px] font-semibold text-green">Paid</span>
              ) : (
                <button
                  type="button"
                  onClick={() => setPayFor({ statement: s })}
                  className="flex h-8 shrink-0 items-center rounded-full bg-accent px-3 text-[13px] font-semibold text-white active:opacity-70"
                >
                  Pay
                </button>
              )}
            </Row>
            );
          })
        )}
      </Section>

      <Section
        title="Payments"
        action={<button className="text-[15px] text-accent" onClick={() => setPayFor({ statement: null })}>Add</button>}
        footer="Card payments are not counted as expenses (they are transfers from your account to the card); they reduce the card balance."
      >
        {card.payments.length === 0 ? (
          <div className="px-4 py-3 text-[15px] text-label-2">No payments recorded yet.</div>
        ) : (
          card.payments.map((p) => (
            <Row key={p.id}>
              <div className="min-w-0 flex-1">
                <div className="tabular text-[17px]">{money(p.amount, p.currency)}</div>
                <div className="truncate text-[13px] text-label-2">
                  {shortDate(p.date)}
                  {p.statement_id ? ` · ${monthLabel(card.statements.find((x) => x.id === p.statement_id)?.period_end.slice(0, 7) ?? "")} statement` : ""}
                  {p.note ? ` · ${p.note}` : ""}
                </div>
              </div>
              <IconButton name="trash" label="Delete payment" className="!text-label-3" onClick={() => deletePayment(p)} />
            </Row>
          ))
        )}
      </Section>

      <Section title="Installments" action={<button className="text-[15px] text-accent" onClick={() => setPlanOpen(true)}>Add</button>}>
        {card.installments.length === 0 ? (
          <div className="px-4 py-3 text-[15px] text-label-2">No active installments.</div>
        ) : (
          card.installments.map((p) => (
            <Row key={p.id}>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[17px]">{p.description}</div>
                <div className="text-[13px] text-label-2">
                  {p.count - p.remaining}/{p.count} paid · {p.remaining} months left
                </div>
              </div>
              <div className="tabular text-right">
                <div className="text-[17px]">{money(p.monthly, p.currency)}</div>
                <div className="text-[12px] text-label-2">{money(p.remaining_total, p.currency, true)} left</div>
              </div>
              <IconButton name="trash" label="Delete" className="!text-label-3" onClick={() => deletePlan(p.id)} />
            </Row>
          ))
        )}
      </Section>

      {schedule.length > 0 && (
        <Section title="Upcoming months">
          <div className="no-scrollbar flex gap-2 overflow-x-auto p-3">
            {schedule.map((s) => (
              <div key={s.month} className="w-[92px] shrink-0 rounded-xl bg-card-2 p-2.5">
                <div className="text-[12px] text-label-2">{monthLabel(s.month, true)}</div>
                {(Object.entries(s.totals) as [Currency, number][]).map(([c, v]) => (
                  <div key={c} className="tabular truncate text-[15px] font-semibold">
                    {money(v, c, true)}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="Card transactions">
        {txs.length ? txs.map((t) => <TxRow key={t.id} tx={t} onClick={() => editTx(t)} />) : <div className="px-4 py-3 text-[15px] text-label-2">No transactions.</div>}
      </Section>

      {editing && <CardForm open card={card} onClose={() => setEditing(false)} />}
      {stmtOpen && <StatementSheet card={card} onClose={() => setStmtOpen(false)} />}
      {planOpen && <PlanSheet card={card} onClose={() => setPlanOpen(false)} />}
      {payFor && <PaymentSheet card={card} statement={payFor.statement} onClose={() => setPayFor(null)} />}
    </div>
  );
}

function StatementSheet({ card, onClose }: { card: Card; onClose: () => void }) {
  const invalidate = useInvalidate();
  const [f, setF] = useState({ period_end: today(), due_date: today(), min_payment: "", paid: false });
  const [totals, setTotals] = useState<AmountRow[]>(() => amountRows({}, amountEntries(card.debts, BASE)[0]?.[0] ?? BASE));
  const set = (k: keyof typeof f, v: string | boolean) => setF((x) => ({ ...x, [k]: v }));
  const n = (s: string) => (s.trim() ? parseAmount(s) || 0 : 0);
  // Primary currency = first line (the minimum payment is in this currency)
  const currency = totals[0]?.currency ?? BASE;
  async function save() {
    if (totals.some((r) => r.text.trim() && Number.isNaN(parseAmount(r.text)))) return toast("Enter a valid amount", "err");
    try {
      await api.post(`cards/${card.id}/statements`, {
        ...f,
        currency,
        totals: rowsToAmounts(totals, parseAmount),
        min_payment: n(f.min_payment),
      });
      invalidate();
      toast("Statement added");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  return (
    <Sheet open onClose={onClose} title="Add Statement" right={<TextButton onClick={save}>Save</TextButton>}>
      <Section>
        <Field label="Statement date">
          <input type="date" className={dateCls} value={f.period_end} onChange={(e) => set("period_end", e.target.value)} />
        </Field>
        <Field label="Due date">
          <input type="date" className={dateCls} value={f.due_date} onChange={(e) => set("due_date", e.target.value)} />
        </Field>
      </Section>
      <Section title="Amounts" footer="Add a line for each currency on the statement. The first line is the statement's main currency.">
        <CurrencyAmountsEditor rows={totals} onChange={setTotals} label="Balance" />
        <Field label={`Minimum payment (${currency})`}>
          <input className={inputCls} inputMode="decimal" placeholder="0" value={f.min_payment} onChange={(e) => set("min_payment", e.target.value)} />
        </Field>
        <Field label="Paid">
          <Toggle checked={f.paid} onChange={(v) => set("paid", v)} />
        </Field>
      </Section>
      <div className="mx-4 mt-6">
        <PrimaryButton onClick={save}>Save</PrimaryButton>
      </div>
    </Sheet>
  );
}

function PlanSheet({ card, onClose }: { card: Card; onClose: () => void }) {
  const invalidate = useInvalidate();
  const [desc, setDesc] = useState("");
  const [monthly, setMonthly] = useState("");
  const [currency, setCurrency] = useState<Currency>(BASE);
  const [count, setCount] = useState(6);
  const [first, setFirst] = useState(thisMonth());
  async function save() {
    const m = parseAmount(monthly);
    if (!desc.trim() || !m) return toast("Description and monthly amount are required", "err");
    try {
      await api.post("cards/installments", { card_id: card.id, description: desc, monthly: m, currency, count, first_month: first });
      invalidate();
      toast("Installment added");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  return (
    <Sheet open onClose={onClose} title="Add Installment" right={<TextButton onClick={save}>Save</TextButton>}>
      <Section footer="For adding installments that started earlier to the card load. Enter new installment purchases by choosing 'Installments' when adding a transaction.">
        <Field label="Description">
          <input className={inputCls} placeholder="e.g. Phone" value={desc} onChange={(e) => setDesc(e.target.value)} />
        </Field>
        <Field label="Monthly amount">
          <input className={inputCls} inputMode="decimal" placeholder="0" value={monthly} onChange={(e) => setMonthly(e.target.value)} />
        </Field>
        <Field label="Currency">
          <CurrencyPicker value={currency} onChange={setCurrency} />
        </Field>
        <Field label="Number of installments">
          <Select value={count} onChange={setCount} options={[2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 18, 24, 36].map((n) => ({ value: n, label: `${n} months` }))} />
        </Field>
        <Field label="First installment month">
          <input type="month" className={dateCls} value={first} onChange={(e) => setFirst(e.target.value)} />
        </Field>
      </Section>
      <div className="mx-4 mt-6">
        <PrimaryButton onClick={save}>Save</PrimaryButton>
      </div>
    </Sheet>
  );
}

function PaymentSheet({ card, statement, onClose }: { card: Card; statement: Statement | null; onClose: () => void }) {
  const invalidate = useInvalidate();
  // What is left, per currency: the statement's remaining amounts, or the card's current balances
  const owed = statement ? statement.remaining : card.debts;
  const primary: Currency = statement?.currency || amountEntries(owed, BASE)[0]?.[0] || BASE;
  const owedEntries = amountEntries(owed, primary).filter(([, v]) => v > 0);
  const left = (c: Currency) => Math.max(0, owed?.[c] ?? 0);
  const minLeft = statement ? Math.max(0, statement.min_payment - (statementPaid(statement)[primary] ?? 0)) : 0;
  const [preset, setPreset] = useState<"all" | "min" | "other">("all");
  const [currency, setCurrency] = useState<Currency>(primary);
  const remaining = left(currency);
  const choices = [...new Set([primary, ...owedEntries.map(([c]) => c)])];
  const [amount, setAmount] = useState(amountInput(left(primary)));
  const [date, setDate] = useState(today());
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  function choose(p: "all" | "min" | "other") {
    setPreset(p);
    if (p === "all") setAmount(amountInput(remaining));
    if (p === "min") {
      setCurrency(primary);
      setAmount(amountInput(minLeft));
    }
    if (p === "other") setAmount("");
  }

  async function save() {
    const value = parseAmount(amount);
    if (!value || value <= 0) return toast("Enter a valid amount", "err");
    if (busy) return;
    setBusy(true);
    try {
      await api.post(`cards/${card.id}/payments`, { amount: value, currency, date, statement_id: statement?.id ?? null, note });
      invalidate();
      toast("Payment recorded");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open onClose={onClose} title="Record Payment" right={<TextButton onClick={save} disabled={busy}>Save</TextButton>}>
      <p className="mx-8 mt-1 text-[13px] text-label-2">
        Record the card payment you made from your bank here; the app does not make payments.
        {statement ? ` Remaining on the ${monthLabel(statement.period_end.slice(0, 7))} statement: ` : " Current balance: "}
        {owedEntries.length ? owedEntries.map(([c, v]) => money(v, c)).join(" · ") : money(0, primary)}.
      </p>
      {statement && (
        <div className="mx-4 mt-3">
          <Segmented
            value={preset}
            onChange={choose}
            options={[
              { value: "all", label: "Full" },
              ...(minLeft > 0 && currency === primary ? [{ value: "min" as const, label: "Minimum" }] : []),
              { value: "other", label: "Other amount" },
            ]}
          />
        </div>
      )}
      <Section>
        <Field label="Currency">
          <CurrencyPicker
            value={currency}
            options={choices.length > 1 ? choices : undefined}
            onChange={(c) => {
              setCurrency(c);
              // Paying another currency: prefill what is left in it
              setPreset(left(c) > 0 ? "all" : "other");
              setAmount(left(c) > 0 ? amountInput(left(c)) : "");
            }}
          />
        </Field>
        <Field label="Amount">
          <input className={inputCls} inputMode="decimal" placeholder="0" value={amount} onChange={(e) => (setAmount(e.target.value), setPreset("other"))} />
        </Field>
        <Field label="Payment date">
          <input type="date" className={dateCls} value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label="Note">
          <input className={inputCls} placeholder="Optional" value={note} onChange={(e) => setNote(e.target.value)} />
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
