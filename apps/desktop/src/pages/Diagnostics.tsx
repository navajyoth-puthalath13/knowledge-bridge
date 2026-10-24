import { useEffect, useState } from "react";
import { BackendInfo, get, post } from "../api";
import type { AppCtx } from "../App";
import { Dot, ErrorBanner, useAction, useLoad } from "../ui";
import { providerLabel } from "./Dashboard";

export function Diagnostics({ backend, status }: AppCtx) {
  const running = backend?.state === "running";
  const diag = useLoad(() => (running ? get<any>("/diagnostics") : Promise.resolve(null)), [running]);
  const [svc, setSvc] = useState<BackendInfo | null>(null);
  const { busy, run } = useAction();
  useEffect(() => { void window.kb.backendInfo().then(setSvc); }, [backend?.state]);
  const d = diag.data;

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Diagnostics</h1><div className="sub">Health checks for every part of the bridge.</div></div>
        <div className="row">
          <button className="btn" disabled={!running || diag.loading} onClick={() => diag.reload()}>{diag.loading ? "Checking…" : "Run checks again"}</button>
          <button className="btn" disabled={!!busy} onClick={() => run("restart", async () => { setSvc(await window.kb.restartBackend()); await diag.reload(); }, "Service restarted")}>Restart service</button>
          <button className="btn primary" disabled={!running || !!busy || !!status?.problems.length || !!status?.activity.running}
                  onClick={() => run("rebuild", () => post("/rebuild"), "Generated graph rebuilt from the registry and source mirrors")}>Rebuild generated graph</button>
        </div>
      </div>
      <ErrorBanner error={diag.error} />

      <div className="grid cols-2">
        <section className="panel">
          <h2>Local service</h2>
          <div className="kv small">
            <div>State</div><div className="row"><Dot tone={running ? "ok" : "err"} />{backend?.state ?? "unknown"}</div>
            <div>Address</div><div className="mono">{svc?.port ? `127.0.0.1:${svc.port} (localhost only, token-protected)` : "—"}</div>
            <div>Python</div><div className="mono" style={{ wordBreak: "break-all" }}>{svc?.python}</div>
            <div>Automatic restarts</div><div>{svc?.restarts ?? 0}</div>
            {backend?.error && <><div>Last error</div><div style={{ color: "var(--err)" }}>{backend.error}</div></>}
          </div>
        </section>
        <section className="panel">
          <h2>Connections</h2>
          {d ? (
            <div className="kv small">
              <div>Apple Notes</div><div className="row"><Dot tone={d.connector.ok ? "ok" : "err"} />{d.connector.message}</div>
              <div>AI provider</div><div className="row"><Dot tone={d.provider.ready ? "ok" : "err"} />
                {providerLabel(d.provider.name)}{d.provider.model ? ` · ${d.provider.model}` : ""}{d.provider.cloud ? " · cloud" : " · local"}
                {d.provider.key_set === false ? " · API key missing" : ""}</div>
              <div>Key storage</div><div>{d.provider.key_storage}</div>
            </div>
          ) : <div className="muted">{running ? "Checking…" : "Service not running."}</div>}
        </section>
      </div>

      {d && (
        <>
          <section className="panel">
            <h2>Source integrity</h2>
            <div className="row small" style={{ marginBottom: 8 }}>
              <span className="badge ok">{d.integrity.ok} mirrors match their checksum</span>
              {d.integrity.modified.length > 0 && <span className="badge warn">{d.integrity.modified.length} edited outside Knowledge Bridge</span>}
              {d.integrity.missing.length > 0 && <span className="badge err">{d.integrity.missing.length} missing</span>}
            </div>
            {[...d.integrity.modified.map((m: any) => ({ ...m, kind: "edited" })), ...d.integrity.missing.map((m: any) => ({ ...m, kind: "missing" }))].map((m: any) => (
              <div key={m.id} className="small"><span className={`badge ${m.kind === "edited" ? "warn" : "err"}`}>{m.kind}</span> {m.title} <span className="muted">· {m.path}</span></div>
            ))}
            <div className="muted small" style={{ marginTop: 6 }}>
              Edited mirrors are never overwritten silently: when the Apple Note changes too, the sync stops for that note and asks you to choose.
            </div>
          </section>

          <section className="panel">
            <h2>Database</h2>
            <div className="kv small">
              <div>Integrity</div><div className="row"><Dot tone={d.database.problems.length ? "err" : "ok"} />{d.database.problems.length ? d.database.problems.join("; ") : "OK"}</div>
              <div>Unfinished writes</div><div>{d.database.pending_writes} {d.database.pending_writes ? "(recovered automatically on the next sync)" : ""}</div>
              <div>Location</div><div className="mono">{d.database.path}</div>
              <div>Data folder</div><div className="mono">{d.data_dir}</div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-head"><h2>Recent log</h2><span className="muted small">last 200 lines</span></div>
            <pre className="log">{d.log.length ? d.log.join("\n") : "(empty)"}</pre>
          </section>
        </>
      )}
      {svc?.logs && svc.logs.length > 0 && (
        <section className="panel">
          <h2>Service process output</h2>
          <pre className="log">{svc.logs.slice(-80).join("\n")}</pre>
        </section>
      )}
    </div>
  );
}
