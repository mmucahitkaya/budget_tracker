import { useState } from "react";
import { useNavigate } from "react-router-dom";
import CardForm from "../components/CardForm";
import CardTile from "../components/CardTile";
import { Empty, IconButton, PageHeader, Section, Spinner, Row, ErrorState } from "../components/ui";
import type { Currency } from "../lib/api";
import { BASE, money, monthLabel } from "../lib/format";
import { useCards } from "../lib/queries";

export default function Cards() {
  const { data: cards, isLoading, isError, error, refetch } = useCards();
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();

  // Upcoming monthly installment load across all cards
  const schedule = new Map<string, Partial<Record<Currency, number>>>();
  for (const c of cards ?? [])
    for (const s of c.schedule) {
      const m = schedule.get(s.month) ?? {};
      for (const [cur, v] of Object.entries(s.totals) as [Currency, number][]) m[cur] = (m[cur] ?? 0) + v;
      schedule.set(s.month, m);
    }
  const upcoming = [...schedule.entries()].filter(([, v]) => Object.keys(v).length > 0).slice(0, 12);
  const max = Math.max(1, ...upcoming.map(([, v]) => v[BASE] ?? 0));

  return (
    <div>
      <PageHeader title="Cards" right={<IconButton name="plus" label="Add card" onClick={() => setAdding(true)} />} />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : !cards?.length ? (
        <Empty icon="card" title="No cards yet" text="Add your credit card with the + at the top right." />
      ) : (
        <>
          <div className="mx-4 mt-4 space-y-3">
            {cards.map((c) => (
              <CardTile key={c.id} card={c} onClick={() => nav(`/cards/${c.id}`)} />
            ))}
          </div>
          {upcoming.length > 0 && (
            <Section title="Upcoming installment load" footer="Monthly total of active installments across all cards.">
              {upcoming.map(([m, v]) => (
                <Row key={m}>
                  <div className="w-16 shrink-0 text-[15px]">{monthLabel(m, true)}</div>
                  <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-fill">
                    <div className="h-full rounded-full bg-accent" style={{ width: `${((v[BASE] ?? 0) / max) * 100}%` }} />
                  </div>
                  <div className="tabular w-28 shrink-0 text-right text-[15px]">
                    {(Object.entries(v) as [Currency, number][]).map(([cur, n]) => (
                      <div key={cur}>{money(n, cur, true)}</div>
                    ))}
                  </div>
                </Row>
              ))}
            </Section>
          )}
        </>
      )}
      {adding && <CardForm open onClose={() => setAdding(false)} />}
    </div>
  );
}
