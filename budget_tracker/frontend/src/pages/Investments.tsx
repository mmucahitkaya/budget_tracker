import { Fragment, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  Empty,
  ErrorState,
  Field,
  Icon,
  IconButton,
  PageHeader,
  PrimaryButton,
  Row,
  Section,
  Segmented,
  Select,
  Sheet,
  Spinner,
  TextButton,
  Toggle,
  dateCls,
  inputCls,
  toast,
} from "../components/ui";
import { api } from "../lib/api";
import { KIND_COLOR, KIND_OPTIONS, grams, groupByAccount, groupHoldings, qty, type AssetKind, type Holding, type MetalSummary, type Portfolio } from "../lib/investments";
import { BASE, amountInput, money, parseAmount, shortDate, today, uid } from "../lib/format";
import { useInvalidate, useLookups } from "../lib/queries";
import { useSettings } from "../lib/settings";
import type { Savings } from "../lib/savings";

const tooltipStyle = {
  contentStyle: { background: "var(--card)", border: "none", borderRadius: 12, boxShadow: "0 4px 16px rgba(0,0,0,.15)", fontSize: 13 },
  labelStyle: { color: "var(--label)", fontWeight: 600 },
  itemStyle: { color: "var(--label)" },
};

function compact(n: number) {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1000) return `${Math.round(n / 1000)}k`;
  return String(Math.round(n));
}

function PL({ pl, pct, className = "" }: { pl: number | null; pct: number | null; className?: string }) {
  if (pl === null) return null;
  const up = pl >= 0;
  return (
    <span className={`tabular ${up ? "text-green" : "text-red"} ${className}`}>
      {up ? "▲" : "▼"} {money(Math.abs(pl), BASE, true)}
      {pct !== null && ` (${Math.abs(pct * 100).toFixed(1)}%)`}
    </span>
  );
}

