import { useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAdd } from "../App";
import TxRow from "../components/TxRow";
import { CatBadge, Empty, Icon, MonthSwitcher, PageHeader, Row, Section, Spinner, ErrorState } from "../components/ui";
import type { Currency, Tx } from "../lib/api";
import { BASE, dayLabel, money, thisMonth } from "../lib/format";
import { useSettings } from "../lib/settings";
import { useLookups, usePlanned, useTransactions } from "../lib/queries";

function Chip({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-8 shrink-0 rounded-full px-3.5 text-[15px] ${active ? "bg-accent text-white" : "bg-card text-label"}`}
    >
      {children}
    </button>
  );
}

export default function Transactions() {
  const [params, setParams] = useSearchParams();
  const month = params.get("month") || params.get("end")?.slice(0,7) || thisMonth();
  const update = (key: string, value: string | number) => setParams(p => { const n = new URLSearchParams(p); if (value !== "") n.set(key, String(value)); else n.delete(key); return n; }, { replace: true });
  const q = params.get("q") || "", setQ = (v: string) => update("q", v);
  const kind = params.get("kind") || "", setKind = (v: string) => update("kind", v);
  const userId = Number(params.get("user_id")) || "", setUserId = (v: number | "") => update("user_id", v);
  const cardId = Number(params.get("card_id")) || "", setCardId = (v: number | "") => update("card_id", v);
  const currency = params.get("currency") || "", setCurrency = (v: string) => update("currency", v);
  const start = params.get("start") || undefined, end = params.get("end") || undefined;
  const categoryId = params.get("category") ? Number(params.get("category")) : undefined;
  const { cards, me, catById } = useLookups();
  const { data: settings } = useSettings();
  const { editTx } = useAdd();
  const nav = useNavigate();
  const planned = (usePlanned(month).data ?? []).filter(
    (p) => !start && !end && (!kind || p.kind === kind) && (!currency || p.currency === currency) && (!userId || p.user_id === userId) && (!cardId || p.card_id === cardId) && (categoryId === undefined || (p.category_id ?? 0) === categoryId),
  );

  const setMonth = (m: string) => {
    params.delete("start"); params.delete("end");
    params.set("month", m);
    setParams(params, { replace: true });
  };

  const { data, isLoading, isError, error, refetch } = useTransactions({
    month: start || end ? undefined : month,
    start, end, limit: 5000,
    merchant_key: params.get("merchant_key") || undefined,
    spending_type: params.get("spending_type") || undefined,
    q: q.trim() || undefined,
    kind: kind || undefined,
    user_id: userId || undefined,
    card_id: cardId || undefined,
    currency: currency || undefined,
    category_id: categoryId,
  });

  const groups = useMemo(() => {
    const m = new Map<string, Tx[]>();
    for (const t of data ?? []) {
      const g = m.get(t.date) ?? [];
      g.push(t);
      m.set(t.date, g);
    }
    return [...m.entries()];
  }, [data]);

  const totals = useMemo(() => {
    const out: Partial<Record<Currency, number>> = {};
    for (const t of data ?? []) out[t.currency] = (out[t.currency] ?? 0) + (t.kind === "income" ? t.amount : -t.amount);
    return out;
  }, [data]);

  return (
    <div>
      <PageHeader title="Transactions" left={<MonthSwitcher value={month} onChange={setMonth} />} />

      {(start || end || params.get("merchant_key") || params.get("spending_type")) && <div className="mx-4 mt-3 rounded-xl bg-card p-3 text-[13px]">
        {start} – {end} {params.get("merchant_key")} {({ fixed: "Fixed", variable: "Variable", one_off: "One-off" } as Record<string,string>)[params.get("spending_type") || ""]}
        <button className="ml-3 text-accent" onClick={() => { const p = new URLSearchParams(params); ["start","end","merchant_key","spending_type"].forEach(k => p.delete(k)); setParams(p); }}>Clear range filter</button>
      </div>}
      {data?.length === 5000 && <p className="mx-4 mt-2 text-[13px]">Showing the first 5,000 records; narrow the date range for a complete total.</p>}
      <div className="mx-4 mt-3 flex h-9 items-center gap-2 rounded-[10px] bg-fill px-2.5">
        <Icon name="search" size={17} className="text-label-2" />
        <input
          type="search"
          placeholder="Search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="min-w-0 flex-1 bg-transparent text-[17px] outline-none placeholder:text-label-2"
        />
      </div>

      <div className="no-scrollbar mt-3 flex gap-2 overflow-x-auto px-4">
        <Chip active={!kind} onClick={() => setKind("")}>All</Chip>
        <Chip active={kind === "expense"} onClick={() => setKind(kind === "expense" ? "" : "expense")}>Expense</Chip>
        <Chip active={kind === "income"} onClick={() => setKind(kind === "income" ? "" : "income")}>Income</Chip>
        {(me?.users.length ?? 0) > 1 &&
          me!.users.map((u) => (
            <Chip key={u.id} active={userId === u.id} onClick={() => setUserId(userId === u.id ? "" : u.id)}>
              {u.name.split(" ")[0]}
            </Chip>
          ))}
        {cards.map((c) => (
          <Chip key={c.id} active={cardId === c.id} onClick={() => setCardId(cardId === c.id ? "" : c.id)}>
            {c.name}
          </Chip>
        ))}
        {(settings?.currencies ?? []).filter((c) => c !== BASE).map((c) => (
          <Chip key={c} active={currency === c} onClick={() => setCurrency(currency === c ? "" : c)}>
            {c}
          </Chip>
        ))}
      </div>

      {categoryId !== undefined && (
        <div className="mx-4 mt-3 flex items-center justify-between rounded-xl bg-card px-4 py-2 text-[15px]">
          <span>
            Category: <b>{catById.get(categoryId)?.name ?? "Uncategorized"}</b>
          </span>
          <button
            className="text-accent"
            onClick={() => {
              params.delete("category");
              setParams(params, { replace: true });
            }}
          >
            Remove
          </button>
        </div>
      )}

      {data && data.length > 0 && (
        <div className="mx-4 mt-3 flex flex-wrap gap-x-4 px-1 text-[13px] text-label-2">
          <span>{data.length} transactions</span>
          {(Object.entries(totals) as [Currency, number][]).map(([c, v]) => (
            <span key={c} className="tabular">
              Net: {money(v, c)}
            </span>
          ))}
        </div>
      )}

      {planned.length > 0 && !q && (
        <Section title="Planned recurring income & expenses" footer="Automatically turned into a transaction on its due date.">
          {planned.map((p) => (
            <Row key={`${p.recurring_id}-${p.date}`} chevron onClick={() => nav(p.kind === "income" ? "/recurring/income" : "/recurring/expenses")} className="opacity-70">
              <CatBadge cat={p.category_id ? catById.get(p.category_id) : null} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[17px]">{p.name}</div>
                <div className="truncate text-[13px] text-label-2">{dayLabel(p.date)} · planned</div>
              </div>
              <div className={`tabular text-[17px] ${p.kind === "income" ? "text-green" : ""}`}>
                {p.kind === "income" ? "+" : "−"}
                {money(p.amount, p.currency)}
              </div>
            </Row>
          ))}
        </Section>
      )}

      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : groups.length === 0 && planned.length === 0 ? (
        <Empty icon="list" title="No transactions found" text="Change the filters or add a new entry." />
      ) : (
        groups.map(([date, txs]) => (
          <Section key={date} title={dayLabel(date)}>
            {txs.map((t) => (
              <TxRow key={t.id} tx={t} onClick={() => editTx(t)} />
            ))}
          </Section>
        ))
      )}
    </div>
  );
}
