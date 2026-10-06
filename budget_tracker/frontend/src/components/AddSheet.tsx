import { useEffect, useMemo, useRef, useState } from "react";
import NewCategorySheet from "./NewCategorySheet";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Currency, type Doc, type Kind, type Method, type Tx } from "../lib/api";
import { BASE, amountInput, dayLabel, parseAmount, today, uid } from "../lib/format";
import { prepareUpload } from "../lib/image";
import { useInvalidate, useLookups } from "../lib/queries";
import {
  CurrencyPicker,
  Field,
  Icon,
  PrimaryButton,
  Section,
  Segmented,
  Select,
  Sheet,
  Spinner,
  TextButton,
  dateCls,
  inputCls,
  toast,
} from "./ui";

const INSTALLMENTS = [1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 18, 24];

export default function AddSheet({
  open,
  initialMode,
  tx,
  onClose,
}: {
  open: boolean;
  initialMode: "manual" | "upload";
  tx?: Tx;
  onClose: () => void;
}) {
  const [mode, setMode] = useState(initialMode);
  // The top and bottom "Save" share the same saving state (no double submit)
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) setMode(tx ? "manual" : initialMode);
  }, [open, initialMode, tx]);

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={tx ? "Edit Transaction" : "New Entry"}
      right={
        mode === "manual" ? (
          <TextButton disabled={saving} onClick={() => document.getElementById("tx-submit")?.click()}>
            Save
          </TextButton>
        ) : undefined
      }
    >
      {!tx && (
        <div className="px-4 pb-1">
          <Segmented
            value={mode}
            onChange={setMode}
            options={[
              { value: "manual", label: "Manual" },
              { value: "upload", label: "Receipt / Statement" },
            ]}
          />
        </div>
      )}
      {mode === "manual" ? <ManualForm key={tx?.id ?? "new"} tx={tx} onDone={onClose} saving={saving} setSaving={setSaving} /> : <UploadForm onDone={onClose} />}
    </Sheet>
  );
}

