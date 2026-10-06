import { amountEntries, type Card } from "../lib/api";
import { BASE, daysUntil, dayLabel, money } from "../lib/format";

export default function CardTile({ card, onClick }: { card: Card; onClick?: () => void }) {
  const due = card.unpaid_statement?.due_date ?? card.next_due_date;
  const d = daysUntil(due);
  const debts = amountEntries(card.debts, BASE);
  const [main, ...rest] = debts;
  const hasDebt = debts.some(([, v]) => v > 0);
  // Limit usage = limit − available (the server converts foreign balances to the base currency)
  const used = card.limit && card.available !== null ? Math.max(0, Math.min(1, (card.limit - card.available) / card.limit)) : null;
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex min-h-[200px] w-full flex-col justify-between gap-4 overflow-hidden rounded-2xl p-4 text-left text-white shadow-sm active:scale-[0.99]"
      style={{ background: `linear-gradient(135deg, ${card.color}, ${card.color}cc 55%, #00000066)` }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[20px] font-semibold">{card.name}</div>
          <div className="truncate text-[13px] opacity-80">{card.bank}</div>
        </div>
        {card.last4 && <div className="tabular shrink-0 text-[15px] opacity-90">•••• {card.last4}</div>}
      </div>
      <div>
        <div className="text-[12px] uppercase tracking-wide opacity-75">Current balance</div>
        <div className="flex items-end justify-between gap-2">
          <div className="min-w-0">
            <div className="tabular truncate text-[26px] font-bold leading-tight">{main ? money(main[1], main[0]) : money(0)}</div>
            {rest.length > 0 && (
              <div className="mt-0.5 flex flex-wrap gap-1">
                {rest.map(([c, v]) => (
                  <span key={c} className="tabular rounded-full bg-white/20 px-2 py-0.5 text-[12px] font-medium">
                    {money(v, c)}
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="shrink-0 text-right text-[13px]">
            <div className="opacity-75">Due</div>
            <div className={`font-semibold ${d <= 3 && hasDebt ? "rounded bg-white/25 px-1.5" : ""}`}>{dayLabel(due)}</div>
          </div>
        </div>
        {used !== null && (
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/25">
            <div className="h-full rounded-full bg-white" style={{ width: `${used * 100}%` }} />
          </div>
        )}
      </div>
    </button>
  );
}