export default function Investments() {
  const nav = useNavigate();
  const invalidate = useInvalidate();
  const tr = useSettings().data?.region === "tr";
  const { userById } = useLookups();
  const q = useQuery({ queryKey: ["investments"], queryFn: () => api.get<Portfolio>("investments") });
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [view, setViewState] = useState<"account" | "kind">(() => {
    try {
      return localStorage.getItem("investments.view") === "kind" ? "kind" : "account";
    } catch {
      return "account";
    }
  });
  const setView = (v: "account" | "kind") => {
    setViewState(v);
    try {
      localStorage.setItem("investments.view", v);
    } catch {
      /* private tab */
    }
  };
  const [refreshing, setRefreshing] = useState(false);
  const p = q.data;
  const open = p?.assets.find((a) => a.id === openId);
  const latestPrice = p?.assets.map((a) => a.price_date).filter((d): d is string => !!d && d.includes("T")).sort().at(-1);

  async function refresh() {
    setRefreshing(true);
    try {
      const r = await api.post<{ updated: number; failed: string[] }>("investments/refresh");
      invalidate();
      q.refetch();
      toast(r.failed.length ? `Updated; failed: ${r.failed.join(", ")}` : "Prices updated", r.failed.length ? "err" : "ok");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setRefreshing(false);
    }
  }

  const visible = (p?.assets ?? []).filter((a) => showArchived || !a.archived);
  const groups = KIND_OPTIONS.map((k) => ({ ...k, items: visible.filter((a) => a.kind === k.value) })).filter((g) => g.items.length);
  const maxKind = Math.max(1, ...(p?.by_kind.map((k) => k.value) ?? [1]));

  return (
    <div>
      <PageHeader
        title="Investments"
        left={
          <TextButton onClick={() => nav("/savings")}>
            <Icon name="chevronLeft" size={20} stroke={2.5} /> Savings
          </TextButton>
        }
        right={<IconButton name="plus" label="Add investment" onClick={() => setAdding(true)} />}
      />
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !p ? (
        <div className="flex justify-center py-20">
          <Spinner />
        </div>
      ) : (
        <>
          <div className="mx-4 mt-4 rounded-2xl bg-card p-4">
            <div className="text-[13px] font-medium uppercase tracking-wide text-label-2">Total value</div>
            <div className="tabular mt-1 text-[32px] font-bold leading-tight">{money(p.value)}</div>
            {p.pl !== null && (
              <div className="mt-0.5 text-[15px]">
                <PL pl={p.pl} pct={p.pl_pct} /> <span className="text-[13px] text-label-2">vs. cost</span>
              </div>
            )}
            <div className="mt-3 flex items-center justify-between gap-2 text-[13px] text-label-2">
              <span>
                {latestPrice ? `Prices: ${shortDate(latestPrice.slice(0, 10))} ${latestPrice.slice(11, 16)}` : "No prices yet"} · at buy
                price
              </span>
              <button type="button" disabled={refreshing} onClick={refresh} className="flex items-center gap-1 font-semibold text-accent disabled:opacity-40">
                {refreshing ? <Spinner /> : <Icon name="refresh" size={16} />} Refresh
              </button>
            </div>
          </div>
          {p.missing_price.length > 0 && (
            <div className="mx-4 mt-3 flex gap-2 rounded-xl bg-orange/15 p-3 text-[13px]">
              <Icon name="warning" size={18} className="text-orange" />
              <span>Assets without a price yet are counted as 0 in the total: {p.missing_price.join(", ")}.</span>
            </div>
          )}

          {!p.assets.length ? (
            <Empty
              icon="chart"
              title="No investments yet"
              text="Add gold, silver, foreign currency, stocks or funds; they are valued at current prices. Start with the + at the top right."
            />
          ) : (
            <>
              {p.by_kind.length > 1 && (
                <Section title="Allocation">
                  {p.by_kind.map((k) => (
                    <Row key={k.kind}>
                      <div className="min-w-0 flex-1">
                        <div className="flex justify-between gap-2 text-[15px]">
                          <span>{k.label}</span>
                          <span className="tabular">
                            {money(k.value, BASE, true)} <span className="text-label-2">· {p.value ? Math.round((k.value / p.value) * 100) : 0}%</span>
                          </span>
                        </div>
                        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-fill">
                          <div className="h-full rounded-full" style={{ width: `${(k.value / maxKind) * 100}%`, background: KIND_COLOR[k.kind] }} />
                        </div>
                      </div>
                    </Row>
                  ))}
                </Section>
              )}

              {p.by_owner.length > 1 && (
                <Section title="By owner">
                  {p.by_owner.map((o) => (
                    <Row key={o.user_id ?? 0}>
                      <span className="flex-1 text-[15px]">{o.user_id ? userById.get(o.user_id)?.name : "Shared"}</span>
                      <span className="tabular text-[15px]">{money(o.value, BASE, true)}</span>
                    </Row>
                  ))}
                </Section>
              )}

              <div className="mx-4 mt-6">
                <Segmented
                  value={view}
                  onChange={setView}
                  options={[
                    { value: "account", label: "By account" },
                    { value: "kind", label: "By type" },
                  ]}
                />
              </div>
              {view === "account" &&
                groupByAccount(visible, (id) => (id ? userById.get(id)?.name ?? "" : "Shared")).map((g) => (
                  <Section key={g.key} title={`${g.title} · ${g.subtitle}`}>
                    <AccountTotal items={g.items} />
                    {KIND_OPTIONS.map((k) => {
                      const items = g.items.filter((a) => a.kind === k.value);
                      if (!items.length) return null;
                      const multi = g.items.some((a) => a.kind !== k.value);
                      return (
                        <Fragment key={k.value}>
                          {multi && <KindHeader kind={k.value} items={items} />}
                          {items.map((a) => (
                            <HoldingRow key={a.id} a={a} onOpen={setOpenId} showKind={!multi} />
                          ))}
                        </Fragment>
                      );
                    })}
                  </Section>
                ))}
              {view === "kind" && groups.map((g) =>
                g.value === "gold" ? (
                  <Fragment key={g.value}>
                    <Section title={g.label}>{p.metals.gold && <MetalTotal kind="gold" m={p.metals.gold} />}</Section>
                    {groupHoldings(g.items).map((sub) => (
                      <Section key={sub.key} title={`Gold · ${sub.title}`}>
                        <SubTotal items={sub.items} />
                        {sub.items.map((a) => (
                          <HoldingRow key={a.id} a={a} onOpen={setOpenId} />
                        ))}
                      </Section>
                    ))}
                  </Fragment>
                ) : (
                  <Section key={g.value} title={g.label}>
                    {g.value === "silver" && p.metals.silver && <MetalTotal kind="silver" m={p.metals.silver} />}
                    {g.items.map((a) => (
                      <HoldingRow key={a.id} a={a} onOpen={setOpenId} />
                    ))}
                  </Section>
                ),
              )}

              {p.history.length > 1 && (
                <Section title="Value history" footer="Total value and cost recorded daily.">
                  <div className="h-48 px-2 pt-3">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={p.history} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                        <CartesianGrid vertical={false} stroke="var(--sep)" />
                        <XAxis dataKey="date" tickFormatter={(d: string) => shortDate(d)} tickLine={false} axisLine={false} tick={{ fill: "var(--label-2)", fontSize: 11 }} minTickGap={40} />
                        <YAxis tickLine={false} axisLine={false} tick={{ fill: "var(--label-2)", fontSize: 11 }} tickFormatter={compact} width={44} />
                        <Tooltip
                          {...tooltipStyle}
                          labelFormatter={(d: string) => shortDate(d)}
                          formatter={(v: number, n: string) => [money(v, BASE, true), n === "value" ? "Value" : "Cost"]}
                        />
                        <Line dataKey="cost" stroke="var(--label-3)" strokeDasharray="4 3" strokeWidth={2} dot={false} />
                        <Line dataKey="value" stroke="var(--accent)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </Section>
              )}

              {p.assets.some((a) => a.archived) && (
                <Section>
                  <Field label="Show disposed">
                    <Toggle checked={showArchived} onChange={setShowArchived} />
                  </Field>
                </Section>
              )}
              <p className="mx-8 mt-3 text-[12px] text-label-3">
                {tr
                  ? "Gold, silver and currencies are valued at Grand Bazaar/central bank buy prices; stocks at the Borsa Istanbul last price (~15 min delayed)."
                  : `Metals and currencies are valued at market prices in ${BASE}; stocks at the last exchange price (may be delayed).`}{" "}
                Funds use the amount you enter manually. Prices update every 30 minutes.
              </p>
            </>
          )}
        </>
      )}
      {adding && p && <AddAssetSheet catalog={p.catalog} onClose={() => setAdding(false)} onCreated={(id) => setOpenId(id)} />}
      {open && <AssetSheet asset={open} onClose={() => setOpenId(null)} />}
    </div>
  );
}

