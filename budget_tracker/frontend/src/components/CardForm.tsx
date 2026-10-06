import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Card } from "../lib/api";
import { BASE, amountInput, parseAmount } from "../lib/format";
import { useInvalidate, useMe } from "../lib/queries";
import { CurrencyAmountsEditor, Field, PrimaryButton, Section, Select, Sheet, TextButton, amountRows, inputCls, rowsToAmounts, toast, type AmountRow } from "./ui";

const COLORS = ["#0A84FF", "#30D158", "#FF9F0A", "#FF375F", "#BF5AF2", "#5E5CE6", "#1C1C1E", "#8E8E93"];
const DAYS = Array.from({ length: 31 }, (_, i) => ({ value: i + 1, label: `Day ${i + 1}` }));

export default function CardForm({ open, onClose, card }: { open: boolean; onClose: () => void; card?: Card }) {
  const me = useMe().data;
  const invalidate = useInvalidate();
  const nav = useNavigate();
  const [f, setF] = useState(() => ({
    name: card?.name ?? "",
    bank: card?.bank ?? "",
    last4: card?.last4 ?? "",
    limit: amountInput(card?.limit),
    statement_day: card?.statement_day ?? 15,
    due_day: card?.due_day ?? 25,
    owner_id: card?.owner_id ?? me?.me.id ?? null,
    holder_id: card?.holder_id ?? 0,
    color: card?.color ?? COLORS[0],
  }));
  const [debts, setDebts] = useState<AmountRow[]>(() => amountRows(card?.debts, BASE, amountInput));
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((x) => ({ ...x, [k]: v }));

  async function save() {
    if (!f.name.trim()) return toast("Card name is required", "err");
    if (debts.some((r) => r.text.trim() && Number.isNaN(parseAmount(r.text)))) return toast("Enter a valid balance", "err");
    const body = {
      ...f,
      holder_id: f.holder_id || null,
      limit: f.limit.trim() ? parseAmount(f.limit) : null,
      debts: rowsToAmounts(debts, parseAmount),
      archived: false,
    };
    try {
      if (card) await api.put(`cards/${card.id}`, body);
      else await api.post("cards", body);
      invalidate();
      toast("Card saved");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }

  async function archive() {
    if (!card || !confirm(`Remove ${card.name}? Past transactions are kept.`)) return;
    await api.del(`cards/${card.id}`);
    invalidate();
    onClose();
    nav("/cards");
  }

  return (
    <Sheet open={open} onClose={onClose} title={card ? "Edit Card" : "New Card"} right={<TextButton onClick={save}>Save</TextButton>}>
      <Section>
        <Field label="Name">
          <input className={inputCls} placeholder="e.g. Rewards Card" value={f.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Bank">
          <input className={inputCls} placeholder="e.g. Example Bank" value={f.bank} onChange={(e) => set("bank", e.target.value)} />
        </Field>
        <Field label="Last 4 digits">
          <input className={inputCls} inputMode="numeric" maxLength={4} placeholder="1234" value={f.last4} onChange={(e) => set("last4", e.target.value.replace(/\D/g, ""))} />
        </Field>
        <Field label={`Limit (${BASE})`}>
          <input className={inputCls} inputMode="decimal" placeholder="Optional" value={f.limit} onChange={(e) => set("limit", e.target.value)} />
        </Field>
        {me && me.users.length > 1 && (
          <Field label="Cardholder">
            <Select value={f.owner_id} onChange={(v) => set("owner_id", v)} options={me.users.map((u) => ({ value: u.id, label: u.name }))} />
          </Field>
        )}
        {me && me.users.length > 1 && (
          <Field label="Used by">
            <Select
              value={f.holder_id}
              onChange={(v) => set("holder_id", v)}
              options={[{ value: 0, label: "Cardholder" }, ...me.users.map((u) => ({ value: u.id, label: `${u.name} (authorized user)` }))]}
            />
          </Field>
        )}
      </Section>

      <Section title="Schedule" footer="Enter these once; each month's dates are calculated from these days. If the bank changes them, they are updated automatically from the statement you upload.">
        <Field label="Statement closing">
          <Select value={f.statement_day} onChange={(v) => set("statement_day", v)} options={DAYS} />
        </Field>
        <Field label="Payment due">
          <Select value={f.due_day} onChange={(v) => set("due_day", v)} options={DAYS} />
        </Field>
      </Section>

      <Section
        title="Current balance"
        footer="You can enter it manually; it is updated automatically when you upload a statement. Add a line for each currency the card is billed in."
      >
        <CurrencyAmountsEditor rows={debts} onChange={setDebts} label="Balance" />
      </Section>

      <Section title="Color">
        <div className="flex flex-wrap gap-3 p-4">
          {COLORS.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={c}
              onClick={() => set("color", c)}
              className={`h-9 w-9 rounded-full ${f.color === c ? "ring-2 ring-accent ring-offset-2 ring-offset-card" : ""}`}
              style={{ background: c }}
            />
          ))}
        </div>
      </Section>

      <div className="mx-4 mt-6 space-y-3">
        <PrimaryButton onClick={save}>Save</PrimaryButton>
        {card && (
          <PrimaryButton tone="red" onClick={archive}>
            Remove Card
          </PrimaryButton>
        )}
      </div>
    </Sheet>
  );
}
