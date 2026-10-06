import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BudgetRow, type Planned, type Card, type Category, type Doc, type Me, type Recurring, type Summary, type Tx } from "./api";

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("me"), staleTime: 60_000 });

export const useCategories = () =>
  useQuery({ queryKey: ["categories"], queryFn: () => api.get<Category[]>("categories"), staleTime: 60_000 });

export const useCards = () => useQuery({ queryKey: ["cards"], queryFn: () => api.get<Card[]>("cards") });

export const useSummary = (month: string) =>
  useQuery({ queryKey: ["summary", month], queryFn: () => api.get<Summary>(`reports/summary?month=${month}`) });

export const useTransactions = (params: Record<string, string | number | undefined>) => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
  return useQuery({ queryKey: ["transactions", params], queryFn: () => api.get<Tx[]>(`transactions?${qs}`) });
};

export const useRecurring = () => useQuery({ queryKey: ["recurring"], queryFn: () => api.get<Recurring[]>("recurring") });

export const useBudgets = (month: string) =>
  useQuery({ queryKey: ["budgets", month], queryFn: () => api.get<BudgetRow[]>(`budgets?month=${month}`) });

export const useDocuments = () =>
  useQuery({
    queryKey: ["documents"],
    queryFn: () => api.get<Doc[]>("documents"),
    // Poll frequently while a document is processing to catch status changes
    refetchInterval: (q) =>
      q.state.data?.some((d) => d.status === "pending" || d.status === "processing") ? 2500 : 30_000,
  });

export function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    for (const k of [
      "transactions",
      "summary",
      "cards",
      "budgets",
      "recurring",
      "planned",
      "documents",
      "document",
      "categories",
      "merchants",
      "trend",
      "report-cats",
      "daily",
      "stats",
      "cat-trend",
      "audit",
      "investments",
      "savings",
      "settings",
      "cashflow",
      "spendable",
      "plan",
      "categorize",
      "insights",
      "txids",
      "report-workspace",
    ]) {
      qc.invalidateQueries({ queryKey: [k] });
    }
  };
}

export function useLookups() {
  const cats = useCategories().data ?? [];
  const cards = useCards().data ?? [];
  const me = useMe().data;
  const catById = new Map(cats.map((c) => [c.id, c]));
  const cardById = new Map(cards.map((c) => [c.id, c]));
  const userById = new Map((me?.users ?? []).map((u) => [u.id, u]));
  return { cats, cards, me, catById, cardById, userById };
}

export const usePlanned = (month: string) =>
  useQuery({ queryKey: ["planned", month], queryFn: () => api.get<Planned[]>(`recurring/planned?month=${month}`) });
