import { useState } from "react";
import { api, type Category, type Kind } from "../lib/api";
import { useInvalidate } from "../lib/queries";
import { Field, PrimaryButton, Section, Sheet, TextButton, inputCls, toast } from "./ui";

export const CATEGORY_COLORS = ["#34C759", "#FF9500", "#5AC8FA", "#FFCC00", "#AF52DE", "#FF3B30", "#5856D6", "#FF2D55", "#A2845E", "#0A84FF", "#30B0C7", "#8E8E93"];
const EMOJIS = ["📦", "🎮", "📚", "🐶", "👶", "🎁", "🏋️", "🎵", "🧸", "🛠️", "🚗", "🏠", "💻", "📷", "⚽", "🎨", "🧴", "💊", "✈️", "☕"];

/** New category while categorizing or entering a transaction: name + emoji; selected once saved. */
export default function NewCategorySheet({ kind = "expense", onClose, onCreated }: { kind?: Kind; onClose: () => void; onCreated: (c: Category) => void }) {
  const invalidate = useInvalidate();
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("📦");
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!name.trim()) return toast("Category name is required", "err");
    setBusy(true);
    try {
      const color = CATEGORY_COLORS[Math.abs([...name].reduce((h, ch) => h * 31 + ch.charCodeAt(0), 7)) % CATEGORY_COLORS.length];
      const c = await api.post<Category>("categories", { name: name.trim(), kind, icon, color });
      invalidate();
      onCreated(c);
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open onClose={onClose} title="New Category" right={<TextButton onClick={save} disabled={busy}>Add</TextButton>}>
      <Section>
        <Field label="Name">
          <input className={inputCls} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Books, Games, Baby" />
        </Field>
      </Section>
      <Section title="Icon">
        <div className="grid grid-cols-10 gap-1 p-2">
          {EMOJIS.map((e) => (
            <button
              key={e}
              type="button"
              onClick={() => setIcon(e)}
              className={`flex h-9 items-center justify-center rounded-lg text-[20px] ${icon === e ? "bg-fill ring-2 ring-accent" : ""}`}
            >
              {e}
            </button>
          ))}
        </div>
        <Field label="Other emoji">
          <input className={inputCls} value={icon} onChange={(e) => setIcon([...e.target.value].slice(-2).join("") || "📦")} />
        </Field>
      </Section>
      <div className="mx-4 mt-6">
        <PrimaryButton onClick={save} disabled={busy}>
          Add and select
        </PrimaryButton>
      </div>
    </Sheet>
  );
}
