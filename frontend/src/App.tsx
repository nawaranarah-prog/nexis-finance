import { lazy, Suspense, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "./services/api";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Toaster } from "./components/toast";
import AppLayout from "./layouts/AppLayout";
import { Empty, ErrorState, Loading } from "./components/ui";
import { useWorkspace } from "./hooks/workspace";

const Overview = lazy(() => import("./pages/Overview"));
const Pulse = lazy(() => import("./pages/Pulse"));
const MarketData = lazy(() => import("./pages/MarketData"));
const DataQuality = lazy(() => import("./pages/DataQuality"));
const AssetResearch = lazy(() => import("./pages/AssetResearch"));
const PortfolioLab = lazy(() => import("./pages/PortfolioLab"));
const RiskAnalytics = lazy(() => import("./pages/RiskAnalytics"));
const StressTesting = lazy(() => import("./pages/StressTesting"));
const FactorAnalytics = lazy(() => import("./pages/FactorAnalytics"));
const QuantStrategies = lazy(() => import("./pages/QuantStrategies"));
const Backtesting = lazy(() => import("./pages/Backtesting"));
const MachineLearning = lazy(() => import("./pages/MachineLearning"));
const RegimeAnalysis = lazy(() => import("./pages/RegimeAnalysis"));
const AnomalyDetection = lazy(() => import("./pages/AnomalyDetection"));
const Experiments = lazy(() => import("./pages/Experiments"));
const Reports = lazy(() => import("./pages/Reports"));
const SystemHealth = lazy(() => import("./pages/SystemHealth"));
const Settings = lazy(() => import("./pages/Settings"));
const Connections = lazy(() => import("./pages/Connections"));
const Intelligence = lazy(() => import("./pages/Intelligence"));
const XRay = lazy(() => import("./pages/XRay"));
const Transactions = lazy(() => import("./pages/Transactions"));
const Graph = lazy(() => import("./pages/Graph"));
const Reconciliation = lazy(() => import("./pages/Reconciliation"));
const Lineage = lazy(() => import("./pages/Lineage"));
const Economic = lazy(() => import("./pages/Economic"));
const AuditLog = lazy(() => import("./pages/AuditLog"));
const Developer = lazy(() => import("./pages/Developer"));
const Assistant = lazy(() => import("./pages/Assistant"));
const Login = lazy(() => import("./pages/Login"));
const Home = lazy(() => import("./pages/Home"));
const InstaTopic = lazy(() => import("./pages/Finstagram").then((m) => ({ default: m.TopicPage })));
const Markets = lazy(() => import("./pages/Markets"));
const Instrument = lazy(() => import("./pages/Instrument"));
const Advisor = lazy(() => import("./pages/Advisor"));
const Compare = lazy(() => import("./pages/Compare"));
const Valuation = lazy(() => import("./pages/Valuation"));
const Finstagram = lazy(() => import("./pages/Finstagram"));
const InstaPost = lazy(() => import("./pages/Finstagram").then((m) => ({ default: m.PostPage })));
const InstaProfile = lazy(() => import("./pages/Finstagram").then((m) => ({ default: m.ProfilePage })));

function Waking() {
  const qc = useQueryClient();
  useEffect(() => {
    const t = window.setInterval(() => qc.invalidateQueries({ queryKey: ["datasets"] }), 6000);
    return () => window.clearInterval(t);
  }, [qc]);
  return (
    <div className="state">
      <span className="spinner" />
      <div style={{ marginTop: 10, fontWeight: 600, color: "var(--text)" }}>Reaching the research API…</div>
      <div style={{ marginTop: 4 }}>The API is starting up or temporarily unavailable. Retrying automatically.</div>
    </div>
  );
}

function Guard({ children }: { children: React.ReactNode }) {
  const { loading, error, dataset } = useWorkspace();
  if (loading) return <Loading label="Connecting to the research API" />;
  if (error instanceof ApiError && (error.status === 0 || error.status >= 500)) return <Waking />;
  if (error) return <ErrorState error={error} />;
  if (!dataset)
    return (
      <Empty>
        No dataset loaded yet. Ingest real market data on the <a href="/market-data">Market Data</a> page, connect a source on{" "}
        <a href="/connections">Connections</a>, or run <code>scripts/seed_live.py</code>.
      </Empty>
    );
  return <>{children}</>;
}

function LegacySocial() {
  const loc = useLocation();
  return <Navigate to={loc.pathname.replace(/^\/social/, "/finstagram") + loc.search} replace />;
}

export default function App() {
  const location = useLocation();
  if (location.pathname === "/login") {
    return <Suspense fallback={<Loading />}><Login /><Toaster /></Suspense>;
  }
  return (
    <AppLayout>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/research" element={<Guard><Overview /></Guard>} />
          <Route path="/market-data" element={<MarketData />} />
          <Route path="/data-quality" element={<Guard><DataQuality /></Guard>} />
          <Route path="/asset-research" element={<Guard><AssetResearch /></Guard>} />
          <Route path="/portfolio-lab" element={<Guard><PortfolioLab /></Guard>} />
          <Route path="/risk" element={<Guard><RiskAnalytics /></Guard>} />
          <Route path="/stress-testing" element={<Guard><StressTesting /></Guard>} />
          <Route path="/factors" element={<Guard><FactorAnalytics /></Guard>} />
          <Route path="/strategies" element={<Guard><QuantStrategies /></Guard>} />
          <Route path="/backtesting" element={<Guard><Backtesting /></Guard>} />
          <Route path="/machine-learning" element={<Guard><MachineLearning /></Guard>} />
          <Route path="/regimes" element={<Guard><RegimeAnalysis /></Guard>} />
          <Route path="/anomalies" element={<Guard><AnomalyDetection /></Guard>} />
          <Route path="/experiments" element={<Experiments />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/system" element={<SystemHealth />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/connections" element={<Connections />} />
          <Route path="/intelligence" element={<Intelligence />} />
          <Route path="/xray" element={<XRay />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/graph" element={<Graph />} />
          <Route path="/reconciliation" element={<Reconciliation />} />
          <Route path="/lineage" element={<Lineage />} />
          <Route path="/economic" element={<Economic />} />
          <Route path="/audit" element={<AuditLog />} />
          <Route path="/developer" element={<Developer />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="/markets" element={<Markets />} />
          <Route path="/markets/:symbol" element={<Instrument />} />
          <Route path="/advisor" element={<Advisor />} />
          <Route path="/pulse" element={<Pulse />} />
          <Route path="/pulse/:symbol" element={<Pulse />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/valuation" element={<Valuation />} />
          <Route path="/valuation/:symbol" element={<Valuation />} />
          <Route path="/finstagram" element={<Finstagram />} />
          <Route path="/social/*" element={<LegacySocial />} />
          <Route path="/finstagram/p/:id" element={<InstaPost />} />
          <Route path="/finstagram/u/:username" element={<InstaProfile />} />
          <Route path="/finstagram/s/:symbol" element={<InstaTopic kind="symbol" />} />
          <Route path="/finstagram/t/:tag" element={<InstaTopic kind="tag" />} />
          <Route path="*" element={<Empty>Page not found.</Empty>} />
        </Routes>
      </Suspense>
    </AppLayout>
  );
}
