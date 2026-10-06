import { createContext, lazy, Suspense, useContext, useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import type { Tx } from "./lib/api";
import { useDocuments, useMe } from "./lib/queries";
import { applySettings, useSettings } from "./lib/settings";
import { Icon, Spinner, ToastHost } from "./components/ui";
import AddSheet from "./components/AddSheet";
import Dashboard from "./pages/Dashboard";
import Transactions from "./pages/Transactions";
import Cards from "./pages/Cards";
import CardDetail from "./pages/CardDetail";
import More from "./pages/More";
import RecurringPage from "./pages/Recurring";
import Budgets from "./pages/Budgets";
import Documents from "./pages/Documents";
import DocumentReview from "./pages/DocumentReview";
import Categories from "./pages/Categories";
import History from "./pages/History";
import Setup from "./pages/Setup";
import SettingsPage from "./pages/Settings";

// The chart library is large; only load it when Reports is opened
const Savings = lazy(() => import("./pages/Savings"));
const Investments = lazy(() => import("./pages/Investments"));
const Reports = lazy(() => import("./pages/Reports"));
const Plan = lazy(() => import("./pages/Plan"));
const Categorize = lazy(() => import("./pages/Categorize"));
const Insights = lazy(() => import("./pages/Insights"));

type AddMode = "manual" | "upload";
interface AddCtx {
  openAdd: (mode?: AddMode) => void;
  editTx: (tx: Tx) => void;
}
const Ctx = createContext<AddCtx>({ openAdd: () => {}, editTx: () => {} });
export const useAdd = () => useContext(Ctx);

function TabBar({ onAdd }: { onAdd: () => void }) {
  const docs = useDocuments().data ?? [];
  const pending = docs.filter((d) => d.status === "review").length;
  const tab = (to: string, icon: string, label: string, badge?: number) => (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `relative flex h-full flex-1 flex-col items-center justify-start gap-0.5 pt-1.5 text-[10px] font-medium ${
          isActive ? "text-accent" : "text-label-2"
        }`
      }
    >
      <Icon name={icon} size={25} stroke={1.8} />
      <span>{label}</span>
      {!!badge && (
        <span className="absolute left-1/2 top-0.5 ml-2 min-w-[18px] rounded-full bg-red px-1 text-center text-[11px] leading-[18px] text-white">
          {badge}
        </span>
      )}
    </NavLink>
  );
  return (
    <nav
      className="relative z-40 shrink-0 border-t border-sep backdrop-blur-xl"
      style={{ background: "var(--bar)", paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      <div className="mx-auto flex h-[56px] max-w-lg items-stretch px-2">
        {tab("/", "home", "Summary")}
        {tab("/transactions", "list", "Transactions")}
        <div className="flex flex-1 items-start justify-center pt-1">
          <button
            type="button"
            onClick={onAdd}
            aria-label="Add"
            className="flex h-11 w-11 items-center justify-center rounded-full bg-accent text-white shadow-md active:scale-95"
          >
            <Icon name="plus" size={26} stroke={2.5} />
          </button>
        </div>
        {tab("/cards", "card", "Cards")}
        {tab("/more", "more", "More", pending)}
      </div>
    </nav>
  );
}

export default function App() {
  const [add, setAdd] = useState<{ open: boolean; mode: AddMode; tx?: Tx }>({ open: false, mode: "manual" });
  const loc = useLocation();
  const scroller = useRef<HTMLDivElement>(null);
  // Scroll content back to top on page change
  useEffect(() => {
    scroller.current?.scrollTo(0, 0);
  }, [loc.pathname]);
  const meQ = useMe();
  const settingsQ = useSettings();
  const settings = settingsQ.data;
  // Locale + base currency must be set before any page formats an amount
  if (settings) applySettings(settings);
  const onSetup = loc.pathname === "/setup";
  const hideTabs = loc.pathname.startsWith("/documents/") || onSetup;

  if (settingsQ.isPending) {
    return (
      <div className="app-shell items-center justify-center">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }
  // First run: the setup assistant comes before everything else
  if (settings && !settings.setup_done && !onSetup) return <Navigate to="/setup" replace />;

  return (
    <Ctx.Provider
      value={{
        openAdd: (mode = "manual") => setAdd({ open: true, mode }),
        editTx: (tx) => setAdd({ open: true, mode: "manual", tx }),
      }}
    >
      {/* Shell fixed to the viewport: keep the HA panel iframe on iOS from growing with content; only the content scrolls */}
      <div className="app-shell">
        <div ref={scroller} id="scroller" className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {settingsQ.isError && !meQ.isError && (
            <div className="pt-safe mx-4 mt-3 flex gap-2 rounded-xl bg-orange/15 p-3 text-[15px]">
              <Icon name="warning" className="text-orange" />
              <div className="min-w-0 flex-1">
                Could not load settings: {(settingsQ.error as Error).message}
                <button className="ml-2 font-semibold text-accent underline" onClick={() => settingsQ.refetch()}>
                  Try again
                </button>
              </div>
            </div>
          )}
          {meQ.isError && (
            <div className="pt-safe mx-4 mt-3 flex gap-2 rounded-xl bg-red/10 p-3 text-[15px] text-red">
              <Icon name="warning" />
              <div className="min-w-0 flex-1">
                Could not connect to the server: {(meQ.error as Error).message}
                <button className="ml-2 font-semibold underline" onClick={() => meQ.refetch()}>
                  Try again
                </button>
              </div>
            </div>
          )}
          <main className={`mx-auto max-w-lg ${onSetup ? "min-h-full" : hideTabs ? "pb-safe" : "pb-6"}`}>
            <Routes>
              <Route path="/setup" element={<Setup />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/" element={<Dashboard />} />
              <Route path="/transactions" element={<Transactions />} />
              <Route path="/cards" element={<Cards />} />
              <Route path="/cards/:id" element={<CardDetail />} />
              <Route path="/more" element={<More />} />
              <Route path="/recurring" element={<RecurringPage kind="expense" />} />
              <Route path="/recurring/expenses" element={<RecurringPage kind="expense" />} />
              <Route path="/recurring/income" element={<RecurringPage kind="income" />} />
              <Route path="/budgets" element={<Budgets />} />
              <Route path="/insights" element={<Suspense fallback={<div className="p-8">Loading…</div>}><Insights /></Suspense>} />
              <Route path="/categorize" element={<Suspense fallback={<div className="p-8">Loading…</div>}><Categorize /></Suspense>} />
              <Route path="/plan" element={<Suspense fallback={<div className="p-8">Loading…</div>}><Plan /></Suspense>} />
              <Route
                path="/reports"
                element={
                  <Suspense fallback={null}>
                    <Reports />
                  </Suspense>
                }
              />
              <Route path="/documents" element={<Documents />} />
              <Route path="/documents/:id" element={<DocumentReview key={loc.pathname} />} />
              <Route path="/categories" element={<Categories />} />
              <Route path="/history" element={<History />} />
              <Route path="/savings" element={<Suspense fallback={<div className="p-8">Loading…</div>}><Savings /></Suspense>} />
              <Route path="/investments" element={<Suspense fallback={<div className="p-8">Loading…</div>}><Investments /></Suspense>} />
            </Routes>
          </main>
        </div>
        {!hideTabs && <TabBar onAdd={() => setAdd({ open: true, mode: "manual" })} />}
      </div>
      <AddSheet
        open={add.open}
        initialMode={add.mode}
        tx={add.tx}
        onClose={() => setAdd((a) => ({ ...a, open: false, tx: undefined }))}
      />
      <ToastHost />
    </Ctx.Provider>
  );
}
