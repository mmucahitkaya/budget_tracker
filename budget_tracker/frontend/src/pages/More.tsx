import { useState } from "react";
import { usePendingCategories } from "../lib/categorize";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon, PageHeader, PrimaryButton, Row, Section, Sheet, Spinner, TextButton, toast } from "../components/ui";
import { api, type Me } from "../lib/api";
import { thisMonth } from "../lib/format";
import { useDocuments, useMe } from "../lib/queries";
import { useSettings } from "../lib/settings";

function Item({ icon, color, label, to, badge, onClick }: { icon: string; color: string; label: string; to?: string; badge?: number; onClick?: () => void }) {
  const nav = useNavigate();
  return (
    <Row chevron onClick={onClick ?? (() => to && nav(to))}>
      <span className="flex h-[30px] w-[30px] items-center justify-center rounded-[7px] text-white" style={{ background: color }}>
        <Icon name={icon} size={18} />
      </span>
      <span className="flex-1 text-[17px]">{label}</span>
      {!!badge && <span className="min-w-[22px] rounded-full bg-red px-1.5 text-center text-[13px] leading-[22px] text-white">{badge}</span>}
    </Row>
  );
}

export default function More() {
  const docs = useDocuments().data ?? [];
  const me = useMe().data;
  const review = docs.filter((d) => d.status === "review").length;
  const [tg, setTg] = useState(false);
  const pending = usePendingCategories().data?.count ?? 0;
  const settings = useSettings().data;
  return (
    <div>
      <PageHeader title="More" sub={me ? `Signed in as ${me.me.name}` : undefined} />
      <Section>
        <Item icon="doc" color="#FF9500" label="Documents" to="/documents" badge={review} />
        <Item icon="tag" color="#FF2D55" label="Categorize" to="/categorize" badge={pending} />
        <Item icon="target" color="#34C759" label="Insights (biggest expenses)" to="/insights" />
        <Item icon="chart" color="#5E5CE6" label="Plan (upcoming months)" to="/plan" />
        <Item icon="repeat" color="#FF3B30" label="Recurring Expenses" to="/recurring/expenses" />
        <Item icon="repeat" color="#34C759" label="Recurring Income" to="/recurring/income" />
        <Item icon="target" color="#34C759" label="Savings & Goals" to="/savings" />
        {settings?.investments !== false && <Item icon="chart" color="#D4A017" label="Investments (gold, currency, stocks, funds)" to="/investments" />}
        <Item icon="target" color="#FF3B30" label="Budgets" to="/budgets" />
        <Item icon="chart" color="#007AFF" label="Reports" to="/reports" />
        <Item icon="tag" color="#AF52DE" label="Categories" to="/categories" />
        <Item icon="refresh" color="#636366" label="Change history" to="/history" />
        <Item icon="send" color="#2AABEE" label="Telegram bot" onClick={() => setTg(true)} />
      </Section>
      <Section footer={settings ? `${settings.base_currency} · ${settings.locale}${settings.region ? ` · ${settings.regions.find((r) => r.code === settings.region)?.name ?? settings.region} pack` : ""}` : undefined}>
        <Item icon="gear" color="#8E8E93" label="Settings" to="/settings" />
      </Section>
      <Section title="Export" footer="Excel-compatible CSV (semicolon-separated).">
        <Item icon="download" color="#8E8E93" label="This month's transactions (CSV)" onClick={() => window.open(`api/transactions/export.csv?month=${thisMonth()}`, "_blank")} />
        <Item icon="download" color="#8E8E93" label="All transactions (CSV)" onClick={() => window.open("api/transactions/export.csv", "_blank")} />
      </Section>
      <Section title="Status">
        <Row>
          <span className="flex-1 text-[17px]">Automatic receipt reading</span>
          <span className={`text-[15px] ${me?.ai_enabled ? "text-green" : "text-orange"}`}>{me?.ai_enabled ? "On" : "Not set up"}</span>
        </Row>
        <Row>
          <span className="flex-1 text-[17px]">Phone notifications</span>
          <span className={`text-[15px] ${me?.notify_enabled ? "text-green" : "text-orange"}`}>{me?.notify_enabled ? "On" : "Not configured"}</span>
        </Row>
      </Section>
      <p className="mx-8 mt-6 text-center text-[13px] text-label-3">Budget · Home Assistant add-on</p>
      {tg && <TelegramSheet onClose={() => setTg(false)} />}
    </div>
  );
}