function HoldingRow({ a, onOpen, showKind = false }: { a: Holding; onOpen: (id: number) => void; showKind?: boolean }) {
  const { userById } = useLookups();
  return (
    <Row chevron onClick={() => onOpen(a.id)} className={a.archived ? "opacity-50" : ""}>
      <span className="h-8 w-1.5 shrink-0 rounded-full" style={{ background: KIND_COLOR[a.kind] }} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[16px]">{a.name}</div>
        <div className="truncate text-[12px] text-label-2">
          {showKind && `${a.kind_label} · `}
          {a.kind === "fund" ? (a.price_date ? `as of ${shortDate(a.price_date)}` : "") : qty(a.quantity, a.unit)}
          {a.kind === "gold" && a.unit === "piece" && a.gross_gram ? ` (${grams(a.gross_gram)}, ≈${grams(a.pure_gram ?? 0)} pure)` : ""}
          {a.price !== null && a.kind !== "fund" ? ` · ${money(a.price)}/${a.unit}` : ""}
          {` · ${a.owner_id ? userById.get(a.owner_id)?.name : "Shared"}`}
          {a.location && a.kind !== "gold" && !showKind ? ` · ${a.location}` : ""}
          {a.stale ? " · stale price" : ""}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <div className="tabular text-[16px]">{money(a.value, BASE, true)}</div>
        <PL pl={a.pl} pct={a.pl_pct} className="text-[12px]" />
      </div>
    </Row>
  );
}

