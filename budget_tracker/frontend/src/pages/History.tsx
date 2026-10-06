import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Empty, ErrorState, Icon, PageHeader, Row, Section, Spinner, TextButton, toast } from "../components/ui";
import { api } from "../lib/api";
import { dayLabel } from "../lib/format";
import { useInvalidate } from "../lib/queries";

interface Entry {
  id: number;
  at: string;
  user: string;
  summary: string;
  action: string;
  entity: string;
  undone: boolean;
  can_undo: boolean;
}

export default function History() {
  const nav = useNavigate();
  const invalidate = useInvalidate();
  const q = useQuery({ queryKey: ["audit"], queryFn: () => api.get<Entry[]>("audit?limit=150") });

  async function undo(e: Entry) {
    if (!confirm(`Undo this change?\n\n${e.summary}`)) return;
    try {
      await api.post(`audit/${e.id}/undo`);
      invalidate();
      q.refetch();
      toast("Undone");
    } catch (err) {
      toast((err as Error).message, "err");
    }
  }

  // Group by day
  const groups = new Map<string, Entry[]>();
  for (const e of q.data ?? []) {
    const day = e.at.slice(0, 10);
    groups.set(day, [...(groups.get(day) ?? []), e]);
  }

  return (
    <div>
      <PageHeader
        title="Change History"
        left={
          <TextButton onClick={() => nav("/more")}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> More
          </TextButton>
        }
      />
      <p className="mx-8 mt-1 text-[13px] text-label-2">
        Who changed what, and when. A change can be undone as long as nobody else has modified the record since.
      </p>
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.isLoading ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : !q.data?.length ? (
        <Empty icon="refresh" title="No changes yet" />
      ) : (
        [...groups.entries()].map(([day, list]) => (
          <Section key={day} title={dayLabel(day)}>
            {list.map((e) => (
              <Row key={e.id} className={e.undone || e.action === "undo" ? "opacity-50" : ""}>
                <div className="min-w-0 flex-1">
                  <div className={`text-[15px] ${e.undone ? "line-through" : ""}`}>{e.summary}</div>
                  <div className="text-[13px] text-label-2">
                    {e.user} · {e.at.slice(11, 16)}
                    {e.undone ? " · undone" : ""}
                  </div>
                </div>
                {e.can_undo && (
                  <button type="button" onClick={() => undo(e)} className="h-8 shrink-0 rounded-full bg-fill px-3 text-[13px] font-semibold text-accent">
                    Undo
                  </button>
                )}
              </Row>
            ))}
          </Section>
        ))
      )}
    </div>
  );
}
