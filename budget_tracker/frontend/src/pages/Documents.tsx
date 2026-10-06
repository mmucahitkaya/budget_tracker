import { useNavigate } from "react-router-dom";
import { useAdd } from "../App";
import { Empty, Icon, IconButton, PageHeader, Row, Section, Spinner, TextButton, ErrorState } from "../components/ui";
import type { Doc } from "../lib/api";
import { LOCALE } from "../lib/format";
import { useDocuments } from "../lib/queries";

export const STATUS: Record<Doc["status"], { label: string; cls: string }> = {
  pending: { label: "Queued", cls: "text-label-2" },
  processing: { label: "Reading…", cls: "text-label-2" },
  review: { label: "Needs review", cls: "text-orange" },
  done: { label: "Saved", cls: "text-green" },
  error: { label: "Error", cls: "text-red" },
  discarded: { label: "Discarded", cls: "text-label-3" },
};

export default function Documents() {
  const { data, isLoading, isError, error, refetch } = useDocuments();
  const nav = useNavigate();
  const { openAdd } = useAdd();

  return (
    <div>
      <PageHeader
        title="Documents"
        left={
          <TextButton onClick={() => nav("/more")}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> More
          </TextButton>
        }
        right={<IconButton name="camera" label="Add document" onClick={() => openAdd("upload")} />}
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : !data?.length ? (
        <Empty icon="doc" title="No documents" text="Upload a receipt photo or a card statement (PDF); line items are read automatically." />
      ) : (
        <Section>
          {data.map((d) => {
            const st = STATUS[d.status];
            return (
              <Row key={d.id} chevron onClick={() => nav(`/documents/${d.id}`)}>
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-fill text-label-2">
                  {d.status === "pending" || d.status === "processing" ? <Spinner /> : <Icon name={d.mime === "application/pdf" ? "doc" : "camera"} size={19} />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[17px]">
                    {d.doc_type === "statement" ? "Statement" : d.doc_type === "receipt" ? "Receipt" : d.filename}
                  </div>
                  <div className="truncate text-[13px] text-label-2">
                    {d.created_at ? new Date(d.created_at.replace(" ", "T") + "Z").toLocaleString(LOCALE, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}
                    {d.row_count ? ` · ${d.row_count} items` : ""}
                  </div>
                </div>
                <span className={`shrink-0 text-[15px] ${st.cls}`}>{st.label}</span>
              </Row>
            );
          })}
        </Section>
      )}
    </div>
  );
}