/** Type subheader within an account: Gold 3 · $120,000 */
function KindHeader({ kind, items }: { kind: AssetKind; items: Holding[] }) {
  const active = items.filter((a) => !a.archived);
  const value = active.reduce((t, a) => t + a.value, 0);
  const label = items[0].kind_label;
  return (
    <div className="flex items-center gap-2 border-b border-sep bg-fill/40 px-4 pb-1.5 pt-3 text-[13px] font-semibold">
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: KIND_COLOR[kind] }} />
      <span className="flex-1">
        {label} <span className="font-normal text-label-2">· {active.length}</span>
      </span>
      <span className="tabular text-label-2">{money(value, BASE, true)}</span>
    </div>
  );
}

/** Account group total: value, profit/loss, and gold grams if any. */
function AccountTotal({ items }: { items: Holding[] }) {
  const active = items.filter((a) => !a.archived);
  const value = active.reduce((t, a) => t + a.value, 0);
  const withPl = active.filter((a) => a.pl !== null);
  const cost = withPl.reduce((t, a) => t + (a.cost ?? 0), 0);
  const pl = withPl.reduce((t, a) => t + (a.pl ?? 0), 0);
  const gold = active.filter((a) => a.kind === "gold");
  const gross = gold.reduce((t, a) => t + (a.gross_gram ?? 0), 0);
  const pure = gold.reduce((t, a) => t + (a.pure_gram ?? 0), 0);
  return (
    <div className="border-b border-sep px-4 py-3">
      <div className="flex items-end justify-between gap-2">
        <div className="tabular text-[20px] font-semibold">{money(value, BASE, true)}</div>
        {cost > 0 && <PL pl={pl} pct={pl / cost} className="text-[13px]" />}
      </div>
      <div className="mt-0.5 text-[12px] text-label-2">
        {active.length} assets
        {gold.length > 0 && ` · gold ${grams(gross)} (≈${grams(pure)} pure)`}
      </div>
    </div>
  );
}

/** Gram and value totals of a gold subgroup (excluding archived). */
function SubTotal({ items }: { items: Holding[] }) {
  const active = items.filter((a) => !a.archived);
  const gross = active.reduce((t, a) => t + (a.gross_gram ?? 0), 0);
  const pure = active.reduce((t, a) => t + (a.pure_gram ?? 0), 0);
  const value = active.reduce((t, a) => t + a.value, 0);
  return (
    <div className="flex justify-between gap-2 border-b border-sep px-4 py-2.5 text-[13px] text-label-2">
      <span className="tabular">
        {grams(gross)} · ≈{grams(pure)} pure
      </span>
      <span className="tabular font-semibold text-label">{money(value, BASE, true)}</span>
    </div>
  );
}

