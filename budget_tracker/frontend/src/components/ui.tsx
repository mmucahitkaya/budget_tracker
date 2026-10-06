import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { Category, Currency } from "../lib/api";
import { BASE, LOCALE, money, monthLabel, shiftMonth, thisMonth } from "../lib/format";
import { useCurrencies } from "../lib/settings";

// ---- Icons (SF Symbols-like, inline SVG) ----------------------------------

const paths: Record<string, ReactNode> = {
  home: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  list: (
    <>
      <path d="M8 6h13M8 12h13M8 18h13" />
      <circle cx="3.5" cy="6" r=".9" fill="currentColor" />
      <circle cx="3.5" cy="12" r=".9" fill="currentColor" />
      <circle cx="3.5" cy="18" r=".9" fill="currentColor" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  card: (
    <>
      <rect x="2.5" y="5" width="19" height="14" rx="2.5" />
      <path d="M2.5 10h19M6 15h4" />
    </>
  ),
  more: (
    <>
      <circle cx="5" cy="12" r="1.3" fill="currentColor" />
      <circle cx="12" cy="12" r="1.3" fill="currentColor" />
      <circle cx="19" cy="12" r="1.3" fill="currentColor" />
    </>
  ),
  chevronRight: <path d="m9 5 7 7-7 7" />,
  chevronLeft: <path d="m15 5-7 7 7 7" />,
  chevronDown: <path d="m5 9 7 7 7-7" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  camera: (
    <>
      <path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" />
      <circle cx="12" cy="13.5" r="3.5" />
    </>
  ),
  doc: (
    <>
      <path d="M6 3h8l5 5v13H6z" />
      <path d="M14 3v5h5M9 13h7M9 17h5" />
    </>
  ),
  repeat: <path d="M17 2l3 3-3 3M20 5H8a4 4 0 0 0-4 4v1M7 22l-3-3 3-3M4 19h12a4 4 0 0 0 4-4v-1" />,
  chart: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  target: (
    <>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1" fill="currentColor" />
    </>
  ),
  tag: (
    <>
      <path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z" />
      <circle cx="7.5" cy="7.5" r="1.3" />
    </>
  ),
  download: <path d="M12 3v12m0 0-5-5m5 5 5-5M4 21h16" />,
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  check: <path d="m5 12 5 5 9-10" />,
  bell: <path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 21h4" />,
  filter: <path d="M3 5h18l-7 8v6l-4 2v-8z" />,
  pencil: <path d="M4 20h4L19 9l-4-4L4 16zM14 6l4 4" />,
  refresh: <path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5" />,
  warning: <path d="M12 3 2 21h20zM12 10v5M12 18v.5" />,
  send: <path d="M21 3 3 10.5l7 2.5 2.5 7zM21 3 10 13" />,
  gear: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </>
  ),
};

export function Icon({ name, size = 22, className = "", stroke = 2 }: { name: string; size?: number; className?: string; stroke?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
      aria-hidden
    >
      {paths[name]}
    </svg>
  );
}

// ---- Page header --------------------------------------------------------------

export function PageHeader({ title, right, left, sub }: { title: string; right?: ReactNode; left?: ReactNode; sub?: ReactNode }) {
  return (
    <header className="pt-safe px-4">
      <div className="flex h-11 items-center justify-between gap-2">
        <div className="min-w-0 flex-1">{left}</div>
        <div className="flex shrink-0 items-center gap-1">{right}</div>
      </div>
      <h1 className="text-[34px] font-bold leading-tight tracking-tight">{title}</h1>
      {sub && <div className="mt-0.5 text-[15px] text-label-2">{sub}</div>}
    </header>
  );
}

export function IconButton({ name, onClick, label, className = "" }: { name: string; onClick?: () => void; label: string; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className={`flex h-11 min-w-11 items-center justify-center rounded-full text-accent active:opacity-50 ${className}`}
    >
      <Icon name={name} />
    </button>
  );
}

export function TextButton({ children, onClick, className = "", disabled }: { children: ReactNode; onClick?: () => void; className?: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`flex h-11 items-center px-1 text-[17px] text-accent active:opacity-50 disabled:opacity-30 ${className}`}
    >
      {children}
    </button>
  );
}

// Whether the browser supports the month picker (iOS Safari: yes; macOS Safari: no)
const MONTH_INPUT_SUPPORTED = (() => {
  if (typeof document === "undefined") return false;
  const i = document.createElement("input");
  i.setAttribute("type", "month");
  return i.type === "month";
})();

