import { useState } from "react";
import { useNavigate } from "react-router-dom";
import NewCategorySheet from "../components/NewCategorySheet";
import { Empty, ErrorState, Icon, PageHeader, Select, Spinner, TextButton, toast } from "../components/ui";
import { api } from "../lib/api";
import { usePendingCategories, type PendingGroup, type PendingOrder } from "../lib/categorize";
import { BASE, dayLabel, money } from "../lib/format";
import { useInvalidate, useLookups } from "../lib/queries";

const CHIP_COUNT = 12;

interface LastAction {
  group: PendingGroup;
  categoryId: number | null;
  label: string;
}

/** One-tap categorization, grouped by merchant, of items left in "Other" after a statement import. */
export default function CategorizePage() {
  const nav = useNavigate();
  const q = usePendingCategories();
  const invalidate = useInvalidate();
  const { catById, cats, cardById } = useLookups();
  const [done, setDone] = useState<Set<string>>(new Set());
  const [last, setLast] = useState<LastAction | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [split, setSplit] = useState<Set<string>>(new Set());
  const [creatingFor, setCreatingFor] = useState<PendingGroup | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const noteOf = (g: PendingGroup) => notes[g.key] ?? (g.orders.length === 1 ? g.orders[0].note : "");
  const other = cats.find((c) => c.kind === "expense" && c.name === "Other Expense");

  async function apply(g: PendingGroup, categoryId: number, label: string, keepOther = false) {
    setBusy(g.key);
    try {
      // Marketplace order or split group: only this order, no merchant rule is learned
      const note = notes[g.key];
      await api.post("transactions/categorize", {
        tx_ids: g.tx_ids,
        category_id: categoryId,
        learn: !keepOther && !g.per_order,
        ...(note !== undefined ? { note } : {}),
      });
      setDone((s) => new Set(s).add(g.key));
      setLast({ group: g, categoryId, label });
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  }

  async function undo() {
    if (!last) return;
    const g = last.group;
    try {
      // Back to the previous state: "Other" and unconfirmed (the rule is not reverted; a new choice overwrites it)
      await api.post("transactions/categorize", { tx_ids: g.tx_ids, category_id: other?.id ?? null, learn: false, confirm: false });
      setDone((s) => {
        const n = new Set(s);
        n.delete(g.key);
        return n;
      });
      setLast(null);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }

  function leave() {
    invalidate();
    nav(-1);
  }

  // Groups marked "separately" are split into their orders
  const asOrder = (g: PendingGroup, o: PendingOrder): PendingGroup => ({
    ...g, key: `${g.key}|${o.key}`, per_order: true, orders: [o], tx_ids: o.tx_ids, total: o.amount, first: o.date, last: o.date,
    count: o.tx_ids.length, cards: o.card_id ? [o.card_id] : [],
  });
  const groups = (q.data?.groups ?? [])
    .flatMap((g) => (split.has(g.key) ? g.orders.map((o) => asOrder(g, o)) : [g]))
    .filter((g) => !done.has(g.key))
    // Newest order first, to compare side by side with order history
    .sort((a, b) => (a.orders[0]?.date ?? a.last) < (b.orders[0]?.date ?? b.last) ? 1 : -1);
  const remaining = groups.reduce((s, g) => s + g.count, 0);
  const favorites = (q.data?.favorites ?? []).filter((id) => catById.has(id));
  const allOptions = cats
    .filter((c) => c.kind === "expense" && !c.archived && c.id !== other?.id)
    .map((c) => ({ value: c.id, label: `${c.icon} ${c.name}` }));

  return (
    <div>
      <PageHeader
        title="Categorize"
        sub={q.data ? (remaining ? `${groups.length} cards · ${remaining} transactions` : undefined) : undefined}
        left={
          <TextButton onClick={leave}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> Back
          </TextButton>
        }
      />
      {creatingFor && (
        <NewCategorySheet
          onClose={() => setCreatingFor(null)}
          onCreated={(c) => apply(creatingFor, c.id, `${c.icon} ${c.name}`)}
        />
      )}
      {last && (
        <div className="mx-4 mt-3 flex items-center gap-2 rounded-xl bg-green/15 px-3 py-2.5 text-[14px]">
          <Icon name="check" size={18} className="shrink-0 text-green" />
          <span className="min-w-0 flex-1 truncate">
            {last.group.merchant} → {last.label}
          </span>
          <button type="button" className="shrink-0 font-semibold text-accent" onClick={undo}>
            Undo
          </button>
        </div>
      )}
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !q.data ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : groups.length === 0 ? (
        <>
          <Empty icon="check" title="All categorized" text="Your choices were learned per merchant; on future statements these merchants will land in the right category automatically." />
          <div className="mx-4">
            <button type="button" onClick={() => { invalidate(); nav("/insights", { replace: true }); }} className="h-12 w-full rounded-xl bg-accent text-[17px] font-semibold text-white">
              View savings insights
            </button>
            <button type="button" onClick={leave} className="mt-2 h-11 w-full text-[17px] text-accent">
              Close
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="mx-8 mt-3 text-[13px] text-label-2">
            Tap a category; transactions from the same merchant change together and the merchant is learned. On marketplaces such as
            Amazon, each order appears as a separate card and is not learned; all months of an installment order change together.
          </p>
          {groups.map((g) => {
            const chips = [g.suggestion, ...favorites.filter((id) => id !== g.suggestion)]
              .filter((id): id is number => id !== null && catById.has(id))
              .slice(0, CHIP_COUNT);
            const cardNames = g.cards.map((id) => cardById.get(id)?.name).filter(Boolean).join(", ");
            return (
              <section key={g.key} className={`mx-4 mt-3 rounded-2xl bg-card p-3.5 ${busy === g.key ? "opacity-50" : ""}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-[16px] font-semibold">{g.merchant}</div>
                    <div className="truncate text-[12px] text-label-2">
                      {g.per_order && g.orders.length === 1 ? (
                        <OrderMeta o={g.orders[0]} />
                      ) : (
                        <>
                          {g.orders.length} orders · {g.first === g.last ? dayLabel(g.first) : `${dayLabel(g.first)} – ${dayLabel(g.last)}`}
                        </>
                      )}
                      {cardNames ? ` · ${cardNames}` : ""}
                    </div>
                    {!g.per_order && g.orders.length > 1 && (
                      <button type="button" className="mt-0.5 text-[13px] font-semibold text-accent" onClick={() => setSplit((s) => new Set(s).add(g.key))}>
                        Categorize separately
                      </button>
                    )}
                  </div>
                  <div className="tabular shrink-0 text-[16px] font-semibold">{money(g.total, BASE, true)}</div>
                </div>
                <input
                  className="mt-2.5 h-9 w-full rounded-lg bg-fill px-3 text-[14px] placeholder:text-label-3"
                  placeholder={g.per_order || g.orders.length === 1 ? "📝 What did you buy? (e.g. headphones, birthday gift)" : "📝 Note (optional, applied to all transactions)"}
                  value={noteOf(g)}
                  maxLength={200}
                  onChange={(e) => setNotes((n) => ({ ...n, [g.key]: e.target.value }))}
                />
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {chips.map((id) => {
                    const c = catById.get(id)!;
                    const suggested = id === g.suggestion;
                    return (
                      <button
                        key={id}
                        type="button"
                        disabled={busy !== null}
                        onClick={() => apply(g, id, `${c.icon} ${c.name}`)}
                        className={`flex h-8 items-center gap-1 rounded-full px-2.5 text-[13px] active:opacity-60 ${
                          suggested ? "bg-accent text-white" : "bg-fill text-label"
                        }`}
                      >
                        <span>{c.icon}</span>
                        {c.name}
                      </button>
                    );
                  })}
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => setCreatingFor(g)}
                    className="flex h-8 items-center gap-1 rounded-full border border-dashed border-accent px-2.5 text-[13px] font-medium text-accent active:opacity-60"
                  >
                    + New
                  </button>
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <div className="min-w-0 flex-1 rounded-lg bg-fill">
                    <Select
                      value={null}
                      placeholder="Other category…"
                      options={allOptions}
                      onChange={(id: number) => apply(g, id, allOptions.find((o) => o.value === id)?.label ?? "")}
                    />
                  </div>
                  {other && (
                    <button type="button" disabled={busy !== null} className="shrink-0 text-[14px] text-label-2" onClick={() => apply(g, other.id, "Keep as Other", true)}>
                      Keep as Other
                    </button>
                  )}
                </div>
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}

function OrderMeta({ o }: { o: PendingOrder }) {
  return (
    <>
      {o.installment_count ? "Order " : ""}
      {dayLabel(o.date)}
      {o.installment_count
        ? ` · ${o.installment_count} installments × ${money(o.monthly ?? 0, BASE, true)}${o.current_no ? ` (now ${o.current_no}/${o.installment_count})` : ""}`
        : ""}
    </>
  );
}
