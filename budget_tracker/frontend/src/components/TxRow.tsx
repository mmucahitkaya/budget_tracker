import type { Tx } from "../lib/api";
import { money } from "../lib/format";
import { useLookups } from "../lib/queries";
import { CatBadge, Row } from "./ui";

export default function TxRow({ tx, onClick, showUser = true }: { tx: Tx; onClick?: () => void; showUser?: boolean }) {
  const { catById, cardById, userById, me } = useLookups();
  const cat = tx.category_id ? catById.get(tx.category_id) : null;
  const card = tx.card_id ? cardById.get(tx.card_id) : null;
  const user = tx.user_id ? userById.get(tx.user_id) : null;
  const meta = [
    tx.user_note ? `📝 ${tx.user_note}` : null,
    tx.time,
    cat?.name ?? "Uncategorized",
    card ? card.name : tx.payment_method === "cash" ? "Cash" : tx.payment_method === "bank" ? "Bank" : tx.payment_method === "voucher" ? "Voucher" : null,
    showUser && user && (me?.users.length ?? 0) > 1 ? user.name.split(" ")[0] : null,
  ].filter(Boolean);

  return (
    <Row onClick={onClick}>
      <CatBadge cat={cat} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[17px]">{tx.merchant || tx.note || cat?.name || "Transaction"}</div>
        <div className="truncate text-[13px] text-label-2">{meta.join(" · ")}</div>
      </div>
      <div className="shrink-0 text-right">
        <div className={`tabular text-[17px] ${tx.kind === "income" ? "text-green" : ""}`}>
          {tx.kind === "income" ? "+" : "−"}
          {money(tx.amount, tx.currency)}
        </div>
        {/* Installment info below the amount: always visible even if the row is truncated */}
        {tx.installment_count && (
          <div className="tabular text-[12px] font-medium text-accent">
            {tx.installment_no ? `Installment ${tx.installment_no}/${tx.installment_count}` : `${tx.installment_count} installments · ${money(tx.installment_monthly ?? 0, tx.currency)}/mo`}
          </div>
        )}
      </div>
    </Row>
  );
}