function ManualForm({
  tx,
  onDone,
  saving,
  setSaving,
}: {
  tx?: Tx;
  onDone: () => void;
  saving: boolean;
  setSaving: (v: boolean) => void;
}) {
  // The server won't create a new record for a second request with the same key
  const clientRef = useRef(uid());
  const inFlight = useRef(false);
  const { cats, cards, me } = useLookups();
  const invalidate = useInvalidate();
  const [kind, setKind] = useState<Kind>(tx?.kind ?? "expense");
  const [amount, setAmount] = useState(amountInput(tx?.amount));
  const [currency, setCurrency] = useState<Currency>(tx?.currency ?? BASE);
  const [merchant, setMerchant] = useState(tx?.merchant ?? "");
  const [categoryId, setCategoryId] = useState<number | null>(tx?.category_id ?? null);
  const [newCat, setNewCat] = useState(false);
  const [date, setDate] = useState(tx?.date ?? today());
  const [method, setMethod] = useState<Method>(tx?.payment_method ?? (cards.length ? "card" : "cash"));
  const [cardId, setCardId] = useState<number | null>(tx?.card_id ?? null);
  const [inst, setInst] = useState<number>(tx?.installment_count ?? 1);
  const [userId, setUserId] = useState<number | null>(tx?.user_id ?? null);
  const [spendingType, setSpendingType] = useState<"variable" | "fixed" | "one_off">(tx?.spending_type ?? "variable");
  const [note, setNote] = useState(tx?.note ?? "");
  const [catTouched, setCatTouched] = useState(!!tx);
  const amountRef = useRef<HTMLInputElement>(null);

  const merchants = useQuery({
    queryKey: ["merchants"],
    queryFn: () => api.get<{ merchant: string; category_id: number | null }[]>("transactions/merchants"),
    staleTime: 60_000,
  }).data;

  useEffect(() => {
    if (!tx) setTimeout(() => amountRef.current?.focus(), 350);
  }, [tx]);
  useEffect(() => {
    if (!cardId && cards.length) setCardId(cards[0].id);
  }, [cards, cardId]);
  useEffect(() => {
    if (!userId && me) setUserId(me.me.id);
  }, [me, userId]);

  // Suggest a category from the merchant name (if the user hasn't picked one)
  useEffect(() => {
    if (catTouched || merchant.trim().length < 2) return;
    const known = merchants?.find((m) => m.merchant.toLowerCase() === merchant.trim().toLowerCase());
    if (known?.category_id) {
      setCategoryId(known.category_id);
      return;
    }
    const t = setTimeout(async () => {
      try {
        const r = await api.get<{ category_id: number | null }>(`transactions/suggest-category?merchant=${encodeURIComponent(merchant)}`);
        if (r.category_id) setCategoryId(r.category_id);
      } catch {
        /* suggestion is optional */
      }
    }, 350);
    return () => clearTimeout(t);
  }, [merchant, merchants, catTouched]);

  const kindCats = useMemo(() => cats.filter((c) => c.kind === kind && !c.archived), [cats, kind]);

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (inFlight.current) return; // two clicks at once
    const value = parseAmount(amount);
    if (!value || value <= 0) {
      toast("Enter a valid amount", "err");
      amountRef.current?.focus();
      return;
    }
    const body = {
      kind,
      amount: value,
      currency,
      date,
      category_id: categoryId,
      user_id: userId,
      payment_method: method,
      card_id: method === "card" ? cardId : null,
      merchant,
      note,
      installment_count: method === "card" && kind === "expense" && inst > 1 ? inst : null,
      spending_type: spendingType,
      client_ref: tx ? undefined : clientRef.current,
    };
    inFlight.current = true;
    setSaving(true);
    try {
      if (tx) await api.put(`transactions/${tx.id}`, body);
      else await api.post("transactions", body);
      invalidate();
      toast(tx ? "Updated" : "Saved");
      onDone();
    } catch (err) {
      toast((err as Error).message, "err");
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  }

  async function remove() {
    if (!tx || !confirm(tx.installment_no ? `Delete all ${tx.installment_count} months of this installment purchase?` : "Delete this transaction?")) return;
    await api.del(`transactions/${tx.id}`);
    invalidate();
    toast("Deleted");
    onDone();
  }

  return (
    <form onSubmit={submit} className="pb-4">
      <div className="px-4 pt-3">
        <Segmented
          value={kind}
          onChange={(k) => {
            setKind(k);
            setCategoryId(null);
            setCatTouched(false);
            if (k === "income" && method === "card") setMethod("bank");
          }}
          options={[
            { value: "expense", label: "Expense" },
            { value: "income", label: "Income" },
          ]}
        />
      </div>

      <div className="mx-4 mt-3 flex items-center gap-3 rounded-xl bg-card px-4 py-3">
        <input
          ref={amountRef}
          inputMode="decimal"
          enterKeyHint="done"
          placeholder="0.00"
          value={amount}
          onChange={(e) => setAmount(e.target.value.replace(/[^\d.,]/g, ""))}
          className={`tabular min-w-0 flex-1 bg-transparent text-[34px] font-semibold outline-none placeholder:text-label-3 ${
            kind === "income" ? "text-green" : "text-label"
          }`}
          aria-label="Amount"
        />
        <CurrencyPicker value={currency} onChange={setCurrency} />
      </div>

      <Section>
        <Field label={kind === "income" ? "Source" : "Merchant"}>
          <input
            className={inputCls}
            placeholder={kind === "income" ? "e.g. Salary" : "e.g. Whole Foods"}
            value={merchant}
            list="merchant-list"
            autoCapitalize="words"
            onChange={(e) => setMerchant(e.target.value)}
          />
          <datalist id="merchant-list">
            {merchants?.map((m) => <option key={m.merchant} value={m.merchant} />)}
          </datalist>
        </Field>
        <Field label="Date">
          <input type="date" className={dateCls} value={date} onChange={(e) => setDate(e.target.value)} required />
        </Field>
      </Section>

      <Section title="Category">
        <div className="grid grid-cols-4 gap-1 p-2">
          {kindCats.map((c) => {
            const active = categoryId === c.id;
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => {
                  setCategoryId(c.id);
                  setCatTouched(true);
                }}
                className={`flex min-h-[68px] flex-col items-center justify-center gap-1 rounded-lg px-0.5 py-1.5 text-center ${
                  active ? "bg-fill ring-2 ring-accent" : "active:bg-fill"
                }`}
              >
                <span className="text-[22px] leading-none">{c.icon}</span>
                <span className="line-clamp-2 text-[11px] leading-tight text-label">{c.name}</span>
              </button>
            );
          })}
          <button
            type="button"
            onClick={() => setNewCat(true)}
            className="flex min-h-[68px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-accent/60 px-0.5 py-1.5 text-center active:bg-fill"
          >
            <span className="text-[22px] leading-none text-accent">+</span>
            <span className="text-[11px] leading-tight text-accent">New</span>
          </button>
        </div>
      </Section>
      {newCat && (
        <NewCategorySheet
          kind={kind}
          onClose={() => setNewCat(false)}
          onCreated={(c) => {
            setCategoryId(c.id);
            setCatTouched(true);
          }}
        />
      )}

      <Section title="Payment">
        <div className="px-4 py-2">
          <Segmented
            value={method}
            onChange={setMethod}
            options={[
              { value: "card", label: "Credit card" },
              { value: "cash", label: "Cash" },
              { value: "bank", label: "Bank" },
              { value: "voucher", label: "Voucher" },
            ]}
          />
        </div>
        {method === "card" && (
          <>
            {cards.length ? (
              <Field label="Card">
                <Select
                  value={cardId}
                  onChange={(id) => {
                    setCardId(id);
                    // When an authorized-user card is picked, the spending is assigned to its holder (can be changed)
                    const c = cards.find((x) => x.id === id);
                    if (!tx && c && (c.holder_id || c.owner_id)) setUserId(c.holder_id ?? c.owner_id);
                  }}
                  options={cards.map((c) => ({ value: c.id, label: c.last4 ? `${c.name} ·${c.last4}` : c.name }))} />
              </Field>
            ) : (
              <div className="px-4 py-3 text-[15px] text-label-2">Add a card from the Cards tab first.</div>
            )}
            {kind === "expense" &&
              (tx?.installment_no ? (
                // One month of an installment series: the amount is this month's installment; changes apply to all months
                <Field label="Installments">
                  <span className="text-[15px] text-label-2">
                    {tx.installment_no}/{tx.installment_count} · applies to all months
                  </span>
                </Field>
              ) : (
                <Field label="Installments">
                  <Select value={inst} onChange={setInst} options={INSTALLMENTS.map((n) => ({ value: n, label: n === 1 ? "Single payment" : `${n} installments` }))} />
                </Field>
              ))}
          </>
        )}
        {me && me.users.length > 1 && (
          <Field label="Who">
            <Select value={userId} onChange={setUserId} options={me.users.map((u) => ({ value: u.id, label: u.name }))} />
          </Field>
        )}
      </Section>

      <Section>
        {kind === "expense" && <Field label="Spending type"><Select value={spendingType} onChange={setSpendingType} options={[{ value: "variable", label: "Variable" }, { value: "fixed", label: "Fixed" }, { value: "one_off", label: "One-off" }]} /></Field>}
        <Field label="Note">
          <input className={inputCls} placeholder="Optional" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </Section>

      {tx?.last_change && (
        <p className="mx-8 mt-3 text-[13px] text-label-2">
          Last change: {tx.last_change.by} · {dayLabel(tx.last_change.at.slice(0, 10))} {tx.last_change.at.slice(11, 16)}
        </p>
      )}
      <div className="mx-4 mt-6 space-y-3">
        <PrimaryButton type="submit" disabled={saving}>
          {saving ? <Spinner className="border-white/40 border-t-white" /> : tx ? "Update" : "Save"}
        </PrimaryButton>
        {tx && (
          <PrimaryButton tone="red" onClick={remove}>
            Delete Transaction
          </PrimaryButton>
        )}
      </div>
      <button id="tx-submit" type="submit" className="hidden" />
    </form>
  );
}

