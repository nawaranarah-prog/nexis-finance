import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import { Empty, ErrorState, Loading } from "./components/ui";
import { useWorkspace } from "./hooks/workspace";

const Overview = lazy(() => import("./pages/Overview"));
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

function Guard({ children }: { children: React.ReactNode }) {
  const { loading, error, dataset } = useWorkspace();
  if (loading) return <Loading label="Connecting to the research API" />;
  if (error) return <ErrorState error={error} />;
  if (!dataset)
    return (
      <Empty>
        No dataset loaded yet. Run <code>backend/.venv/Scripts/python scripts/seed_demo.py</code> or ingest data on the{" "}
        <a href="/market-data">Market Data</a> page.
      </Empty>
    );
  return <>{children}</>;
}

export default function App() {
  return (
    <AppLayout>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Guard><Overview /></Guard>} />
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
          <Route path="*" element={<Empty>Page not found.</Empty>} />
        </Routes>
      </Suspense>
    </AppLayout>
  );
}
