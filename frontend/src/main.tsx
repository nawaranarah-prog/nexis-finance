import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Analytics, type BeforeSendEvent } from "@vercel/analytics/react";
import App from "./App";
import { WorkspaceProvider } from "./hooks/workspace";
import { LanguageProvider } from "./i18n";
import { ApiError } from "./services/api";
import "./styles.css";
import "./styles-markets.css";
import "./styles-social.css";
import "./styles-home.css";
import "./styles-pulse.css";
import "./styles-intel.css";
import "./styles-np.css";
import "./styles-graph.css";
import "./styles-tm.css";
import "./styles-da.css";

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // Analytics are deterministic for a dataset version; avoid needless recomputation.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
});

/* Vercel Web Analytics (cookieless page views, every route via the History API). URLs that carry secrets are cleaned
   before anything is sent: password-reset / email-verification tokens and the Stripe checkout session id. */
const SECRET_PARAMS = ["token", "session_id", "code", "state"];
function scrub(event: BeforeSendEvent): BeforeSendEvent {
  const url = new URL(event.url);
  SECRET_PARAMS.forEach((p) => url.searchParams.delete(p));
  return { ...event, url: url.toString() };
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <WorkspaceProvider>
          <LanguageProvider>
            <App />
          </LanguageProvider>
        </WorkspaceProvider>
      </BrowserRouter>
    </QueryClientProvider>
    <Analytics beforeSend={scrub} />
  </StrictMode>,
);