/** Gold/silver total: gross grams, pure (24k) equivalent and comparison with the gram price. */
function MetalTotal({ kind, m }: { kind: "gold" | "silver"; m: MetalSummary }) {
  const premium = m.base_equivalent !== null ? m.value - m.base_equivalent : null;
  return (
    <div className="border-b border-sep px-4 py-3">
      <div className="flex items-end justify-between gap-2">
        <div>
          <div className="text-[12px] text-label-2">Total {kind === "gold" ? "gold" : "silver"}</div>
          <div className="tabular text-[20px] font-semibold">{grams(m.gross_gram)}</div>
        </div>
        {kind === "gold" && (
          <div className="text-right">
            <div className="text-[12px] text-label-2">Pure (24k) equivalent</div>
            <div className="tabular text-[20px] font-semibold">{grams(m.pure_gram)}</div>
          </div>
        )}
      </div>
      {kind === "gold" && m.base_equivalent !== null && (
        <div className="mt-1 text-[12px] text-label-2">
          At {m.base_name} price {money(m.base_equivalent, BASE, true)}
          {premium !== null && Math.abs(premium) >= 1 && ` · coin/workmanship premium ${premium > 0 ? "+" : "−"}${money(Math.abs(premium), BASE, true)}`}
        </div>
      )}
    </div>
  );
}

/** Where it is held: physical (on hand, with a group name) or bank/brokerage. */
function PlaceFields({ place, setPlace, label, setLabel }: { place: "physical" | "bank"; setPlace: (v: "physical" | "bank") => void; label: string; setLabel: (v: string) => void }) {
  return (
    <>
      <div className="px-4 py-2">
        <Segmented
          value={place}
          onChange={setPlace}
          options={[
            { value: "physical", label: "Physical" },
            { value: "bank", label: "Bank" },
          ]}
        />
      </div>
      <Field label={place === "bank" ? "Bank" : "Group"}>
        <input
          className={inputCls}
          placeholder={place === "bank" ? "e.g. Example Bank" : "Savings"}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
      </Field>
    </>
  );
}

/** Physical: group name goes into the note. Bank: bank name goes into location; the old note is kept (unless it is the bank name). */
function placeBody(place: "physical" | "bank", label: string, oldNote = "") {
  const v = label.trim();
  return place === "bank" ? { location: v, note: oldNote === v ? "" : oldNote } : { location: "", note: v };
}

function useGoals() {
  return (useQuery({ queryKey: ["savings"], queryFn: () => api.get<Savings>("savings") }).data?.goals ?? []).filter((g) => !g.archived);
}

function OwnerGoalFields({
  owner,
  setOwner,
  goal,
  setGoal,
}: {
  owner: number | 0;
  setOwner: (v: number | 0) => void;
  goal: number | 0;
  setGoal: (v: number | 0) => void;
}) {
  const { me } = useLookups();
  const goals = useGoals();
  return (
    <>
      <Field label="Owner">
        <Select value={owner} onChange={setOwner} options={[{ value: 0, label: "Shared" }, ...(me?.users ?? []).map((u) => ({ value: u.id, label: u.name }))]} />
      </Field>
      <Field label="Savings goal">
        <Select value={goal} onChange={setGoal} options={[{ value: 0, label: "Not linked" }, ...goals.map((g) => ({ value: g.id, label: g.name }))]} />
      </Field>
    </>
  );
}

/** First catalog entry of a kind (for currencies: the first one that is not the base currency). */
function defaultCode(catalog: Portfolio["catalog"], k: AssetKind): string {
  if (k !== "gold" && k !== "silver" && k !== "metal" && k !== "fx") return "";
  const items = catalog[k] ?? [];
  return (k === "fx" ? items.find((i) => i.code !== BASE) : undefined)?.code ?? items[0]?.code ?? "";
}