function UploadForm({ onDone }: { onDone: () => void }) {
  const { cards, me } = useLookups();
  const navigate = useNavigate();
  const invalidate = useInvalidate();
  const [kind, setKind] = useState<"auto" | "receipt" | "statement">("auto");
  const [cardId, setCardId] = useState<number | 0>(0);
  const [busy, setBusy] = useState(false);
  const cameraRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function upload(list: FileList | null) {
    if (!list?.length) return;
    setBusy(true);
    try {
      const fd = new FormData();
      for (const f of Array.from(list)) fd.append("files", await prepareUpload(f));
      fd.append("kind", kind);
      if (cardId) fd.append("card_id", String(cardId));
      const docs = await api.post<Doc[]>("documents", fd);
      invalidate();
      toast(docs.length > 1 ? `Reading ${docs.length} documents…` : "Reading document…");
      onDone();
      navigate(docs.length === 1 ? `/documents/${docs[0].id}` : "/documents");
    } catch (err) {
      toast((err as Error).message, "err");
    } finally {
      setBusy(false);
      if (cameraRef.current) cameraRef.current.value = "";
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="pb-4">
      {me && !me.ai_enabled && (
        <div className="mx-4 mt-3 flex gap-2 rounded-xl bg-orange/15 p-3 text-[15px]">
          <Icon name="warning" className="text-orange" />
          <span>To enable automatic reading, enter your Ollama address (ollama_url) in the add-on settings.</span>
        </div>
      )}
      <Section title="Document type" footer="For statements, picking the card makes matching exact; otherwise it is matched by the last 4 digits.">
        <div className="px-4 py-2">
          <Segmented
            value={kind}
            onChange={setKind}
            options={[
              { value: "auto", label: "Auto" },
              { value: "receipt", label: "Receipt" },
              { value: "statement", label: "Statement" },
            ]}
          />
        </div>
        {cards.length > 0 && (
          <Field label="Card">
            <Select
              value={cardId}
              onChange={setCardId}
              options={[{ value: 0, label: "Detect automatically" }, ...cards.map((c) => ({ value: c.id, label: c.name }))]}
            />
          </Field>
        )}
      </Section>

      <div className="mx-4 mt-6 grid grid-cols-2 gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => cameraRef.current?.click()}
          className="flex aspect-[4/3] flex-col items-center justify-center gap-2 rounded-2xl bg-card text-accent active:opacity-60 disabled:opacity-40"
        >
          <Icon name="camera" size={34} stroke={1.6} />
          <span className="text-[15px] font-semibold">Take Photo</span>
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
          className="flex aspect-[4/3] flex-col items-center justify-center gap-2 rounded-2xl bg-card text-accent active:opacity-60 disabled:opacity-40"
        >
          <Icon name="doc" size={34} stroke={1.6} />
          <span className="text-[15px] font-semibold">File / PDF</span>
        </button>
      </div>
      {busy && (
        <div className="mt-4 flex items-center justify-center gap-2 text-[15px] text-label-2">
          <Spinner /> Uploading…
        </div>
      )}
      <p className="mx-8 mt-4 text-center text-[13px] text-label-2">
        JPEG, PNG, HEIC or PDF. You can select multiple files; extracted items are shown for your review.
      </p>
      <input ref={cameraRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => upload(e.target.files)} />
      <input ref={fileRef} type="file" accept="image/*,application/pdf" multiple hidden onChange={(e) => upload(e.target.files)} />
    </div>
  );
}
