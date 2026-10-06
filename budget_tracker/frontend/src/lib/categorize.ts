import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export interface PendingOrder {
  key: string;
  tx_ids: number[];
  date: string;
  amount: number;
  merchant: string;
  card_id: number | null;
  installment_count: number | null;
  monthly: number | null;
  /** Latest installment posted to a statement so far */
  current_no: number | null;
  /** User note (what was bought) */
  note: string;
}

export interface PendingGroup {
  key: string;
  merchant: string;
  /** Marketplace: each order is a separate card; no merchant rule is learned */
  per_order: boolean;
  orders: PendingOrder[];
  tx_ids: number[];
  total: number;
  first: string;
  last: string;
  cards: number[];
  count: number;
  suggestion: number | null;
}
export interface PendingData {
  groups: PendingGroup[];
  count: number;
  favorites: number[];
}

/** "Other"/uncategorized expenses (grouped by merchant). */
export const usePendingCategories = () =>
  useQuery({ queryKey: ["categorize"], queryFn: () => api.get<PendingData>("transactions/categorize/pending") });
