import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  CurrencyAmountsEditor,
  CurrencyPicker,
  ErrorState,
  Field,
  Icon,
  PageHeader,
  PrimaryButton,
  Section,
  Select,
  Spinner,
  TextButton,
  Toggle,
  dateCls,
  inputCls,
  toast,
  amountRows,
  rowsToAmounts,
  type AmountRow,
} from "../components/ui";
import { api, fileUrl, type Currency, type Doc, type Draft, type DraftRow } from "../lib/api";
import { BASE, amountInput, money, parseAmount, shortDate } from "../lib/format";
import { useInvalidate, useLookups, useMe } from "../lib/queries";
import { STATUS } from "./Documents";

export default function DocumentReview() {
  const { id } = useParams();
  const nav = useNavigate();
  const invalidate = useInvalidate();
  const me = useMe().data;
  const { data: doc, isError, error, refetch } = useQuery({
    queryKey: ["document", id],
    queryFn: () => api.get<Doc>(`documents/${id}`),
    refetchInterval: (q) => (q.state.data && ["pending", "processing"].includes(q.state.data.status) ? 2000 : false),
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const draftOwner = useRef("");

  const draftKey = `budget:document:${me?.me.id ?? "unknown"}:${id}`;
  useEffect(() => {
    if (!doc || !me) return;
    draftOwner.current = "";
    if (doc.status === "review" && doc.draft) {
      let initial = structuredClone(doc.draft);
      try {
        const saved = JSON.parse(sessionStorage.getItem(draftKey) || "null");
        if (saved?.original === JSON.stringify(doc.draft)) initial = saved.draft;
      } catch { /* Storage may be unavailable in a private webview. */ }
      setDraft(initial);
      draftOwner.current = draftKey;
    } else {
      setDraft(null);
      try { sessionStorage.removeItem(draftKey); } catch { /* optional draft storage */ }
    }
  }, [id, doc?.status, me?.me.id]);
  useEffect(() => {
    if (!draft || !doc?.draft || doc.status !== "review" || !me || draftOwner.current !== draftKey) return;
    try { sessionStorage.setItem(draftKey, JSON.stringify({ original: JSON.stringify(doc.draft), draft })); }
    catch { /* Editing still works without browser storage. */ }
  }, [draft, draftKey, doc?.status]);

  const back = (
    <TextButton onClick={() => nav(-1)}>
      <Icon name="chevronLeft" size={20} stroke={2.5} /> Back
    </TextButton>
  );

  if (isError) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (!doc) return <div className="flex justify-center py-20"><Spinner /></div>;

  async function retry(kind?: string) {
    try {
      await api.post(`documents/${id}/retry${kind ? `?kind=${kind}` : ""}`);
      setDraft(null);
      invalidate();
    } catch (e) { toast((e as Error).message, "err"); }
  }
  async function discard() {
    if (!confirm("Delete this document?")) return;
    try {
      await api.del(`documents/${id}`);
      try { sessionStorage.removeItem(draftKey); } catch { /* optional storage */ }
      invalidate();
      nav("/documents", { replace: true });
    } catch (e) { toast((e as Error).message, "err"); }
  }
  async function confirmDraft() {
    if (!draft || inFlight.current) return; // double-click guard
    inFlight.current = true;
    setSaving(true);
    try {
      const r = await api.post<{ created: number }>(`documents/${id}/confirm`, { draft });
      invalidate();
      try { sessionStorage.removeItem(draftKey); } catch { /* optional storage */ }
      toast(`${r.created} transactions saved`);
      // If anything is left in "Other", go to the quick categorize screen
      const pending = await api.get<{ count: number }>("transactions/categorize/pending").catch(() => ({ count: 0 }));
      nav(pending.count ? "/categorize" : "/", { replace: true });
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  const st = STATUS[doc.status];
  const title = doc.doc_type === "statement" ? "Statement" : doc.doc_type === "receipt" ? "Receipt" : "Document";

  return (
    <div className={doc.status === "review" ? "pb-28" : ""}>
      <PageHeader title={title} left={back} right={<TextButton onClick={discard} className="!text-red">Delete</TextButton>} sub={<span className={st.cls}>{st.label}</span>} />

      <Preview doc={doc} />
      {doc.status === "review" && <p className="mx-5 mt-2 text-[12px] text-label-2">Edits are kept as a draft in this tab; nothing is added to transactions until you save.</p>}

      {(doc.status === "pending" || doc.status === "processing") && (
        <div className="mt-10 flex flex-col items-center gap-3 text-label-2">
          <Spinner className="h-8 w-8" />
          <div className="text-[15px]">Reading the document, this may take a few seconds…</div>
          <div className="text-[13px] text-label-3">You can leave this page; you will be notified when it is done.</div>
        </div>
      )}

      {doc.status === "error" && (
        <>
          <div className="mx-4 mt-4 flex gap-2 rounded-xl bg-red/10 p-3 text-[15px] text-red">
            <Icon name="warning" />
            <span>{doc.error || "The document could not be read."}</span>
          </div>
          <Section title="Try again">
            <div className="grid grid-cols-3 gap-2 p-3">
              {[
                ["auto", "Automatic"],
                ["receipt", "As receipt"],
                ["statement", "As statement"],
              ].map(([k, l]) => (
                <button key={k} type="button" onClick={() => retry(k)} className="h-11 rounded-lg bg-fill text-[15px] font-medium text-accent active:opacity-60">
                  {l}
                </button>
              ))}
            </div>
          </Section>
        </>
      )}

      {doc.status === "done" && (
        <div className="mx-4 mt-4 flex items-center gap-2 rounded-xl bg-green/10 p-3 text-[15px] text-green">
          <Icon name="check" /> The items in this document have been saved.
        </div>
      )}

      {doc.status === "review" && draft && <DraftEditor draft={draft} setDraft={setDraft} docId={doc.id} />}

      {doc.status === "review" && draft && (
        <ConfirmBar draft={draft} saving={saving} onConfirm={confirmDraft} />
      )}
    </div>
  );
}

function Preview({ doc }: { doc: Doc }) {
  const isPdf = doc.mime === "application/pdf";
  const canShowImage = !isPdf && !doc.mime.includes("heic") && !doc.mime.includes("heif");
  return (
    <a href={fileUrl(doc.id)} target="_blank" rel="noreferrer" className="mx-4 mt-3 flex items-center gap-3 rounded-xl bg-card p-2 active:opacity-70">
      {canShowImage ? (
        <img src={fileUrl(doc.id)} alt="" className="h-16 w-16 rounded-lg object-cover" />
      ) : (
        <span className="flex h-16 w-16 items-center justify-center rounded-lg bg-fill text-label-2">
          <Icon name="doc" size={28} />
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[15px]">{doc.filename}</div>
        <div className="text-[13px] text-accent">Open document</div>
      </div>
      <Icon name="chevronRight" size={16} className="mr-2 text-label-3" />
    </a>
  );
}

function ConfirmBar({ draft, saving, onConfirm }: { draft: Draft; saving: boolean; onConfirm: () => void }) {
  const count = draft.rows.filter((r) => r.include).length;
  const totals = useMemo(() => {
    const t: Partial<Record<Currency, number>> = {};
    for (const r of draft.rows) if (r.include && r.mode === "transaction") t[r.currency] = (t[r.currency] ?? 0) + (r.kind === "income" ? -r.amount : r.amount);
    return Object.entries(t) as [Currency, number][];
  }, [draft]);
  const needsCard = draft.doc_type === "statement" && !draft.card_id;
  return (
    <div className="pb-safe fixed inset-x-0 bottom-0 z-40 border-t border-sep backdrop-blur-xl" style={{ background: "var(--bar)" }}>
      <div className="mx-auto max-w-lg px-4 pt-3">
        {totals.length > 0 && (
          <div className="tabular mb-2 text-center text-[13px] text-label-2">Total: {totals.map(([c, v]) => money(v, c)).join(" · ")}</div>
        )}
        <PrimaryButton onClick={onConfirm} disabled={saving || needsCard || (count === 0 && draft.doc_type !== "statement")}>
          {saving ? <Spinner className="border-white/40 border-t-white" /> : needsCard ? "Select a card first" : count ? `Save ${count} items` : "Save statement"}
        </PrimaryButton>
      </div>
    </div>
  );
}

function DraftEditor({ draft, setDraft, docId }: { draft: Draft; setDraft: (d: Draft) => void; docId: number }) {
  const { cards, cats } = useLookups();
  const invalidate = useInvalidate();
  async function createCard() {
    try {
      const r = await api.post<{ card_id: number; name: string; draft: Draft }>(`documents/${docId}/create-card`);
      setDraft({ ...draft, card_id: r.card_id, rows: draft.rows.map((x) => (x.payment_method === "card" ? { ...x, card_id: r.card_id } : x)) });
      invalidate();
      toast(`Card "${r.name}" created`);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  const updateRow = (i: number, patch: Partial<DraftRow>) =>
    setDraft({ ...draft, rows: draft.rows.map((r, j) => (i === j ? { ...r, ...patch } : r)) });
  const allOn = draft.rows.every((r) => r.include);
  const stmt = draft.statement;

  return (
    <>
      {(draft.doc_type === "statement" || draft.rows.some((r) => r.payment_method === "card")) && (
        <Section title={draft.doc_type === "statement" ? "Statement details" : undefined} footer={draft.doc_type === "statement" && !draft.card_id ? "If this statement's card is not in the app yet, you can create it above from the statement details." : undefined}>
          <Field label="Card">
            <Select
              value={draft.card_id}
              placeholder="Select a card"
              onChange={(v) => setDraft({ ...draft, card_id: v, rows: draft.rows.map((r) => (r.payment_method === "card" ? { ...r, card_id: v } : r)) })}
              options={cards.map((c) => ({ value: c.id, label: c.last4 ? `${c.name} ·${c.last4}` : c.name }))}
            />
          </Field>
          {draft.doc_type === "statement" && !draft.card_id && (
            <button type="button" onClick={createCard} className="flex min-h-11 w-full items-center gap-2 px-4 text-left text-[17px] text-accent active:bg-fill">
              <Icon name="plus" size={20} /> Create a new card from statement details
            </button>
          )}
          {stmt && (
            <>
              <Field label="Statement date">
                <input type="date" className={dateCls} value={stmt.period_end} onChange={(e) => setDraft({ ...draft, statement: { ...stmt, period_end: e.target.value } })} />
              </Field>
              <Field label="Due date">
                <input type="date" className={dateCls} value={stmt.due_date} onChange={(e) => setDraft({ ...draft, statement: { ...stmt, due_date: e.target.value } })} />
              </Field>
              <StatementTotals stmt={stmt} onChange={(patch) => setDraft({ ...draft, statement: { ...stmt, ...patch } })} />
              <AmountField
                label={`Minimum (${stmt.currency || BASE})`}
                value={stmt.min_payment}
                onChange={(v) => setDraft({ ...draft, statement: { ...stmt, min_payment: v } })}
              />
            </>
          )}
        </Section>
      )}

      <TotalCheck draft={draft} />

      <Section
        title={`${draft.rows.length} items`}
        action={
          draft.rows.length > 1 && (
            <button className="text-[15px] text-accent" onClick={() => setDraft({ ...draft, rows: draft.rows.map((r) => ({ ...r, include: !allOn })) })}>
              {allOn ? "None" : "All"}
            </button>
          )
        }
        footer="Grayed-out items are not saved. Existing entries with the same merchant and amount are matched automatically; entries that only match on amount are shown as suggestions for you to decide."
      >
        {draft.rows.map((r, i) => (
          <DraftRowEditor key={i} row={r} cats={cats} onChange={(p) => updateRow(i, p)} />
        ))}
      </Section>
    </>
  );
}

type DraftStatement = NonNullable<Draft["statement"]>;

/** Statement balance per currency (editable list) + main currency. */
function StatementTotals({ stmt, onChange }: { stmt: DraftStatement; onChange: (patch: Partial<DraftStatement>) => void }) {
  const main = stmt.currency || BASE;
  const [rows, setRows] = useState<AmountRow[]>(() => amountRows(stmt.totals, main, amountInput));
  function update(next: AmountRow[]) {
    setRows(next);
    const totals = rowsToAmounts(next, parseAmount);
    onChange({ totals, currency: next[0]?.currency ?? main });
  }
  return (
    <>
      <Field label="Main currency">
        <CurrencyPicker
          value={main}
          onChange={(c) => {
            // Move (or add) the chosen currency to the first line
            const i = rows.findIndex((r) => r.currency === c);
            const next = i >= 0 ? [rows[i], ...rows.filter((_, j) => j !== i)] : [{ currency: c, text: "" }, ...rows.filter((r) => r.text.trim())];
            setRows(next);
            onChange({ currency: c, totals: rowsToAmounts(next, parseAmount) });
          }}
        />
      </Field>
      <CurrencyAmountsEditor rows={rows} onChange={update} label="Balance" />
    </>
  );
}

/** Compares the total of the parsed rows with the statement's "period spending" (in the statement's main currency). */
function TotalCheck({ draft }: { draft: Draft }) {
  const expected = draft.statement?.period_spending;
  if (!expected) return null;
  const cur = draft.statement?.currency || BASE;
  const read = draft.rows
    .filter((r) => r.kind === "expense" && r.currency === cur)
    .reduce((s, r) => s + (r.line_amount ?? r.amount), 0);
  const diff = Math.round((read - expected) * 100) / 100;
  if (Math.abs(diff) < 0.01) {
    return (
      <div className="mx-4 mt-4 flex items-center gap-2 rounded-xl bg-green/10 p-3 text-[15px] text-green">
        <Icon name="check" /> Parsed rows match the statement's period spending ({money(expected, cur)}).
      </div>
    );
  }
  const suspects = draft.rows.filter((r) => r.kind === "expense" && Math.abs((r.line_amount ?? r.amount) - Math.abs(diff)) < 0.01);
  return (
    <div className="mx-4 mt-4 flex gap-2 rounded-xl bg-orange/15 p-3 text-[15px]">
      <Icon name="warning" className="text-orange" />
      <div>
        Parsed rows total {money(read, cur)}, statement period spending is {money(expected, cur)}. Difference: <b>{money(diff, cur)}</b>.
        {diff > 0 && suspects.length > 0 && <> Possibly read extra: <b>{suspects.map((r) => r.merchant).join(", ")}</b>.</>}
        {diff < 0 && " Some rows may have been missed; compare with the document."}
      </div>
    </div>
  );
}

function AmountField({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  const [text, setText] = useState(amountInput(value));
  return (
    <Field label={label}>
      <input
        className={inputCls}
        inputMode="decimal"
        value={text}
        placeholder="0"
        onChange={(e) => {
          setText(e.target.value);
          const n = parseAmount(e.target.value);
          onChange(Number.isNaN(n) ? 0 : n);
        }}
      />
    </Field>
  );
}

function DraftRowEditor({ row, cats, onChange }: { row: DraftRow; cats: ReturnType<typeof useLookups>["cats"]; onChange: (p: Partial<DraftRow>) => void }) {
  const [open, setOpen] = useState(false);
  const [amountText, setAmountText] = useState(amountInput(row.amount));
  const cat = cats.find((c) => c.id === row.category_id);
  const badges: string[] = [];
  const cands = row.match_candidates ?? [];
  const matched = row.match ? cands.find((c) => c.tx_id === row.match) : undefined;
  if (row.mode === "installment_only") badges.push(row.note ?? `Earlier installment ${row.installment_no}/${row.installment_count} · card load only`);
  else if (row.installment_count) badges.push(`${row.installment_count} installments · total amount`);
  if (row.kind === "income") badges.push("Refund");

  return (
    <div className={`relative border-b border-sep last:border-b-0 ${row.include ? "" : "opacity-50"}`}>
      <div className="flex min-h-14 items-center gap-3 py-2 pl-4 pr-3">
        <Toggle checked={row.include} onChange={(v) => onChange({ include: v })} />
        <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setOpen(!open)}>
          <div className="truncate text-[16px]">{row.merchant || "—"}</div>
          <div className="truncate text-[13px] text-label-2">
            {shortDate(row.date)} · {cat ? `${cat.icon} ${cat.name}` : "Uncategorized"}
          </div>
          {badges.length > 0 && <div className="truncate text-[12px] text-orange">{badges.join(" · ")}</div>}
        </button>
        <button type="button" onClick={() => setOpen(!open)} className="tabular shrink-0 text-right text-[16px]">
          {row.kind === "income" ? "+" : ""}
          {money(row.amount, row.currency)}
        </button>
      </div>
      {/* Statement reconciliation: this row may be the same expense as an existing entry */}
      {row.match && (
        <div className="mx-3 mb-2 flex items-center gap-2 rounded-lg bg-green/10 px-3 py-2 text-[13px]">
          <Icon name="check" size={16} className="shrink-0 text-green" />
          <span className="min-w-0 flex-1">
            Matched an existing entry
            {matched && (
              <>
                : <b>{matched.merchant || "—"}</b> · {shortDate(matched.date)} · {money(matched.amount, matched.currency)}
              </>
            )}
            . No new entry will be created{row.payment_method === "card" ? "; the existing entry will be linked to the card" : ""}.
          </span>
          <button
            type="button"
            className="shrink-0 font-semibold text-accent"
            onClick={() => onChange({ match: null, duplicate_of: null, include: true })}
          >
            Keep separate
          </button>
        </div>
      )}
      {!row.match && cands.length > 0 && row.kind === "expense" && (
        <div className="mx-3 mb-2 rounded-lg bg-orange/10 px-3 py-2 text-[13px]">
          <div className="mb-1 text-label-2">Possible duplicate (same amount, nearby date):</div>
          {cands.map((c) => (
            <div key={c.tx_id} className="flex items-center gap-2 py-0.5">
              <span className="min-w-0 flex-1 truncate">
                <b>{c.merchant || "—"}</b> · {shortDate(c.date)} · {money(c.amount, c.currency)}
              </span>
              <button
                type="button"
                className="shrink-0 font-semibold text-accent"
                onClick={() => onChange({ match: c.tx_id, duplicate_of: c.tx_id, include: false })}
              >
                Match
              </button>
            </div>
          ))}
        </div>
      )}
      {open && (
        <div className="mx-3 mb-3 overflow-hidden rounded-xl bg-card-2">
          <Field label="Description">
            <input className={inputCls} value={row.merchant} onChange={(e) => onChange({ merchant: e.target.value })} />
          </Field>
          <Field label="Amount">
            <input
              className={inputCls}
              inputMode="decimal"
              value={amountText}
              onChange={(e) => {
                setAmountText(e.target.value);
                const n = parseAmount(e.target.value);
                if (!Number.isNaN(n)) onChange({ amount: n });
              }}
            />
          </Field>
          <Field label="Currency">
            <CurrencyPicker value={row.currency} onChange={(c) => onChange({ currency: c })} />
          </Field>
          <Field label="Date">
            <input type="date" className={dateCls} value={row.date} onChange={(e) => onChange({ date: e.target.value })} />
          </Field>
          <Field label="Category">
            <Select
              value={row.category_id}
              placeholder="Select"
              onChange={(v) => onChange({ category_id: v })}
              options={cats.filter((c) => c.kind === row.kind && !c.archived).map((c) => ({ value: c.id, label: `${c.icon} ${c.name}` }))}
            />
          </Field>
          {row.items && row.items.length > 0 && (
            <div className="px-4 py-2">
              <div className="mb-1 text-[13px] text-label-2">Receipt items</div>
              {row.items.map((it, k) => (
                <div key={k} className="flex justify-between gap-2 py-0.5 text-[13px]">
                  <span className="truncate">{it.description}</span>
                  <span className="tabular shrink-0">{money(it.amount, row.currency)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
