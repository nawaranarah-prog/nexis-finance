import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { WorkspaceProvider } from "./hooks/workspace";
import { LanguageProvider } from "./i18n";
import { ApiError } from "./services/api";
import "./styles.css";
import "./styles-markets.css";
import "./styles-social.css";
import "./styles-home.css";

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
  </StrictMode>,
);
