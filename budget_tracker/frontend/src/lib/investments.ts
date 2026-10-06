import { LOCALE } from "./format";

export type AssetKind = "gold" | "silver" | "metal" | "fx" | "stock" | "fund";

export interface Lot {
  id: number;
  side: "buy" | "sell";
  date: string;
  quantity: number;
  unit_price: number;
  total: number;
  user_id: number | null;
  note: string;
}

export interface Holding {
  id: number;
  kind: AssetKind;
  kind_label: string;
  code: string;
  name: string;
  unit: string;
  owner_id: number | null;
  goal_id: number | null;
  note: string;
  /** "" = physical (held in hand); non-empty = bank/brokerage */
  location: string;
  archived: boolean;
  lots: Lot[];
  quantity: number | null;
  price: number | null;
  price_date: string | null;
  price_source: string | null;
  stale: boolean;
  value: number;
  cost: number | null;
  pl: number | null;
  pl_pct: number | null;
  realized: number;
  missing_price: boolean;
  gross_gram?: number;
  pure_gram?: number;
}

export interface MetalSummary {
  gross_gram: number;
  pure_gram: number;
  value: number;
  base_equivalent: number | null;
  base_name: string;
}

export interface CatalogItem {
  code: string;
  name: string;
  unit: string;
  gram?: number;
  purity?: number;
}

/** Gram display: 22.8 g */
export function grams(n: number): string {
  return `${new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 }).format(n)} g`;
}

export interface Portfolio {
  assets: Holding[];
  value: number;
  cost: number;
  pl: number | null;
  pl_pct: number | null;
  by_kind: { kind: AssetKind; label: string; value: number }[];
  by_owner: { user_id: number | null; value: number }[];
  missing_price: string[];
  metals: Partial<Record<"gold" | "silver", MetalSummary>>;
  history: { date: string; value: number; cost: number }[];
  catalog: Record<"gold" | "silver" | "metal" | "fx", CatalogItem[]>;
}

export const KIND_OPTIONS: { value: AssetKind; label: string }[] = [
  { value: "gold", label: "Gold" },
  { value: "silver", label: "Silver" },
  { value: "metal", label: "Metal" },
  { value: "fx", label: "Currency" },
  { value: "stock", label: "Stock" },
  { value: "fund", label: "Fund" },
];

export const KIND_COLOR: Record<AssetKind, string> = {
  gold: "#D4A017",
  silver: "#8E8E93",
  metal: "#AC8E68",
  fx: "#30B0C7",
  stock: "#5E5CE6",
  fund: "#34C759",
};

/** Gold subgroup: physical holdings by note (e.g. Wedding jewelry, Savings), bank holdings by bank name. */
export function holdingGroup(a: Holding): { key: string; title: string; physical: boolean } {
  if (a.location) return { key: `b:${a.location}`, title: `${a.location} · bank`, physical: false };
  const name = a.note.trim() || "Savings";
  return { key: `f:${name}`, title: `${name} · physical`, physical: true };
}

/** Physical first, then bank; sorted by name within each. */
export function groupHoldings(items: Holding[]) {
  const map = new Map<string, { key: string; title: string; physical: boolean; items: Holding[] }>();
  for (const a of items) {
    const g = holdingGroup(a);
    if (!map.has(g.key)) map.set(g.key, { ...g, items: [] });
    map.get(g.key)!.items.push(a);
  }
  return [...map.values()].sort((x, y) => Number(y.physical) - Number(x.physical) || x.title.localeCompare(y.title, LOCALE));
}

/** Account group: bank holdings by bank + owner, physical ones by group name. */
export interface AccountGroup {
  key: string;
  title: string;
  subtitle: string;
  physical: boolean;
  items: Holding[];
}

export function groupByAccount(items: Holding[], ownerName: (id: number | null) => string): AccountGroup[] {
  const map = new Map<string, AccountGroup>();
  for (const a of items) {
    const g = a.location
      ? { key: `b:${a.location}:${a.owner_id ?? 0}`, title: a.location, subtitle: `Bank · ${ownerName(a.owner_id)}`, physical: false }
      : { key: `f:${a.note.trim() || "Savings"}`, title: a.note.trim() || "Savings", subtitle: "Physical", physical: true };
    if (!map.has(g.key)) map.set(g.key, { ...g, items: [] });
    map.get(g.key)!.items.push(a);
  }
  const order = KIND_OPTIONS.map((k) => k.value);
  for (const g of map.values()) g.items.sort((x, y) => order.indexOf(x.kind) - order.indexOf(y.kind) || x.name.localeCompare(y.name, LOCALE));
  return [...map.values()].sort((x, y) => Number(y.physical) - Number(x.physical) || x.title.localeCompare(y.title, LOCALE));
}

/** Quantity: no unnecessary decimals for grams/units. */
export function qty(n: number | null, unit: string): string {
  if (n === null) return "";
  return `${new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 4 }).format(n)} ${unit}`;
}
