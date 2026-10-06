import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Currency } from "../lib/api";
import { BASE, money, shortDate } from "../lib/format";
import { ErrorState, FxWarning, Icon, Row, Section, Segmented, Spinner, TextButton } from "./ui";
const EXPENSE = "var(--accent)";
const axisTick = { fill: "var(--label-2)", fontSize: 11 };
const compact = (n: number) => (Math.abs(n) >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
const tooltipStyle = { contentStyle: { background: "var(--card)", color: "var(--label)", borderRadius: 12 } };
interface Cashflow {
  opening_balance: number | null;
  minimum_balance: number | null;
  in: number;
  out: number;
  net: number;
  events: { date: string; title: string; amount: number; direction: "in" | "out"; kind: string; estimated: boolean; note: string }[];
  series: { date: string; net: number }[];
  fx_missing: { currency: Currency; amount: number }[];
}

export default function CashflowSection({ filters }: { filters: string }) {
  const [all, setAll] = useState(false);
  const [days, setDays] = useState<"30" | "60" | "90">("30");
  const q = useQuery({
    queryKey: ["cashflow", days, filters],
    queryFn: () => api.get<Cashflow>(`reports/cashflow?days=${days}&${filters}`),
  });
  const cf = q.data;
  const series = (cf?.series ?? []).map((p) => ({ ...p, label: shortDate(p.date) }));
  return (
    <Section
      title="Cash flow (upcoming days)"
      footer="Expected income, recurring payments not paid by card, and card due payments. Card spending is not counted separately; the estimated statement is deducted on the card's due date. For later periods, estimates include only installments and recurring payments."
    >
      <div className="px-4 pt-3">
        <Segmented
          value={days}
          onChange={setDays}
          options={[
            { value: "30", label: "30 days" },
            { value: "60", label: "60 days" },
            { value: "90", label: "90 days" },
          ]}
        />
      </div>
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !cf ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : (
        <>
          <FxWarning missing={cf.fx_missing} />
          {cf.opening_balance !== null && (
            <p className="px-4 pt-3 text-[13px]">
              Tracked free cash {money(cf.opening_balance)} · lowest expected balance {money(cf.minimum_balance ?? 0)}
            </p>
          )}
          <div className="grid grid-cols-3 gap-2 p-3">
            <div className="min-w-0 rounded-xl bg-card-2 p-2.5">
              <div className="text-[12px] text-label-2">In</div>
              <div className="tabular truncate text-[15px] font-semibold text-green">{money(cf.in, BASE, true)}</div>
            </div>
            <div className="min-w-0 rounded-xl bg-card-2 p-2.5">
              <div className="text-[12px] text-label-2">Out</div>
              <div className="tabular truncate text-[15px] font-semibold">{money(cf.out, BASE, true)}</div>
            </div>
            <div className="min-w-0 rounded-xl bg-card-2 p-2.5">
              <div className="text-[12px] text-label-2">Net</div>
              <div className={`tabular truncate text-[15px] font-semibold ${cf.net < 0 ? "text-red" : ""}`}>
                {money(cf.net, BASE, true)}
              </div>
            </div>
          </div>
          <div className="px-2">
            <div className="h-40">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={series} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="var(--sep)" />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} tick={axisTick} interval={Math.ceil(series.length / 5)} />
                  <YAxis tickLine={false} axisLine={false} tick={axisTick} tickFormatter={compact} width={44} />
                  <ReferenceLine y={0} stroke="var(--label-3)" />
                  <Tooltip {...tooltipStyle} formatter={(v: number) => [money(v, BASE, true), "Cumulative net"]} />
                  <Line dataKey="net" stroke={EXPENSE} strokeWidth={2} dot={false} activeDot={{ r: 4 }} type="stepAfter" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
          {cf.events.length === 0 ? (
            <div className="px-4 py-3 text-[15px] text-label-2">No payments or income expected in this period.</div>
          ) : (
            cf.events.slice(0, all ? undefined : 25).map((e, i) => (
              <Row key={`${e.date}-${e.title}-${i}`}>
                <span
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
                    e.direction === "in"
                      ? "bg-green/15 text-green"
                      : e.kind === "recurring"
                        ? "bg-fill text-label-2"
                        : "bg-accent/15 text-accent"
                  }`}
                >
                  <Icon name={e.direction === "in" ? "plus" : e.kind === "recurring" ? "repeat" : "card"} size={17} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[15px]">{e.title}</div>
                  <div className="truncate text-[12px] text-label-2">
                    {shortDate(e.date)}
                    {e.estimated ? " · estimated" : ""}
                    {e.note ? ` · ${e.note}` : ""}
                  </div>
                </div>
                <span className={`tabular shrink-0 text-[15px] ${e.direction === "in" ? "text-green" : ""}`}>
                  {e.direction === "in" ? "+" : "−"}
                  {money(e.amount, BASE, true)}
                </span>
              </Row>
            ))
          )}
          {cf.events.length > 25 && <TextButton onClick={() => setAll(!all)}>{all ? "Show less" : "Show all payments"}</TextButton>}
        </>
      )}
    </Section>
  );
}
