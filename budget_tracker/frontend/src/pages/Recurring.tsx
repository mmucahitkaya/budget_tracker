import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CatBadge,
  CurrencyPicker,
  Empty,
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
  TextButton,
  Toggle,
  dateCls,
  inputCls,
  toast,
  ErrorState,
} from "../components/ui";
import {
  api,
  type Currency,
  type Kind,
  type Method,
  type Recurring,
} from "../lib/api";
import {
  BASE,
  LOCALE,
  amountInput,
  dayLabel,
  money,
  monthLabel,
  parseAmount,
  shiftMonth,
  today,
} from "../lib/format";
import { useInvalidate, useLookups, useRecurring } from "../lib/queries";

const MONTHS_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

const FREQ = {
  weekly: "Weekly",
  monthly: "Monthly",
  yearly: "Yearly",
} as const;

export default function RecurringPage({ kind }: { kind: Kind }) {
  const { data: all, isError, error, refetch } = useRecurring();
  const data = all?.filter((r) => r.kind === kind);
  const income = kind === "income";
  const { catById } = useLookups();
  const nav = useNavigate();
  const [edit, setEdit] = useState<Recurring | "new" | "salary" | null>(null);
  const active = (data ?? []).filter((r) => r.active);
  const inactive = (data ?? []).filter((r) => !r.active);
  const monthly = (kind: Kind) =>
    active
      .filter((r) => r.kind === kind && r.currency === BASE)
      .reduce(
        (s, r) =>
          s +
          (r.frequency === "monthly"
            ? r.next_amount
            : r.frequency === "weekly"
              ? (r.amount * 52) / 12
              : r.amount / 12),
        0,
      );

  const list = (rows: Recurring[]) =>
    rows.map((r) => (
      <Row key={r.id} chevron onClick={() => setEdit(r)}>
        <CatBadge cat={r.category_id ? catById.get(r.category_id) : null} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[17px]">{r.name}</div>
          <div className="truncate text-[13px] text-label-2">
            {r.active
              ? `${r.last_business_day ? "Last business day of month" : FREQ[r.frequency]} · next ${dayLabel(r.next_date)}`
              : r.end_date
                ? "Completed"
                : FREQ[r.frequency]}
            {r.auto_create ? "" : " · reminder only"}
            {r.asset_label ? ` · ${r.asset_label}` : ""}
          </div>
          {r.end_date && r.active && (
            <div className="truncate text-[13px] text-label-2">
              Until {monthLabel(r.end_date.slice(0, 7))} · {r.remaining_count}{" "}
              payments left
              {r.remaining_total
                ? ` · ${money(r.remaining_total, r.currency, true)} remaining`
                : ""}
            </div>
          )}
        </div>
        <div
          className={`tabular text-[17px] ${r.kind === "income" ? "text-green" : ""}`}
        >
          {money(r.next_amount, r.currency, true)}
        </div>
      </Row>
    ));

  return (
    <div>
      <PageHeader
        title={income ? "Recurring Income" : "Recurring Expenses"}
        left={
          <TextButton onClick={() => nav("/more")}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> More
          </TextButton>
        }
        right={
          <IconButton name="plus" label="Add" onClick={() => setEdit("new")} />
        }
      />
      {active.length > 0 && (
        <div className="mx-4 mt-4 rounded-xl bg-card p-3">
          <div className="text-[13px] text-label-2">Monthly recurring {income ? "income" : "expenses"} (this month)</div>
          <div className={`tabular text-[20px] font-semibold ${income ? "text-green" : ""}`}>
            {money(monthly(kind), BASE, true)}
          </div>
        </div>
      )}
      {income && (
        <Section footer="Enter your salary once; it is recorded automatically as income on its day each month and shows up in your cash flow. When you get a raise, just edit the amount; past entries stay unchanged.">
          <Row chevron onClick={() => setEdit("salary")}>
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-green/15 text-green">
              <Icon name="plus" size={18} />
            </span>
            <span className="flex-1 text-[17px]">Add salary</span>
          </Row>
        </Section>
      )}
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : !data?.length ? (
        <Empty
          icon="repeat"
          title={income ? "No recurring income" : "No recurring expenses"}
          text={
            income
              ? "Add regular income such as salary or rental income. It is recorded automatically on its due date and shows as planned in future months."
              : "Add regular expenses such as rent, loans, utilities and subscriptions; you can set an end month. They are recorded automatically on their due date and show as planned in future months."
          }
        />
      ) : (
        <>
          <Section title="Active">{list(active)}</Section>
          {inactive.length > 0 && (
            <Section title="Paused / completed">
              {list(inactive)}
            </Section>
          )}
        </>
      )}
      {edit && (
        <RecurringSheet
          rec={edit === "new" || edit === "salary" ? undefined : edit}
          salary={edit === "salary"}
          defaultKind={kind}
          onClose={() => setEdit(null)}
        />
      )}
    </div>
  );
}

