import type { Currency } from "./api";
export interface Account {
  id: number;
  name: string;
  kind: "cash" | "bank" | "savings";
  currency: Currency;
  opening_balance: number;
  opening_date: string;
  owner_id: number | null;
  archived: boolean;
  balance: number;
  allocated: number;
  available: number;
  updated_on: string;
}
export interface Goal {
  invested: number;
  funded: number;
  id: number;
  name: string;
  currency: Currency;
  target: number;
  target_date: string | null;
  monthly: number;
  reminder_day: number | null;
  owner_id: number | null;
  emergency: boolean;
  archived: boolean;
  allocated: number;
  remaining: number;
  required_monthly: number | null;
  overdue: boolean;
  month_allocated: number;
  contributions: { user_id: number; amount: number }[];
  accounts: { account_id: number; amount: number }[];
}
export interface Savings {
  investments: number;
  accounts: Account[];
  goals: Goal[];
  savings: number;
  assets: number;
  card_debt: number;
  net_worth: number;
  available_cash: number;
  month_contribution: number;
  essential_monthly: number;
  emergency_months: number | null;
  as_of: string;
  by_currency: Partial<Record<Currency, { balance: number; allocated: number; available: number }>>;
  fx_missing: { currency: Currency; amount: number }[];
  series: {
    month: string;
    balance: number;
    contribution: number;
    deposit: number;
    withdrawal: number;
    adjustment: number;
    valuation_change: number;
  }[];
  movements: {
    id: number;
    source_id: number | null;
    target_id: number | null;
    source_amount: number;
    target_amount: number;
    date: string;
    kind: string;
    note: string;
    user_id: number;
  }[];
}