function TelegramSheet({ onClose }: { onClose: () => void }) {
  // Show the server's current state on every open, not the stale cached one
  const meQ = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("me"),
    refetchOnMount: "always",
    // If the bot is just starting, poll until the username arrives
    refetchInterval: (q) => (q.state.data?.telegram.enabled && !q.state.data.telegram.bot ? 3000 : false),
  });
  const me = meQ.data;
  const qc = useQueryClient();
  const [code, setCode] = useState<{ code: string; bot: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const t = me?.telegram;

  async function getCode() {
    setBusy(true);
    try {
      setCode(await api.post<{ code: string; bot: string | null }>("telegram/link-code"));
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }
  async function unlink() {
    if (!confirm("Unlink Telegram?")) return;
    await api.post("telegram/unlink");
    qc.invalidateQueries({ queryKey: ["me"] });
    toast("Unlinked");
  }

  return (
    <Sheet open onClose={onClose} title="Telegram Bot" left={<TextButton onClick={onClose}>Close</TextButton>}>
      {meQ.isFetching && !t ? (
        <div className="flex justify-center py-16">
          <Spinner />
        </div>
      ) : !t?.enabled ? (
        <Section footer="Create a bot with @BotFather on Telegram, enter the token you get in the telegram_bot_token field of the add-on settings, then restart the add-on.">
          <Row>
            <span className="flex-1 text-[17px]">Status</span>
            <span className="text-[15px] text-orange">No bot token</span>
          </Row>
        </Section>
      ) : (
        <>
          <Section footer="Only linked accounts can use the bot; everyone else is ignored.">
            <Row>
              <span className="flex-1 text-[17px]">Bot</span>
              <span className="text-[15px] text-label-2">{t.bot ? `@${t.bot}` : "connecting…"}</span>
            </Row>
            <Row>
              <span className="flex-1 text-[17px]">Your account</span>
              <span className={`text-[15px] ${t.linked ? "text-green" : "text-label-2"}`}>{t.linked ? "Linked" : "Not linked"}</span>
            </Row>
            <Row>
              <span className="flex-1 text-[17px]">Linked people</span>
              <span className="truncate text-[15px] text-label-2">{t.linked_users.join(", ") || "—"}</span>
            </Row>
          </Section>

          {code ? (
            <Section title="Linking" footer="The code is valid for 10 minutes and can be used once.">
              <div className="px-4 py-4 text-center">
                <div className="tabular text-[40px] font-bold tracking-[0.2em]">{code.code}</div>
                <div className="mt-2 text-[15px] text-label-2">
                  Send this to {code.bot ? <b>@{code.bot}</b> : "your bot"} on Telegram:
                </div>
                <div className="mt-1 font-mono text-[17px]">/link {code.code}</div>
                {code.bot && (
                  <a
                    href={`https://t.me/${code.bot}?start=link_${code.code}`}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-4 inline-flex h-11 items-center rounded-full bg-[#2AABEE] px-5 text-[15px] font-semibold text-white"
                  >
                    Open in Telegram
                  </a>
                )}
              </div>
            </Section>
          ) : (
            <div className="mx-4 mt-6">
              <PrimaryButton onClick={getCode} disabled={busy}>
                {busy ? <Spinner /> : t.linked ? "Link another device" : "Get a linking code"}
              </PrimaryButton>
            </div>
          )}

          <Section title="What you can do with the bot">
            <div className="space-y-1.5 px-4 py-3 text-[15px]">
              <div>📸 Send a receipt photo / 📄 statement PDF → it gets read and saved with one tap</div>
              <div>✍️ Quick expense with a short message like <code>250 gas</code></div>
              <div>📊 /summary · 🧾 /recent · 📅 /upcoming</div>
              <div>🔔 Monthly summary, due date and budget notifications arrive here too</div>
            </div>
          </Section>

          {t.linked && (
            <div className="mx-4 mt-4">
              <PrimaryButton tone="red" onClick={unlink}>
                Unlink
              </PrimaryButton>
            </div>
          )}
        </>
      )}
    </Sheet>
  );
}