function RecurringSheet({
  rec,
  salary,
  defaultKind = "expense",
  onClose,
}: {
  rec?: Recurring;
  salary?: boolean;
  defaultKind?: Kind;
  onClose: () => void;
}) {
  const { cats, cards, me } = useLookups();
  const invalidate = useInvalidate();
  const salaryCat =
    cats.find((c) => c.kind === "income" && c.name === "Salary")?.id ?? null;
  // For a new salary the name is per person: "Alice Salary", "Bob Salary"
  const salaryName = (uid: number | null) => {
    const n = me?.users.find((u) => u.id === uid)?.name ?? "";
    return n ? `${n.charAt(0).toLocaleUpperCase(LOCALE)}${n.slice(1)} Salary` : "Salary";
  };
  const [name, setName] = useState(rec?.name ?? (salary ? salaryName(me?.me.id ?? null) : ""));
  const [kind, setKind] = useState<Kind>(
    rec?.kind ?? (salary ? "income" : defaultKind),
  );
  const [amount, setAmount] = useState(amountInput(rec?.amount));
  const [currency, setCurrency] = useState<Currency>(rec?.currency ?? BASE);
  const [frequency, setFrequency] = useState(rec?.frequency ?? "monthly");
  const [nextDate, setNextDate] = useState(rec?.next_date ?? today());
  const [categoryId, setCategoryId] = useState<number | null>(
    rec?.category_id ?? (salary ? salaryCat : null),
  );
  const [method, setMethod] = useState<Method>(rec?.payment_method ?? "bank");
  const [cardId, setCardId] = useState<number | null>(
    rec?.card_id ?? cards[0]?.id ?? null,
  );
  const [userId, setUserId] = useState<number | null>(
    rec?.user_id ?? me?.me.id ?? null,
  );
  const [auto, setAuto] = useState(rec?.auto_create ?? true);
  const [active, setActive] = useState(rec?.active ?? true);
  const [hasEnd, setHasEnd] = useState(!!rec?.end_date);
  const [endMonth, setEndMonth] = useState(
    rec?.end_date?.slice(0, 7) ?? shiftMonth(nextDate.slice(0, 7), 11),
  );
  const [backfill, setBackfill] = useState(false);
  const [lastBusiness, setLastBusiness] = useState(
    rec?.last_business_day ?? false,
  );
  const [raiseMonths, setRaiseMonths] = useState<number[]>(
    rec?.raise_months ?? [],
  );
  const isPast = nextDate < today();
  // Payment day in the selected end month (capped at month end)
  const endDate = (() => {
    if (!hasEnd || !endMonth) return null;
    const [y, m] = endMonth.split("-").map(Number);
    const day = Math.min(
      Number(nextDate.slice(8, 10)),
      new Date(y, m, 0).getDate(),
    );
    return `${endMonth}-${String(day).padStart(2, "0")}`;
  })();
  const count = (() => {
    if (!endDate || frequency !== "monthly") return null;
    const [y1, m1] = nextDate.split("-").map(Number);
    const [y2, m2] = endDate.split("-").map(Number);
    return (y2 - y1) * 12 + (m2 - m1) + 1;
  })();

  async function save() {
    const value = parseAmount(amount);
    if (!name.trim() || !value) return toast("Name and amount are required", "err");
    const body = {
      name,
      kind,
      amount: value,
      currency,
      frequency,
      next_date: nextDate,
      category_id: categoryId,
      payment_method: method,
      card_id: method === "card" ? cardId : null,
      user_id: userId,
      auto_create: auto,
      active,
      note: "",
      end_date: endDate,
      backfill: !rec && isPast && backfill,
      last_business_day: frequency === "monthly" && lastBusiness,
      raise_months: raiseMonths,
    };
    try {
      if (rec) await api.put(`recurring/${rec.id}`, body);
      else await api.post("recurring", body);
      setTimeout(invalidate, 800); // let back-dated entry creation finish in the background
      invalidate();
      toast("Saved");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  async function remove() {
    if (
      !rec ||
      !confirm(`Delete ${rec.name}? Transactions already created will be kept.`)
    )
      return;
    await api.del(`recurring/${rec.id}`);
    invalidate();
    onClose();
  }

  return (
    <Sheet
      open
      onClose={onClose}
      title={
        salary && !rec
          ? "Salary"
          : `${rec ? "" : "New "}Recurring ${kind === "income" ? "Income" : "Expense"}`
      }
      right={<TextButton onClick={save}>Save</TextButton>}
    >
      <div className="px-4 pt-2">
        <Segmented
          value={kind}
          onChange={(k) => {
            setKind(k);
            setCategoryId(null);
          }}
          options={[
            { value: "expense", label: "Expense" },
            { value: "income", label: "Income" },
          ]}
        />
      </div>
      <Section>
        <Field label="Name">
          <input
            className={inputCls}
            placeholder={kind === "income" ? "e.g. Salary" : "e.g. Rent"}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        {rec?.asset_label && (
          <Field label="Gold">
            <span className="text-[15px] text-label-2">{rec.asset_label} · ≈{money(rec.next_amount, BASE, true)}</span>
          </Field>
        )}
        <Field label={rec?.asset_label ? "Amount if no price" : rec?.schedule.length ? "Default amount" : "Amount"}>
          <input
            className={inputCls}
            inputMode="decimal"
            placeholder="0"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </Field>
        <Field label="Currency">
          <CurrencyPicker value={currency} onChange={setCurrency} />
        </Field>
        <Field label="Category">
          <Select
            value={categoryId}
            placeholder="Select"
            onChange={setCategoryId}
            options={cats
              .filter((c) => c.kind === kind && !c.archived)
              .map((c) => ({ value: c.id, label: `${c.icon} ${c.name}` }))}
          />
        </Field>
      </Section>
      <Section title="Schedule">
        <Field label="Frequency">
          <Select
            value={frequency}
            onChange={setFrequency}
            options={Object.entries(FREQ).map(([v, l]) => ({
              value: v as Recurring["frequency"],
              label: l,
            }))}
          />
        </Field>
        {frequency === "monthly" && (
          <Field label="Last business day of month">
            <Toggle checked={lastBusiness} onChange={setLastBusiness} />
          </Field>
        )}
        <Field
          label={
            frequency === "monthly" && lastBusiness
              ? rec
                ? "Next month"
                : "First month"
              : rec
                ? "Next payment"
                : "First payment"
          }
        >
          <input
            type="date"
            className={dateCls}
            value={nextDate}
            onChange={(e) => setNextDate(e.target.value)}
          />
        </Field>
        <Field label="Has end date">
          <Toggle checked={hasEnd} onChange={setHasEnd} />
        </Field>
        {hasEnd && (
          <Field label="Last payment month">
            <input
              type="month"
              className={dateCls}
              value={endMonth}
              min={nextDate.slice(0, 7)}
              onChange={(e) => setEndMonth(e.target.value)}
            />
          </Field>
        )}
        {!rec && isPast && (
          <Field label="Also add past months">
            <Toggle checked={backfill} onChange={setBackfill} />
          </Field>
        )}
      </Section>
      {(count || (!rec && isPast)) && (
        <p className="mx-8 mt-1.5 text-[13px] text-label-2">
          {count
            ? `${count} payments total${parseAmount(amount) ? ` · ${money(count * parseAmount(amount), currency, true)}` : ""}. `
            : ""}
          {!rec &&
            isPast &&
            (backfill
              ? "Payments from the first payment until today will be added as transactions."
              : "Past payments are not added; only future ones.")}
        </p>
      )}
      {rec && rec.schedule.length > 0 && (
        <Section
          title="Amounts by month"
          footer="This payment's amount varies by month; months not listed use the default amount."
        >
          {rec.schedule.map((s) => (
            <Row key={s.date}>
              <span className="flex-1 text-[15px]">{dayLabel(s.date)}</span>
              <span className="tabular text-[15px]">
                {money(s.amount, rec.currency, true)}
              </span>
            </Row>
          ))}
        </Section>
      )}
      {
        <Section
          title={kind === "income" ? "Raise months" : "Increase months"}
          footer={`On the 1st of the selected months, ${kind === "income" ? "the person gets" : "you get"} a Telegram reminder to update the amount${kind === "expense" ? " (e.g. rent increase)" : ""}.`}
        >
          <div className="grid grid-cols-6 gap-1.5 p-3">
            {MONTHS_SHORT.map((m, i) => {
              const on = raiseMonths.includes(i + 1);
              return (
                <button
                  key={m}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    setRaiseMonths(
                      on
                        ? raiseMonths.filter((x) => x !== i + 1)
                        : [...raiseMonths, i + 1],
                    )
                  }
                  className={`rounded-lg py-1.5 text-[13px] font-medium ${on ? "bg-accent text-white" : "bg-fill text-label"}`}
                >
                  {m}
                </button>
              );
            })}
          </div>
        </Section>
      }
      <Section title="Payment">
        <div className="px-4 py-2">
          <Segmented
            value={method}
            onChange={setMethod}
            options={[
              { value: "bank", label: "Bank" },
              { value: "card", label: "Credit card" },
              { value: "cash", label: "Cash" },
              { value: "voucher", label: "Voucher" },
            ]}
          />
        </div>
        {method === "card" && cards.length > 0 && (
          <Field label="Card">
            <Select
              value={cardId}
              onChange={setCardId}
              options={cards.map((c) => ({ value: c.id, label: c.name }))}
            />
          </Field>
        )}
        {me && me.users.length > 1 && (
          <Field label="Who">
            <Select
              value={userId}
              onChange={(v) => {
                if (!rec && salary && name === salaryName(userId)) setName(salaryName(v));
                setUserId(v);
              }}
              options={me.users.map((u) => ({ value: u.id, label: u.name }))}
            />
          </Field>
        )}
      </Section>
      <Section footer="When on, the transaction is recorded automatically on its due date; when off, only a notification is sent.">
        <Field label="Auto-record">
          <Toggle checked={auto} onChange={setAuto} />
        </Field>
        <Field label="Active">
          <Toggle checked={active} onChange={setActive} />
        </Field>
      </Section>
      <div className="mx-4 mt-6 space-y-3">
        <PrimaryButton onClick={save}>Save</PrimaryButton>
        {rec && (
          <PrimaryButton tone="red" onClick={remove}>
            Delete
          </PrimaryButton>
        )}
      </div>
    </Sheet>
  );
}
