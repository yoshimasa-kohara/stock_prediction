// 画面の URL と、表示する画面の対応
import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router";

import Layout from "./components/Layout";
import StatusProvider from "./context/StatusProvider";
import AccuracyComparePage from "./pages/AccuracyComparePage";
import CandidatesPage from "./pages/CandidatesPage";
import HoldingsPage from "./pages/HoldingsPage";
import NotFound from "./pages/NotFound";
import ResultsPage from "./pages/ResultsPage";
import RunsPage from "./pages/RunsPage";
import SearchPage from "./pages/SearchPage";
import TopPage from "./pages/TopPage";

// チャートの部品が大きいので、チャートのある画面は開いたときに読み込む(最初の画面を速く出すため)
const StockDetailPage = lazy(() => import("./pages/StockDetailPage"));
const AccuracyPage = lazy(() => import("./pages/AccuracyPage"));

const loading = <p className="loading">読み込み中…</p>;

export default function App() {
  return (
    <StatusProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<TopPage />} />
          <Route path="candidates" element={<CandidatesPage />} />
          <Route path="candidates/results" element={<ResultsPage />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="holdings" element={<HoldingsPage />} />
          <Route
            path="accuracy"
            element={
              <Suspense fallback={loading}>
                <AccuracyPage />
              </Suspense>
            }
          />
          <Route path="accuracy/compare" element={<AccuracyComparePage />} />
          <Route path="runs" element={<RunsPage />} />
          <Route
            path="stocks/:code"
            element={
              <Suspense fallback={loading}>
                <StockDetailPage />
              </Suspense>
            }
          />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </StatusProvider>
  );
}