export function MonthSwitcher({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const current = thisMonth();
  function manual() {
    const v = prompt("Choose a month (YYYY-MM, e.g. 2025-10)", value)?.trim();
    if (!v) return;
    const m = /^(\d{4})-(\d{1,2})$/.exec(v);
    if (!m || +m[2] < 1 || +m[2] > 12) return toast("Format: 2025-10", "err");
    onChange(`${m[1]}-${m[2].padStart(2, "0")}`);
  }
  return (
    <div className="flex items-center">
      <IconButton name="chevronLeft" label="Previous month" onClick={() => onChange(shiftMonth(value, -1))} />
      {/* Tapping the month name opens a month/year picker (to jump a year back without paging) */}
      <label className="relative flex min-h-11 min-w-[7.5rem] cursor-pointer items-center justify-center rounded-lg text-[17px] font-semibold active:bg-fill">
        <span className="border-b border-dashed border-label-3">{monthLabel(value)}</span>
        {MONTH_INPUT_SUPPORTED ? (
          <input
            type="month"
            aria-label="Choose month"
            value={value}
            onChange={(e) => e.target.value && onChange(e.target.value)}
            className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
          />
        ) : (
          <button type="button" aria-label="Choose month" onClick={manual} className="absolute inset-0" />
        )}
      </label>
      <IconButton name="chevronRight" label="Next month" onClick={() => onChange(shiftMonth(value, 1))} />
      {value !== current && (
        <button type="button" onClick={() => onChange(current)} className="ml-1 h-8 shrink-0 rounded-full bg-fill px-2.5 text-[13px] font-semibold text-accent">
          This month
        </button>
      )}
    </div>
  );
}

// ---- Grouped list (iOS inset grouped) ------------------------------------

export function Section({ title, footer, children, action }: { title?: ReactNode; footer?: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="mx-4 mt-6">
      {(title || action) && (
        <div className="mb-1.5 flex min-h-6 items-end justify-between px-4">
          <h2 className="text-[13px] font-normal uppercase tracking-wide text-label-2">{title}</h2>
          {action}
        </div>
      )}
      <div className="overflow-hidden rounded-xl bg-card">{children}</div>
      {footer && <p className="mt-1.5 px-4 text-[13px] text-label-2">{footer}</p>}
    </section>
  );
}

export function Row({
  children,
  onClick,
  chevron,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  chevron?: boolean;
  className?: string;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={`relative flex min-h-11 w-full items-center gap-3 py-2.5 pl-4 pr-4 text-left after:absolute after:bottom-0 after:left-4 after:right-0 after:h-px after:bg-sep last:after:hidden ${
        onClick ? "active:bg-fill" : ""
      } ${className}`}
    >
      {children}
      {chevron && <Icon name="chevronRight" size={16} className="text-label-3" stroke={2.5} />}
    </Tag>
  );
}

export function CatBadge({ cat, size = 36 }: { cat?: Category | null; size?: number }) {
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-full"
      style={{ width: size, height: size, background: `${cat?.color ?? "#8E8E93"}26`, fontSize: size * 0.5 }}
    >
      {cat?.icon ?? "❔"}
    </span>
  );
}

// ---- Form fields ----------------------------------------------------------

export function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={`relative flex min-h-11 items-center gap-3 py-1.5 pl-4 pr-4 after:absolute after:bottom-0 after:left-4 after:right-0 after:h-px after:bg-sep last:after:hidden ${className}`}>
      <span className="shrink-0 text-[17px]">{label}</span>
      <div className="flex min-w-0 flex-1 justify-end text-right text-label-2">{children}</div>
    </label>
  );
}

export const inputCls = "w-full min-w-0 bg-transparent text-right text-[17px] text-label outline-none placeholder:text-label-3";
// Date fields size to content and align to the right of the row
export const dateCls = "min-w-0 bg-transparent text-right text-[17px] text-label outline-none";

