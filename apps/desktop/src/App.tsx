import { useCallback, useEffect, useState } from "react";
import { BackendInfo, get, Status } from "./api";
import { Dashboard } from "./pages/Dashboard";
import { Diagnostics } from "./pages/Diagnostics";
import { History } from "./pages/History";
import { Intelligence } from "./pages/Intelligence";
import { Setup } from "./pages/Setup";
import { Dot, ToastProvider } from "./ui";

export type Page = "dashboard" | "setup" | "intelligence" | "history" | "diagnostics";

const NAV: Array<[Page, string]> = [
  ["dashboard", "Dashboard"],
  ["setup", "Setup"],
  ["intelligence", "Intelligence"],
  ["history", "Sync history"],
  ["diagnostics", "Diagnostics"],
];

export interface AppCtx {
  status: Status | null;
  backend: BackendInfo | null;
  refresh: () => Promise<void>;
  go: (p: Page, sub?: string) => void;
  sub?: string;
}

export function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [sub, setSub] = useState<string | undefined>();
  const [status, setStatus] = useState<Status | null>(null);
  const [backend, setBackend] = useState<BackendInfo | null>(null);

  const refresh = useCallback(async () => {
    try {
      setStatus(await get<Status>("/status"));
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void window.kb.backendInfo().then(setBackend);
    return window.kb.onBackendChange((info) => {
      setBackend(info);
      void refresh();
    });
  }, [refresh]);

  const running = status?.activity.running;
  useEffect(() => {
    void refresh();
    const t = window.setInterval(refresh, running ? 1000 : 4000);
    return () => window.clearInterval(t);
  }, [refresh, running]);

  // first launch: send the user to Setup
  useEffect(() => {
    if (status && status.problems.length && status.counts.notes_total === 0 && page === "dashboard" && !sub) {
      setPage("setup");
      setSub("first-run");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.problems.length]);

  const go = (p: Page, s?: string) => {
    setPage(p);
    setSub(s);
  };
  const ctx: AppCtx = { status, backend, refresh, go, sub };
  const svc = backend?.state ?? "starting";

  return (
    <ToastProvider>
      <div className="app">
        <aside className="sidebar">
          <div className="brand"><span className="brand-mark" />Knowledge Bridge</div>
          {NAV.map(([key, label]) => (
            <button key={key} className={`nav ${page === key ? "active" : ""}`} onClick={() => go(key)}>
              <span>{label}</span>
              {key === "intelligence" && !!status?.counts.reviews && <span className="badge warn">{status.counts.reviews}</span>}
              {key === "history" && !!status?.counts.conflicts && <span className="badge err">{status.counts.conflicts}</span>}
            </button>
          ))}
          <div className="sidebar-foot">
            <div className="row">
              <Dot tone={svc === "running" ? "ok" : svc === "starting" ? "warn" : "err"} pulse={svc === "starting"} />
              Service {svc === "running" ? "connected" : svc}
            </div>
            <div className="row">
              <Dot tone={status?.automation.enabled ? (running ? "ok" : "ok") : "off"} pulse={running} />
              {running ? "Syncing…" : status?.automation.enabled ? "Automatic sync on" : "Automatic sync off"}
            </div>
            {status && <div className="faint">v{status.version}</div>}
          </div>
        </aside>
        <main className="main">
          {page === "dashboard" && <Dashboard {...ctx} />}
          {page === "setup" && <Setup {...ctx} />}
          {page === "intelligence" && <Intelligence {...ctx} />}
          {page === "history" && <History {...ctx} />}
          {page === "diagnostics" && <Diagnostics {...ctx} />}
        </main>
      </div>
    </ToastProvider>
  );
}
