import { useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Currency } from "../lib/api";
import type { Account, Goal, Savings as SavingsData } from "../lib/savings";
import { BASE, LOCALE, amountInput, money, monthLabel, parseAmount, shortDate, today, uid } from "../lib/format";
import { useInvalidate, useLookups } from "../lib/queries";
import { useSettings } from "../lib/settings";
import {
  Empty,
  ErrorState,
  Field,
  FxWarning,
  PageHeader,
  PrimaryButton,
  Progress,
  Row,
  Section,
  Segmented,
  Select,
  Sheet,
  Spinner,
  TextButton,
  dateCls,
  inputCls,
  toast,
} from "../components/ui";

const kinds = { cash: "Cash", bank: "Bank", savings: "Savings" };
const movementNames: Record<string, string> = {
  transfer: "Transfer",
  deposit: "Deposit",
  withdrawal: "Withdrawal",
  adjustment: "Balance adjustment",
};
/** Enabled currencies from settings (base first), plus the current value if it is no longer enabled. */
function useCurrencyOptions(current: Currency) {
  const { data: settings } = useSettings();
  const list = [...(settings?.currencies ?? [BASE])];
  if (!list.includes(current)) list.push(current);
  return list.map((value) => ({ value: value as Currency, label: value }));
}
const compact = (n: number) =>
  Math.abs(n) >= 1000 ? `${(n / 1000).toLocaleString(LOCALE, { maximumFractionDigits: 1 })}k` : String(n);
const chartStyle = { background: "var(--card)", color: "var(--label)", border: "1px solid var(--sep)", borderRadius: 12 };