export function Select<T extends string | number>({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: T | null | undefined;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  placeholder?: string;
}) {
  const current = options.find((o) => o.value === value);
  // Visible label is separate; a transparent native select sits on top (opens the iOS wheel picker)
  return (
    <div className="relative flex min-w-0 items-center justify-end gap-1">
      <span className={`truncate text-[17px] ${current ? "text-label-2" : "text-label-3"}`}>
        {current?.label ?? placeholder ?? "Select"}
      </span>
      <Icon name="chevronDown" size={14} className="text-label-3" stroke={2.5} />
      <select
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        value={value ?? ""}
        onChange={(e) => {
          const raw = e.target.value;
          const opt = options.find((o) => String(o.value) === raw);
          if (opt) onChange(opt.value);
        }}
      >
        {placeholder && (
          <option value="" disabled>
            {placeholder}
          </option>
        )}
        {options.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={(e) => {
        e.preventDefault();
        onChange(!checked);
      }}
      className={`relative h-[31px] w-[51px] shrink-0 rounded-full transition-colors ${checked ? "bg-green" : "bg-fill"}`}
    >
      <span
        className={`absolute left-0 top-[2px] h-[27px] w-[27px] rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-[22px]" : "translate-x-[2px]"
        }`}
      />
    </button>
  );
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  className = "",
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  className?: string;
}) {
  return (
    <div className={`flex rounded-[9px] bg-fill p-[2px] ${className}`}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={`h-8 flex-1 rounded-[7px] px-2 text-[13px] font-medium transition-colors ${
            value === o.value ? "bg-seg shadow-sm" : "text-label"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Currency choice limited to the household's enabled currencies (Settings). The current value is always offered,
 * even if it was disabled later. Up to 4 options render as a segmented control, more as a picker.
 */
export function CurrencyPicker({
  value,
  onChange,
  options: only,
}: {
  value: Currency;
  onChange: (c: Currency) => void;
  /** Restrict to these currencies instead of the enabled ones */
  options?: Currency[];
}) {
  const enabled = useCurrencies();
  const list = [...(only ?? enabled)];
  if (value && !list.includes(value)) list.push(value);
  const opts = list.map((c) => ({ value: c, label: c }));
  if (opts.length <= 1) return <span className="text-[17px] text-label-2">{value}</span>;
  if (opts.length > 4) return <Select value={value} onChange={onChange} options={opts} />;
  return <Segmented value={value} onChange={onChange} className={opts.length > 3 ? "w-48" : opts.length === 3 ? "w-40" : "w-28"} options={opts} />;
}

/**
 * Editable list of per-currency amounts (card balances, statement totals). Each row: currency + amount text.
 * Rows are kept as text so partially typed amounts are not lost; `onChange` receives the rows.
 */
export interface AmountRow {
  currency: Currency;
  text: string;
}

export function CurrencyAmountsEditor({
  rows,
  onChange,
  label = "Amount",
  addLabel = "Add currency",
}: {
  rows: AmountRow[];
  onChange: (rows: AmountRow[]) => void;
  label?: string;
  addLabel?: string;
}) {
  const enabled = useCurrencies();
  const used = new Set(rows.map((r) => r.currency));
  const free = enabled.filter((c) => !used.has(c));
  const set = (i: number, patch: Partial<AmountRow>) => onChange(rows.map((r, j) => (i === j ? { ...r, ...patch } : r)));
  return (
    <>
      {rows.map((r, i) => {
        const choices = [r.currency, ...free];
        return (
          <div
            key={`${r.currency}-${i}`}
            className="relative flex min-h-11 items-center gap-2 py-1.5 pl-4 pr-2 after:absolute after:bottom-0 after:left-4 after:right-0 after:h-px after:bg-sep"
          >
            <span className="shrink-0 text-[17px]">{label}</span>
            <div className="w-[4.5rem] shrink-0">
              {choices.length > 1 ? (
                <Select value={r.currency} onChange={(c) => set(i, { currency: c })} options={choices.map((c) => ({ value: c, label: c }))} />
              ) : (
                <span className="text-[17px] text-label-2">{r.currency}</span>
              )}
            </div>
            <input
              className={inputCls}
              inputMode="decimal"
              placeholder="0"
              aria-label={`${label} (${r.currency})`}
              value={r.text}
              onChange={(e) => set(i, { text: e.target.value })}
            />
            {rows.length > 1 ? (
              <button
                type="button"
                aria-label={`Remove ${r.currency}`}
                onClick={() => onChange(rows.filter((_, j) => j !== i))}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-label-3 active:bg-fill"
              >
                <Icon name="close" size={16} stroke={2.5} />
              </button>
            ) : (
              <span className="w-2 shrink-0" />
            )}
          </div>
        );
      })}
      {free.length > 0 && (
        <button
          type="button"
          onClick={() => onChange([...rows, { currency: free[0], text: "" }])}
          className="flex min-h-11 w-full items-center gap-2 px-4 text-left text-[17px] text-accent active:bg-fill"
        >
          <Icon name="plus" size={20} /> {addLabel}
        </button>
      )}
    </>
  );
}

/** Currency dict → editor rows (non-zero amounts, `first` leading; at least one row). */
export function amountRows(d: Record<Currency, number> | null | undefined, first: Currency = BASE, toText: (n: number) => string = String): AmountRow[] {
  const entries = Object.entries(d ?? {}).filter(([, v]) => v);
  entries.sort(([a], [b]) => (a === first ? -1 : b === first ? 1 : a.localeCompare(b)));
  const rows = entries.map(([currency, v]) => ({ currency, text: toText(v) }));
  return rows.length ? rows : [{ currency: first, text: "" }];
}

/** Editor rows → currency dict (empty / invalid amounts dropped, duplicates summed). */
export function rowsToAmounts(rows: AmountRow[], parse: (s: string) => number): Record<Currency, number> {
  const out: Record<Currency, number> = {};
  for (const r of rows) {
    if (!r.text.trim()) continue;
    const n = parse(r.text);
    if (!Number.isFinite(n) || n === 0) continue;
    out[r.currency] = Math.round(((out[r.currency] ?? 0) + n) * 100) / 100;
  }
  return out;
}

/** Per-currency amounts as compact lines/chips: "$1,200.00 · €35.00". */
export function AmountsList({
  amounts,
  first = BASE,
  compact = false,
  inline = false,
  empty,
  format,
}: {
  amounts: Record<Currency, number> | null | undefined;
  first?: Currency;
  compact?: boolean;
  inline?: boolean;
  empty?: ReactNode;
  format?: (n: number, c: Currency, compact: boolean) => string;
}) {
  const fmt = format ?? money;
  const entries = Object.entries(amounts ?? {}).filter(([, v]) => Math.abs(v) >= 0.005);
  entries.sort(([a], [b]) => (a === first ? -1 : b === first ? 1 : a.localeCompare(b)));
  if (!entries.length) return <>{empty ?? fmt(0, first, compact)}</>;
  if (inline) return <>{entries.map(([c, v]) => fmt(v, c, compact)).join(" · ")}</>;
  return (
    <>
      {entries.map(([c, v], i) => (
        <div key={c} className={i > 0 ? "text-label-2" : undefined}>
          {fmt(v, c, compact)}
        </div>
      ))}
    </>
  );
}

export function PrimaryButton({
  children,
  onClick,
  disabled,
  tone = "accent",
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  tone?: "accent" | "red" | "plain";
  type?: "button" | "submit";
}) {
  const toneCls = tone === "red" ? "bg-card text-red" : tone === "plain" ? "bg-card text-accent" : "bg-accent text-white";
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`flex h-[50px] w-full items-center justify-center gap-2 rounded-xl text-[17px] font-semibold active:opacity-70 disabled:opacity-40 ${toneCls}`}
    >
      {children}
    </button>
  );
}

export function Empty({ icon, title, text }: { icon: string; title: string; text?: string }) {
  return (
    <div className="flex flex-col items-center px-8 py-14 text-center text-label-2">
      <Icon name={icon} size={44} stroke={1.5} className="mb-3 text-label-3" />
      <div className="text-[17px] font-semibold text-label">{title}</div>
      {text && <div className="mt-1 text-[15px]">{text}</div>}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return <span className={`inline-block h-5 w-5 animate-spin rounded-full border-2 border-label-3 border-t-label-2 ${className}`} />;
}

export function Progress({ ratio, className = "" }: { ratio: number; className?: string }) {
  const color = ratio >= 1 ? "bg-red" : ratio >= 0.8 ? "bg-orange" : "bg-green";
  return (
    <div className={`h-1.5 overflow-hidden rounded-full bg-fill ${className}`}>
      <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(100, ratio * 100)}%` }} />
    </div>
  );
}

