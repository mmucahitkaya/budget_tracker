import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { CatBadge, Field, Icon, IconButton, PageHeader, PrimaryButton, Row, Section, Segmented, Select, Sheet, TextButton, Toggle, inputCls, toast } from "../components/ui";
import { api, type Category, type Kind } from "../lib/api";
import { useCategories, useInvalidate } from "../lib/queries";

const COLORS = ["#34C759", "#FF9500", "#5AC8FA", "#FFCC00", "#AF52DE", "#FF3B30", "#5856D6", "#FF2D55", "#A2845E", "#0A84FF", "#30B0C7", "#8E8E93"];

export default function Categories() {
  const { data } = useCategories();
  const nav = useNavigate();
  const [kind, setKind] = useState<Kind>("expense");
  const [edit, setEdit] = useState<Category | "new" | null>(null);
  const list = (data ?? []).filter((c) => c.kind === kind);
  return (
    <div>
      <PageHeader
        title="Categories"
        left={
          <TextButton onClick={() => nav("/more")}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> More
          </TextButton>
        }
        right={<IconButton name="plus" label="Add" onClick={() => setEdit("new")} />}
      />
      <div className="mx-4 mt-3">
        <Segmented
          value={kind}
          onChange={setKind}
          options={[
            { value: "expense", label: "Expense" },
            { value: "income", label: "Income" },
          ]}
        />
      </div>
      <Section footer="When you correct the category of an expense, future expenses from the same merchant are automatically assigned to that category.">
        {list.map((c) => (
          <Row key={c.id} chevron onClick={() => setEdit(c)} className={c.archived ? "opacity-40" : ""}>
            <CatBadge cat={c} size={32} />
            <span className="flex-1 text-[17px]">{c.name}</span>
          </Row>
        ))}
      </Section>
      {edit && <CategorySheet cat={edit === "new" ? undefined : edit} kind={kind} onClose={() => setEdit(null)} />}
    </div>
  );
}

const PROTECTED = ["Other Expense", "Other Income"];

function CategorySheet({ cat, kind, onClose }: { cat?: Category; kind: Kind; onClose: () => void }) {
  const invalidate = useInvalidate();
  const all = useCategories().data ?? [];
  const sameKind = all.filter((c) => c.kind === (cat?.kind ?? kind) && c.id !== cat?.id && !c.archived);
  const other = sameKind.find((c) => PROTECTED.includes(c.name));
  const [moveTo, setMoveTo] = useState<number | null>(other?.id ?? null);
  const canDelete = !!cat && !PROTECTED.includes(cat.name);

  async function remove() {
    if (!cat) return;
    try {
      const u = await api.get<{ transactions: number; recurring: number; installments: number; budgets: number }>(`categories/${cat.id}/usage`);
      const target = sameKind.find((c) => c.id === (moveTo ?? other?.id));
      const parts = [
        u.transactions && `${u.transactions} transactions`,
        u.recurring && `${u.recurring} recurring items`,
        u.installments && `${u.installments} installments`,
      ].filter(Boolean);
      const msg = `Delete "${cat.name}"?` + (parts.length ? `\n${parts.join(", ")} will be moved to "${target?.name ?? "Other"}".` : "") +
        (u.budgets ? `\n${u.budgets} monthly budgets for this category will be deleted.` : "");
      if (!confirm(msg)) return;
      const r = await api.del<{ moved: number }>(`categories/${cat.id}${moveTo ? `?move_to=${moveTo}` : ""}`);
      invalidate();
      toast(r.moved ? `Deleted; ${r.moved} transactions moved` : "Deleted");
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  const [name, setName] = useState(cat?.name ?? "");
  const [icon, setIcon] = useState(cat?.icon ?? "📦");
  const [color, setColor] = useState(cat?.color ?? COLORS[0]);
  const [essential, setEssential] = useState(cat?.essential ?? false);
  const [archived, setArchived] = useState(cat?.archived ?? false);
  async function save() {
    if (!name.trim()) return toast("Name is required", "err");
    const body = { name, kind: cat?.kind ?? kind, icon: icon || "•", color, archived, essential };
    if (cat) await api.put(`categories/${cat.id}`, body);
    else await api.post("categories", body);
    invalidate();
    onClose();
  }
  return (
    <Sheet open onClose={onClose} title={cat ? "Category" : "New Category"} right={<TextButton onClick={save}>Save</TextButton>}>
      <div className="flex justify-center pt-2">
        <CatBadge cat={{ id: 0, name, kind, icon, color, archived }} size={64} />
      </div>
      <Section>
        <Field label="Name">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Category name" />
        </Field>
        <Field label="Emoji">
          <input className={inputCls} value={icon} onChange={(e) => setIcon([...e.target.value].slice(-2).join(""))} />
        </Field>
        {(cat?.kind ?? kind) === "expense" && (
          <Field label="Essential expense">
            <Toggle checked={essential} onChange={setEssential} />
          </Field>
        )}
        {cat && (
          <Field label="Hide">
            <Toggle checked={archived} onChange={setArchived} />
          </Field>
        )}
      </Section>
      <Section title="Color">
        <div className="flex flex-wrap gap-3 p-4">
          {COLORS.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={c}
              onClick={() => setColor(c)}
              className={`h-9 w-9 rounded-full ${color === c ? "ring-2 ring-accent ring-offset-2 ring-offset-card" : ""}`}
              style={{ background: c }}
            />
          ))}
        </div>
      </Section>
      <div className="mx-4 mt-6">
        <PrimaryButton onClick={save}>Save</PrimaryButton>
      </div>
      {canDelete && (
        <Section title="Delete" footer="Transactions, recurring items and installments in the deleted category are moved to the category you choose. Items moved to 'Other' show up on the Categorize screen.">
          <Field label="Move transactions to">
            <Select value={moveTo} onChange={setMoveTo} options={sameKind.map((c) => ({ value: c.id, label: `${c.icon} ${c.name}` }))} />
          </Field>
          <button type="button" onClick={remove} className="w-full border-t border-sep py-3 text-[17px] text-red active:bg-fill">
            Delete category
          </button>
        </Section>
      )}
    </Sheet>
  );
}