function AddAssetSheet({ catalog, onClose, onCreated }: { catalog: Portfolio["catalog"]; onClose: () => void; onCreated: (id: number) => void }) {
  const invalidate = useInvalidate();
  const tr = useSettings().data?.region === "tr";
  const [kind, setKind] = useState<AssetKind>("gold");
  const [code, setCode] = useState(defaultCode(catalog, "gold"));
  const [name, setName] = useState("");
  const [owner, setOwner] = useState<number | 0>(0);
  const [goal, setGoal] = useState<number | 0>(0);
  const [value, setValue] = useState("");
  const [cost, setCost] = useState("");
  const [place, setPlace] = useState<"physical" | "bank">("physical");
  const [placeLabel, setPlaceLabel] = useState("");
  const [quantity, setQuantity] = useState("");
  const [unitPrice, setUnitPrice] = useState("");
  const [date, setDate] = useState(today());
  const [busy, setBusy] = useState(false);
  const lotRef = useRef(uid());
  // Asset was created but the buy-lot response was lost: pressing Save again must not create a new asset
  const createdRef = useRef<Holding | null>(null);

  function chooseKind(k: AssetKind) {
    setKind(k);
    setCode(defaultCode(catalog, k));
  }
  const items = kind === "gold" || kind === "silver" || kind === "metal" || kind === "fx" ? catalog[kind] ?? [] : [];
  const unit = kind === "stock" ? "piece" : items.find((i) => i.code === code)?.unit ?? "";

  async function save() {
    if (busy) return;
    const body = {
      kind,
      code: kind === "fund" ? "" : code,
      name,
      owner_id: owner || null,
      goal_id: goal || null,
      ...placeBody(place, placeLabel),
      value: kind === "fund" ? parseAmount(value) : null,
      cost: kind === "fund" && cost.trim() ? parseAmount(cost) : null,
    };
    if (kind === "fund" && !(body.value! >= 0)) return toast("Enter the fund amount", "err");
    if (kind === "stock" && !code.trim()) return toast(`Enter the stock ticker (e.g. ${tr ? "THYAO" : "AAPL"})`, "err");
    if (place === "bank" && !placeLabel.trim()) return toast("Enter the bank name", "err");
    const q = parseAmount(quantity);
    const price = parseAmount(unitPrice);
    if (kind !== "fund" && quantity.trim() && (!(q > 0) || !(price >= 0) || !unitPrice.trim())) return toast("Enter quantity and buy price", "err");
    setBusy(true);
    try {
      const a = createdRef.current ?? (await api.post<Holding>("investments/assets", body));
      createdRef.current = a;
      if (kind !== "fund" && q > 0) {
        await api.post(`investments/assets/${a.id}/lots`, { side: "buy", date, quantity: q, unit_price: price, client_ref: lotRef.current });
      }
      invalidate();
      toast("Added");
      onClose();
      onCreated(a.id);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open onClose={onClose} title="Add Investment" right={<TextButton onClick={save} disabled={busy}>Save</TextButton>}>
      <div className="px-4 pt-1">
        <Segmented value={kind} onChange={chooseKind} options={KIND_OPTIONS} />
      </div>
      <Section>
        {kind === "stock" && (
          <Field label="Ticker">
            <input className={inputCls} placeholder={tr ? "e.g. THYAO" : "e.g. AAPL"} autoCapitalize="characters" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
          </Field>
        )}
        {(kind === "gold" || kind === "silver" || kind === "metal" || kind === "fx") && (
          <Field label="Type">
            <Select value={code} onChange={setCode} options={items.map((i) => ({ value: i.code, label: kind === "fx" ? `${i.code} · ${i.name}` : i.name }))} />
          </Field>
        )}
        {kind === "fund" ? (
          <>
            <Field label="Fund">
              <input className={inputCls} placeholder={tr ? "e.g. TTE · Tech Fund" : "e.g. Index fund"} value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label={`Current amount (${BASE})`}>
              <input className={inputCls} inputMode="decimal" placeholder="0" value={value} onChange={(e) => setValue(e.target.value)} />
            </Field>
            <Field label="Total invested">
              <input className={inputCls} inputMode="decimal" placeholder="Optional" value={cost} onChange={(e) => setCost(e.target.value)} />
            </Field>
          </>
        ) : (
          <Field label="Display name">
            <input className={inputCls} placeholder="Optional" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        )}
        <OwnerGoalFields owner={owner} setOwner={setOwner} goal={goal} setGoal={setGoal} />
      </Section>
      <Section title="Location" footer="Gold is listed separately by physical/bank and group.">
        <PlaceFields place={place} setPlace={setPlace} label={placeLabel} setLabel={setPlaceLabel} />
      </Section>
      {kind !== "fund" && (
        <Section title="Quantity held" footer="Enter the buy price to track profit/loss. You can add purchases from different dates separately later.">
          <Field label={`Quantity (${unit})`}>
            <input className={inputCls} inputMode="decimal" placeholder="0" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          </Field>
          <Field label={`Buy price (${BASE}/${unit})`}>
            <input className={inputCls} inputMode="decimal" placeholder="0" value={unitPrice} onChange={(e) => setUnitPrice(e.target.value)} />
          </Field>
          <Field label="Buy date">
            <input type="date" className={dateCls} value={date} max={today()} onChange={(e) => setDate(e.target.value)} />
          </Field>
        </Section>
      )}
      <div className="mx-4 mt-6">
        <PrimaryButton onClick={save} disabled={busy}>
          {busy ? <Spinner /> : "Save"}
        </PrimaryButton>
      </div>
    </Sheet>
  );
}

function AssetSheet({ asset: a, onClose }: { asset: Holding; onClose: () => void }) {
  const invalidate = useInvalidate();
  const { userById } = useLookups();
  const [owner, setOwner] = useState<number | 0>(a.owner_id ?? 0);
  const [goal, setGoal] = useState<number | 0>(a.goal_id ?? 0);
  const [name, setName] = useState(a.name);
  const [value, setValue] = useState(amountInput(a.kind === "fund" ? a.value : null));
  const [cost, setCost] = useState(amountInput(a.kind === "fund" ? a.cost : null));
  const [place, setPlace] = useState<"physical" | "bank">(a.location ? "bank" : "physical");
  const [placeLabel, setPlaceLabel] = useState(a.location || a.note);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [quantity, setQuantity] = useState("");
  const [unitPrice, setUnitPrice] = useState(a.price !== null ? amountInput(a.price) : "");
  const [date, setDate] = useState(today());
  const [busy, setBusy] = useState(false);
  const lotRef = useRef(uid());

  async function call(fn: () => Promise<unknown>, done: string) {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      invalidate();
      toast(done);
      return true;
    } catch (e) {
      toast((e as Error).message, "err");
      return false;
    } finally {
      setBusy(false);
    }
  }
  const saveInfo = () =>
    call(
      () =>
        api.put(`investments/assets/${a.id}`, {
          kind: a.kind,
          code: a.code,
          name,
          owner_id: owner || null,
          goal_id: goal || null,
          ...placeBody(place, placeLabel, a.note),
          archived: a.archived,
          value: a.kind === "fund" ? parseAmount(value) : null,
          cost: a.kind === "fund" && cost.trim() ? parseAmount(cost) : null,
        }),
      "Saved",
    ).then((ok) => ok && onClose());
  async function addLot() {
    const q = parseAmount(quantity);
    const price = parseAmount(unitPrice);
    if (!(q > 0) || !(price >= 0) || !unitPrice.trim()) return toast("Enter quantity and price", "err");
    const ok = await call(
      () => api.post(`investments/assets/${a.id}/lots`, { side, date, quantity: q, unit_price: price, client_ref: lotRef.current }),
      side === "buy" ? "Buy added" : "Sale added",
    );
    if (ok) {
      setQuantity("");
      lotRef.current = uid();
    }
  }
  const removeLot = (id: number) => confirm("Delete this record?") && call(() => api.del(`investments/lots/${id}`), "Deleted");
  const remove = () =>
    confirm(a.lots.length ? "Move this asset to disposed? Its history will be kept." : "Delete this asset?") &&
    call(() => api.del(`investments/assets/${a.id}`), "Removed").then((ok) => ok && onClose());

  return (
    <Sheet open onClose={onClose} title={a.name} right={<TextButton onClick={saveInfo} disabled={busy}>Save</TextButton>}>
      <div className="mx-4 mt-1 rounded-2xl bg-card p-4">
        <div className="text-[13px] text-label-2">
          {a.kind_label}
          {a.kind !== "fund" && a.quantity !== null && ` · ${qty(a.quantity, a.unit)}`}
        </div>
        <div className="tabular text-[28px] font-bold">{money(a.value)}</div>
        <PL pl={a.pl} pct={a.pl_pct} className="text-[15px]" />
        <div className="mt-2 text-[12px] text-label-2">
          {a.kind === "fund"
            ? a.price_date && `Amount entered on ${shortDate(a.price_date)}`
            : a.price !== null
              ? `${money(a.price)}/${a.unit} · ${a.price_source} · ${a.price_date ? `${shortDate(a.price_date.slice(0, 10))} ${a.price_date.slice(11, 16)}` : ""}`
              : "Price not available yet"}
          {a.cost !== null && a.kind !== "fund" && ` · cost ${money(a.cost)}`}
          {a.realized ? ` · realized ${money(a.realized)}` : ""}
        </div>
      </div>

      {a.kind === "fund" ? (
        <Section title="Fund amount" footer="Periodically enter the current amount from your bank; the date is recorded automatically.">
          <Field label={`Current amount (${BASE})`}>
            <input className={inputCls} inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
          </Field>
          <Field label="Total invested">
            <input className={inputCls} inputMode="decimal" placeholder="Optional" value={cost} onChange={(e) => setCost(e.target.value)} />
          </Field>
        </Section>
      ) : (
        <>
          <Section title="Add buy / sell">
            <div className="px-4 py-2">
              <Segmented
                value={side}
                onChange={setSide}
                options={[
                  { value: "buy", label: "Buy" },
                  { value: "sell", label: "Sell" },
                ]}
              />
            </div>
            <Field label={`Quantity (${a.unit})`}>
              <input className={inputCls} inputMode="decimal" placeholder="0" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
            </Field>
            <Field label={`Price (${BASE}/${a.unit})`}>
              <input className={inputCls} inputMode="decimal" value={unitPrice} onChange={(e) => setUnitPrice(e.target.value)} />
            </Field>
            <Field label="Date">
              <input type="date" className={dateCls} value={date} max={today()} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <div className="p-3">
              <PrimaryButton tone="plain" onClick={addLot} disabled={busy}>
                {side === "buy" ? "Add buy" : "Add sale"}
              </PrimaryButton>
            </div>
          </Section>
          {a.lots.length > 0 && (
            <Section title="History">
              {a.lots.map((l) => (
                <Row key={l.id}>
                  <div className="min-w-0 flex-1">
                    <div className="text-[15px]">
                      {l.side === "buy" ? "Buy" : "Sell"} · {qty(l.quantity, a.unit)}
                    </div>
                    <div className="truncate text-[12px] text-label-2">
                      {shortDate(l.date)} · {money(l.unit_price)}/{a.unit}
                      {l.user_id ? ` · ${userById.get(l.user_id)?.name}` : ""}
                    </div>
                  </div>
                  <span className="tabular text-[15px]">{money(l.total, BASE, true)}</span>
                  <IconButton name="trash" label="Delete" className="!text-label-3" onClick={() => removeLot(l.id)} />
                </Row>
              ))}
            </Section>
          )}
        </>
      )}

      <Section title="Details">
        <Field label="Name">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <OwnerGoalFields owner={owner} setOwner={setOwner} goal={goal} setGoal={setGoal} />
      </Section>
      <Section title="Location">
        <PlaceFields place={place} setPlace={setPlace} label={placeLabel} setLabel={setPlaceLabel} />
      </Section>
      <div className="mx-4 mt-6 space-y-3">
        <PrimaryButton onClick={saveInfo} disabled={busy}>
          Save
        </PrimaryButton>
        {!a.archived && (
          <PrimaryButton tone="red" onClick={remove} disabled={busy}>
            {a.lots.length ? "Move to disposed" : "Delete"}
          </PrimaryButton>
        )}
      </div>
    </Sheet>
  );
}