export default function Savings() {
  const nav = useNavigate();
  const invalidate = useInvalidate();
  const { userById } = useLookups();
  const query = useQuery({ queryKey: ["savings"], queryFn: () => api.get<SavingsData>("savings") });
  const [tab, setTab] = useState<"overview" | "accounts" | "goals" | "history">("overview");
  const [form, setForm] = useState<"account" | "movement" | "goal" | "allocation" | null>(null);
  const [selectedAccount, setSelectedAccount] = useState<Account>();
  const [selectedGoal, setSelectedGoal] = useState<Goal>();
  const [archived, setArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const s = query.data;
  const close = () => {
    setForm(null);
    setSelectedAccount(undefined);
    setSelectedGoal(undefined);
  };
  async function remove(id: number) {
    if (busy || !confirm("Delete this account movement? Balances and goal allocations will be rechecked.")) return;
    setBusy(true);
    try {
      await api.del(`savings/movements/${id}`);
      invalidate();
      toast("Movement deleted");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <PageHeader
        title="Savings"
        left={<TextButton onClick={() => nav("/more")}>‹ More</TextButton>}
        sub="Accounts, transfers and goals"
      />
      <div className="mx-4 mt-3">
        <Segmented
          value={tab}
          onChange={setTab}
          options={[
            { value: "overview", label: "Overview" },
            { value: "accounts", label: "Accounts" },
            { value: "goals", label: "Goals" },
            { value: "history", label: "Activity" },
          ]}
        />
      </div>
      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !s ? (
        <div className="p-10 text-center">
          <Spinner />
        </div>
      ) : (
        <>
          <FxWarning missing={s.fx_missing} currenciesOnly />
          {tab === "overview" && (
            <>
              <div className="mx-4 mt-4 grid grid-cols-2 gap-3">
                <Metric label="Total savings" value={money(s.savings)} />
                <Metric label="Net contribution this month" value={money(s.month_contribution)} />
                <Metric label="Tracked net worth" value={money(s.net_worth)} />
                <button type="button" onClick={() => nav("/investments")} className="text-left active:opacity-70">
                  <Metric label="Investments ›" value={money(s.investments)} />
                </button>
                <Metric label="Emergency coverage" value={s.emergency_months === null ? "—" : `${s.emergency_months} mo`} />
              </div>
              <p className="mx-5 mt-2 text-[13px] text-label-2">
                Net worth: tracked accounts + investments (current value) − recorded card debt. Other debts are not included. Emergency coverage is based on the
                average of the last 3 full months of the essential categories you marked.
              </p>
              <Section title="By currency" footer="Allocating to a goal is not a money transfer; the same money cannot be allocated to more than one goal.">
                {(Object.entries(s.by_currency) as [Currency, { balance: number; allocated: number; available: number }][]).map(
                  ([c, v]) => (
                    <Row key={c}>
                      <div className="flex-1">
                        <b>{money(v.balance, c)}</b>
                        <div className="text-[13px] text-label-2">
                          Allocated to goals {money(v.allocated, c)} · available {money(v.available, c)}
                        </div>
                      </div>
                    </Row>
                  ),
                )}
                {!Object.keys(s.by_currency).length && (
                  <Empty icon="target" title="Add your first savings account" text="Enter your starting balance in the Accounts tab." />
                )}
              </Section>
              <Section
                title="Savings balance · 12 months"
                footer="Opening balances and adjustments do not count as contributions. Value change shows exchange-rate and conversion effects separately from contributions."
              >
                <div className="h-56 p-2">
                  <ResponsiveContainer>
                    <LineChart data={s.series}>
                      <CartesianGrid stroke="var(--sep)" vertical={false} />
                      <XAxis dataKey="month" tickFormatter={(v) => monthLabel(v, true)} tick={{ fontSize: 10 }} interval={2} />
                      <YAxis width={50} tickFormatter={compact} tick={{ fontSize: 11 }} />
                      <Tooltip contentStyle={chartStyle} formatter={(v: number) => [money(v), "Balance"]} />
                      <Line dataKey="balance" stroke="var(--accent)" dot={false} strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </Section>
              <Section title="Monthly net contribution">
                <div className="h-44 p-2">
                  <ResponsiveContainer>
                    <BarChart data={s.series}>
                      <XAxis dataKey="month" tickFormatter={(v) => monthLabel(v, true)} tick={{ fontSize: 10 }} interval={2} />
                      <YAxis width={50} tickFormatter={compact} tick={{ fontSize: 11 }} />
                      <Tooltip contentStyle={chartStyle} formatter={(v: number) => [money(v), "Net contribution"]} />
                      <Bar dataKey="contribution" fill="var(--accent)" radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                {s.series.slice(-3).map((x) => (
                  <Row key={x.month}>
                    <div className="flex-1 text-[13px]">
                      <b>{monthLabel(x.month)}</b>
                      <div>
                        In {money(x.deposit)} · out {money(x.withdrawal)}
                      </div>
                      <div className="text-label-2">
                        Opening/adjustment {money(x.adjustment)} · value change {money(x.valuation_change)}
                      </div>
                    </div>
                  </Row>
                ))}
              </Section>
            </>
          )}
          {tab === "accounts" && (
            <>
              <div className="mx-4 mt-4 flex gap-3">
                <PrimaryButton onClick={() => setForm("account")}>Add account</PrimaryButton>
                <PrimaryButton tone="plain" onClick={() => setForm("movement")}>
                  Add movement
                </PrimaryButton>
              </div>
              <p className="mx-5 mt-3 text-[13px] text-label-2">
                Balances are tracked from the opening amount and the movements you add here. Spending records do not change these accounts
                automatically. You can track income/expenses separately under Transactions.
              </p>
              <label className="mx-4 mt-3 flex gap-2 text-[13px]">
                <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Show archived
              </label>
              <Section>
                {s.accounts
                  .filter((a) => archived || !a.archived)
                  .map((a) => (
                    <Row
                      key={a.id}
                      chevron
                      onClick={() => {
                        setSelectedAccount(a);
                        setForm("account");
                      }}
                    >
                      <div className="min-w-0 flex-1">
                        <div>
                          {a.name}
                          {a.archived ? " · archived" : ""}
                        </div>
                        <div className="text-[13px] text-label-2">
                          {kinds[a.kind]} · {a.owner_id ? userById.get(a.owner_id)?.name : "Shared"} · last activity {shortDate(a.updated_on)}
                        </div>
                        <div className="text-[13px] text-label-2">Available {money(a.available, a.currency)}</div>
                      </div>
                      <b>{money(a.balance, a.currency)}</b>
                    </Row>
                  ))}
                {!s.accounts.length && <Empty icon="card" title="No accounts" />}
              </Section>
            </>
          )}
          {tab === "goals" && (
            <>
              <div className="mx-4 mt-4">
                <PrimaryButton onClick={() => setForm("goal")}>Add goal</PrimaryButton>
              </div>
              <label className="mx-4 mt-3 flex gap-2 text-[13px]">
                <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Show archived
              </label>
              {s.goals
                .filter((g) => archived || !g.archived)
                .map((g) => (
                  <Section
                    key={g.id}
                    title={g.name}
                    action={
                      <TextButton
                        onClick={() => {
                          setSelectedGoal(g);
                          setForm("goal");
                        }}
                      >
                        Edit
                      </TextButton>
                    }
                  >
                    <div className="space-y-2 p-4">
                      <div className="flex flex-wrap justify-between">
                        <b>{money(g.funded, g.currency)}</b>
                        <span className="text-label-2">/ {money(g.target, g.currency)}</span>
                      </div>
                      <Progress ratio={g.funded / g.target} />
                      {g.invested > 0 && (
                        <p className="text-[13px] text-label-2">
                          Allocated from accounts {money(g.allocated, g.currency)} · linked investments (current value) {money(g.invested, g.currency)}
                        </p>
                      )}
                      <p className="text-[13px] text-label-2">
                        {g.owner_id ? userById.get(g.owner_id)?.name : "Shared goal"}
                        {g.emergency ? " · Emergency" : ""}
                        {g.target_date ? ` · ${shortDate(g.target_date)}` : ""}
                      </p>
                      <p className="text-[13px]">
                        Remaining {money(g.remaining, g.currency)}
                        {g.required_monthly !== null && ` · required monthly ${money(g.required_monthly, g.currency)}`}
                      </p>
                      {g.overdue && <p className="text-[13px] text-red">Target date has passed; update the plan.</p>}
                      <p className="text-[13px] text-label-2">
                        Monthly plan {money(g.monthly, g.currency)} · allocated this month {money(g.month_allocated, g.currency)}. The plan is not
                        added to the balance automatically.
                      </p>
                      {g.accounts.map((a) => (
                        <div className="text-[13px]" key={a.account_id}>
                          {s.accounts.find((x) => x.id === a.account_id)?.name}: {money(a.amount, g.currency)}
                        </div>
                      ))}
                      {g.contributions.map((c) => (
                        <div className="text-[13px] text-label-2" key={c.user_id}>
                          {userById.get(c.user_id)?.name ?? "User"} · net allocation {money(c.amount, g.currency)}
                        </div>
                      ))}
                      {!g.archived && (
                        <PrimaryButton
                          tone="plain"
                          onClick={() => {
                            setSelectedGoal(g);
                            setForm("allocation");
                          }}
                        >
                          Allocate / release funds
                        </PrimaryButton>
                      )}
                    </div>
                  </Section>
                ))}
              {!s.goals.length && <Empty icon="target" title="No goals" text="Create a goal for a vacation, a home or an emergency fund." />}
            </>
          )}
          {tab === "history" && (
            <>
              <div className="mx-4 mt-4">
                <PrimaryButton onClick={() => setForm("movement")}>Add movement</PrimaryButton>
              </div>
              <Section title="Last 100 movements">
                {s.movements.map((m) => {
                  const source = s.accounts.find((a) => a.id === m.source_id),
                    target = s.accounts.find((a) => a.id === m.target_id);
                  return (
                    <Row key={m.id}>
                      <div className="min-w-0 flex-1">
                        <div>
                          {source?.name ?? "External"} → {target?.name ?? "External"}
                        </div>
                        <div className="text-[13px] text-label-2">
                          {shortDate(m.date)} · {movementNames[m.kind]} · {userById.get(m.user_id)?.name}
                        </div>
                        <div className="text-[13px]">
                          {source && money(m.source_amount, source.currency)}
                          {source && target && " → "}
                          {target && money(m.target_amount, target.currency)}
                        </div>
                        <div className="text-[13px] text-label-2">{m.note}</div>
                      </div>
                      <TextButton disabled={busy} onClick={() => remove(m.id)}>
                        Delete
                      </TextButton>
                    </Row>
                  );
                })}
                {!s.movements.length && <Empty icon="list" title="No movements yet" />}
              </Section>
            </>
          )}
          {form === "account" && <AccountForm account={selectedAccount} onClose={close} />}
          {form === "goal" && <GoalForm goal={selectedGoal} onClose={close} />}
          {form === "movement" && <MovementForm accounts={s.accounts.filter((a) => !a.archived)} onClose={close} />}
          {form === "allocation" && selectedGoal && (
            <AllocationForm
              goal={selectedGoal}
              accounts={s.accounts.filter((a) => !a.archived && a.kind === "savings" && a.currency === selectedGoal.currency)}
              onClose={close}
            />
          )}
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-card p-3">
      <div className="text-[12px] text-label-2">{label}</div>
      <div className="tabular break-words text-[20px] font-semibold">{value}</div>
    </div>
  );
}

function Editor({
  title,
  onClose,
  save,
  children,
}: {
  title: string;
  onClose: () => void;
  save: () => Promise<unknown>;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const invalidate = useInvalidate();
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    try {
      await save();
      invalidate();
      toast("Saved");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <Sheet open onClose={onClose} title={title}>
      <form onSubmit={submit}>
        <fieldset disabled={busy}>
          {children}
          <div className="m-4">
            <PrimaryButton type="submit" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </PrimaryButton>
          </div>
        </fieldset>
      </form>
    </Sheet>
  );
}
function Owner({ value, set }: { value: number; set: (v: number) => void }) {
  const { me } = useLookups();
  return (
    <Field label="Owner">
      <Select
        value={value}
        onChange={set}
        options={[{ value: 0, label: "Shared" }, ...(me?.users ?? []).map((u) => ({ value: u.id, label: u.name }))]}
      />
    </Field>
  );
}
function Amount({ label, value, set, required = false }: { label: string; value: string; set: (v: string) => void; required?: boolean }) {
  return (
    <Field label={label}>
      <input className={inputCls} inputMode="decimal" value={value} required={required} onChange={(e) => set(e.target.value)} />
    </Field>
  );
}
function number(s: string) {
  const n = parseAmount(s);
  if (!Number.isFinite(n)) throw new Error("Enter a valid amount");
  return n;
}

function AccountForm({ account: a, onClose }: { account?: Account; onClose: () => void }) {
  const [name, setName] = useState(a?.name ?? "");
  const [kind, setKind] = useState<Account["kind"]>(a?.kind ?? "savings");
  const [currency, setCurrency] = useState<Currency>(a?.currency ?? BASE);
  const currencies = useCurrencyOptions(currency);
  const [amount, setAmount] = useState(amountInput(a?.opening_balance ?? 0));
  const [date, setDate] = useState(a?.opening_date ?? today());
  const [owner, setOwner] = useState(a?.owner_id ?? 0);
  const [archived, setArchived] = useState(a?.archived ?? false);
  return (
    <Editor
      title={a ? "Edit account" : "New account"}
      onClose={onClose}
      save={() => {
        const body = { name, kind, currency, opening_balance: number(amount), opening_date: date, owner_id: owner || null, archived };
        return a ? api.put(`savings/accounts/${a.id}`, body) : api.post("savings/accounts", body);
      }}
    >
      <Section
        footer={
          a
            ? "Opening details are fixed. Add a movement to change the balance."
            : "Enter the actual balance as of this date. Past spending is not deducted automatically."
        }
      >
        <Field label="Name">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
        </Field>
        {!a ? (
          <>
            <Field label="Type">
              <Select
                value={kind}
                onChange={setKind}
                options={Object.entries(kinds).map(([value, label]) => ({ value: value as Account["kind"], label }))}
              />
            </Field>
            <Field label="Currency">
              <Select value={currency} onChange={setCurrency} options={currencies} />
            </Field>
            <Amount label="Opening balance" value={amount} set={setAmount} required />
            <Field label="Opening date">
              <input type="date" className={dateCls} value={date} max={today()} required onChange={(e) => setDate(e.target.value)} />
            </Field>
          </>
        ) : (
          <Row>
            {kinds[kind]} · {currency} · opening {money(a.opening_balance, currency)}
          </Row>
        )}
        <Owner value={owner} set={setOwner} />
        {a && (
          <Field label="Archive">
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
          </Field>
        )}
      </Section>
    </Editor>
  );
}
function GoalForm({ goal: g, onClose }: { goal?: Goal; onClose: () => void }) {
  const [name, setName] = useState(g?.name ?? "");
  const [currency, setCurrency] = useState<Currency>(g?.currency ?? BASE);
  const currencies = useCurrencyOptions(currency);
  const [target, setTarget] = useState(amountInput(g?.target));
  const [monthly, setMonthly] = useState(amountInput(g?.monthly ?? 0));
  const [date, setDate] = useState(g?.target_date ?? "");
  const [reminder, setReminder] = useState(g?.reminder_day ?? 0);
  const [owner, setOwner] = useState(g?.owner_id ?? 0);
  const [emergency, setEmergency] = useState(g?.emergency ?? false);
  const [archived, setArchived] = useState(g?.archived ?? false);
  return (
    <Editor
      title={g ? "Edit goal" : "New goal"}
      onClose={onClose}
      save={() => {
        const body = {
          name,
          currency,
          target: number(target),
          monthly: number(monthly),
          target_date: date || null,
          reminder_day: reminder || null,
          owner_id: owner || null,
          emergency,
          archived,
        };
        return g ? api.put(`savings/goals/${g.id}`, body) : api.post("savings/goals", body);
      }}
    >
      <Section footer="The contribution plan is for reminders only; it does not create money movements automatically.">
        <Field label="Name">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
        </Field>
        <Field label="Currency">
          <Select value={currency} onChange={setCurrency} options={currencies} />
        </Field>
        <Amount label="Target amount" value={target} set={setTarget} required />
        <Field label="Target date">
          <input type="date" className={dateCls} value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Amount label="Monthly contribution plan" value={monthly} set={setMonthly} required />
        <Field label="Reminder day">
          <Select
            value={reminder}
            onChange={setReminder}
            options={[{ value: 0, label: "Off" }, ...Array.from({ length: 31 }, (_, i) => ({ value: i + 1, label: String(i + 1) }))]}
          />
        </Field>
        <Owner value={owner} set={setOwner} />
        <Field label="Emergency fund">
          <input type="checkbox" checked={emergency} onChange={(e) => setEmergency(e.target.checked)} />
        </Field>
        {g && (
          <Field label="Archive">
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
          </Field>
        )}
      </Section>
    </Editor>
  );
}
function MovementForm({ accounts, onClose }: { accounts: Account[]; onClose: () => void }) {
  const [kind, setKind] = useState("transfer");
  const [direction, setDirection] = useState("in");
  const [source, setSource] = useState(0);
  const [target, setTarget] = useState(0);
  const [amount, setAmount] = useState("");
  const [received, setReceived] = useState("");
  const [date, setDate] = useState(today());
  const [note, setNote] = useState("");
  const ref = useRef(uid());
  const sourceOn = kind === "transfer" || kind === "withdrawal" || (kind === "adjustment" && direction === "out");
  const targetOn = kind === "transfer" || kind === "deposit" || (kind === "adjustment" && direction === "in");
  const a = accounts.find((a) => a.id === source),
    b = accounts.find((a) => a.id === target);
  const cross = kind === "transfer" && a && b && a.currency !== b.currency;
  const options = [{ value: 0, label: "Select account" }, ...accounts.map((a) => ({ value: a.id, label: `${a.name} (${a.currency})` }))];
  return (
    <Editor
      title="Account movement"
      onClose={onClose}
      save={() =>
        api.post("savings/movements", {
          kind,
          source_id: sourceOn ? source || null : null,
          target_id: targetOn ? target || null : null,
          amount: number(amount),
          received_amount: cross ? number(received) : null,
          date,
          note,
          client_ref: ref.current,
        })
      }
    >
      <Section footer="This movement is not included in income/expense reports. Balance adjustments do not count as savings contributions.">
        <Field label="Action">
          <Select value={kind} onChange={setKind} options={Object.entries(movementNames).map(([value, label]) => ({ value, label }))} />
        </Field>
        {kind === "adjustment" && (
          <Field label="Direction">
            <Select
              value={direction}
              onChange={setDirection}
              options={[
                { value: "in", label: "Increase balance" },
                { value: "out", label: "Decrease balance" },
              ]}
            />
          </Field>
        )}
        {sourceOn && (
          <Field label="Source">
            <Select value={source} onChange={setSource} options={options} />
          </Field>
        )}
        {targetOn && (
          <Field label="Destination">
            <Select value={target} onChange={setTarget} options={options} />
          </Field>
        )}
        <Amount label={`Amount ${sourceOn ? (a?.currency ?? "") : (b?.currency ?? "")}`} value={amount} set={setAmount} required />
        {cross && <Amount label={`Amount received ${b.currency}`} value={received} set={setReceived} required />}
        <Field label="Date">
          <input type="date" className={dateCls} value={date} max={today()} onChange={(e) => setDate(e.target.value)} required />
        </Field>
        <Field label="Note">
          <input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
        </Field>
      </Section>
    </Editor>
  );
}
function AllocationForm({ goal, accounts, onClose }: { goal: Goal; accounts: Account[]; onClose: () => void }) {
  const [account, setAccount] = useState(accounts[0]?.id ?? 0);
  const [release, setRelease] = useState("add");
  const [amount, setAmount] = useState("");
  const ref = useRef(uid());
  return (
    <Editor
      title={goal.name}
      onClose={onClose}
      save={() =>
        api.post(`savings/goals/${goal.id}/allocations`, {
          account_id: account,
          amount: Math.abs(number(amount)) * (release === "release" ? -1 : 1),
          client_ref: ref.current,
        })
      }
    >
      <Section footer="Allocating only earmarks existing money for the goal; it does not change the account balance.">
        <Field label="Account">
          <Select
            value={account}
            onChange={setAccount}
            placeholder="Select account"
            options={accounts.map((a) => ({ value: a.id, label: `${a.name} · available ${money(a.available, a.currency)}` }))}
          />
        </Field>
        <Field label="Action">
          <Select
            value={release}
            onChange={setRelease}
            options={[
              { value: "add", label: "Allocate to goal" },
              { value: "release", label: "Release" },
            ]}
          />
        </Field>
        <Amount label={`Amount ${goal.currency}`} value={amount} set={setAmount} required />
        {!accounts.length && <p className="p-4 text-[13px]">First create a savings account in {goal.currency}.</p>}
      </Section>
    </Editor>
  );
}
