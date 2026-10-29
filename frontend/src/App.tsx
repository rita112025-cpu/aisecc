import { NavLink, Route, Routes } from "react-router-dom";
import { DashboardPage } from "./pages/DashboardPage";
import { GitPage } from "./pages/GitPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { RepairsPage } from "./pages/RepairsPage";
import { ScansPage } from "./pages/ScansPage";
import { SecurityPage } from "./pages/SecurityPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TestsPage } from "./pages/TestsPage";

const NAV = [
  ["/", "總覽 Dashboard"],
  ["/projects", "專案管理"],
  ["/scans", "程式碼掃描"],
  ["/security", "安全風險"],
  ["/tests", "測試中心"],
  ["/repairs", "AI 修復建議"],
  ["/git", "Git 變更比較"],
  ["/history", "歷史報告"],
  ["/settings", "系統設定"],
] as const;

export function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>AI 軟體工程<br />控制中心</h1>
        <nav aria-label="主選單">
          {NAV.map(([to, text]) => (
            <NavLink key={to} to={to} end={to === "/"}>{text}</NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/scans" element={<ScansPage />} />
          <Route path="/security" element={<SecurityPage />} />
          <Route path="/tests" element={<TestsPage />} />
          <Route path="/repairs" element={<RepairsPage />} />
          <Route path="/git" element={<GitPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="state">找不到頁面</div>} />
        </Routes>
      </main>
    </div>
  );
}