// ---- Bottom sheet -------------------------------------------------------------

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Sheet({
  open,
  onClose,
  title,
  left,
  right,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  left?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  // If a field inside changed, tapping outside / Cancel / Escape asks for confirmation first
  const [dirty, setDirty] = useState(false);

  const requestClose = useCallback(() => {
    if (dirty && !confirm("You have unsaved changes. Close anyway?")) return;
    onClose();
  }, [dirty, onClose]);

  useEffect(() => {
    if (!open) return;
    setDirty(false);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const returnTo = document.activeElement as HTMLElement | null;
    // If the form didn't focus one of its fields, focus the panel (screen readers announce the title)
    const t = setTimeout(() => {
      if (!panelRef.current?.contains(document.activeElement)) panelRef.current?.focus();
    }, 320);
    return () => {
      clearTimeout(t);
      document.body.style.overflow = prevOverflow;
      returnTo?.focus?.();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        requestClose();
      } else if (e.key === "Tab" && panelRef.current) {
        // Keep focus inside the panel
        const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, requestClose]);

  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col justify-end">
      <div className="animate-fade absolute inset-0 bg-black/40" onClick={requestClose} aria-hidden />
      <div
        ref={panelRef}
        tabIndex={-1}
        className="animate-sheet relative flex max-h-[calc(100dvh-max(env(safe-area-inset-top),12px)-8px)] flex-col rounded-t-[14px] bg-bg outline-none"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-14 shrink-0 items-center justify-between gap-2 px-4">
          <div className="flex min-w-16 justify-start">{left ?? <TextButton onClick={requestClose}>Cancel</TextButton>}</div>
          <h2 id={titleId} className="truncate text-[17px] font-semibold">
            {title}
          </h2>
          <div className="flex min-w-16 justify-end">{right}</div>
        </div>
        <div className="pb-safe min-h-0 flex-1 overflow-y-auto overscroll-contain" onInputCapture={() => setDirty(true)} onClickCapture={e => { if ((e.target as HTMLElement).closest("[aria-pressed], [role=switch]")) setDirty(true); }}>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Shown when data fails to load (instead of an empty list or an endless spinner). */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center px-8 py-14 text-center" role="alert">
      <Icon name="warning" size={40} stroke={1.6} className="mb-3 text-orange" />
      <div className="text-[17px] font-semibold">Couldn't load</div>
      <div className="mt-1 text-[15px] text-label-2">{(error as Error)?.message || "Unknown error"}</div>
      <button type="button" onClick={onRetry} className="mt-4 h-11 rounded-full bg-card px-5 text-[15px] font-semibold text-accent active:opacity-60">
        Try again
      </button>
    </div>
  );
}

// ---- Notification (toast) ---------------------------------------------------

let pushToast: ((msg: string, tone?: "ok" | "err") => void) | null = null;
export const toast = (msg: string, tone: "ok" | "err" = "ok") => pushToast?.(msg, tone);

export function ToastHost() {
  const [items, setItems] = useState<{ id: number; msg: string; tone: "ok" | "err" }[]>([]);
  useEffect(() => {
    pushToast = (msg, tone = "ok") => {
      const id = Date.now() + Math.random();
      setItems((x) => [...x, { id, msg, tone }]);
      setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 3200);
    };
    return () => {
      pushToast = null;
    };
  }, []);
  return createPortal(
    <div role="status" aria-live="polite" className="pointer-events-none fixed inset-x-0 top-0 z-[60] flex flex-col items-center gap-2 px-4 pt-[max(env(safe-area-inset-top),12px)]">
      {items.map((i) => (
        <div
          key={i.id}
          className={`animate-fade max-w-md rounded-2xl px-4 py-3 text-[15px] font-medium text-white shadow-lg ${
            i.tone === "err" ? "bg-red" : "bg-[#1c1c1e]/90"
          }`}
        >
          {i.msg}
        </div>
      ))}
    </div>,
    document.body,
  );
}

/** Shown when foreign-currency amounts can't be added to totals because no exchange rate was found. */
export function FxWarning({ missing, currenciesOnly = false }: { missing?: { currency: Currency; amount: number }[]; currenciesOnly?: boolean }) {
  if (!missing?.length) return null;
  const list = missing.map((m) => `${new Intl.NumberFormat(LOCALE, { style: "currency", currency: m.currency }).format(m.amount)}`).join(", ");
  return (
    <div className="mx-4 mt-3 flex gap-2 rounded-xl bg-orange/15 p-3 text-[13px]">
      <Icon name="warning" size={18} className="text-orange" />
      <span>
        {currenciesOnly ? `Exchange rate missing: ${missing.map(m => m.currency).join(", ")}. ${BASE} totals and historical charts may be incomplete.` : `Exchange rate missing: ${list} could not be included in ${BASE} totals.`} This fills in automatically once rates are fetched online.
      </span>
    </div>
  );
}
