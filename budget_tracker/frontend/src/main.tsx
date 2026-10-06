import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

// The HA sidebar panel opens the app inside an iframe
if (window.self !== window.top) document.documentElement.classList.add("embedded");

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      // Retry a few more times while the add-on restarts (502-504)
      retry: (count, err) => ((err as { status?: number }).status ?? 0) >= 502 ? count < 6 : count < 1,
      retryDelay: (n) => Math.min(1000 * 2 ** n, 8000),
      refetchOnWindowFocus: true,
      staleTime: 10_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <HashRouter>
        <App />
      </HashRouter>
    </QueryClientProvider>
  </StrictMode>,
);
