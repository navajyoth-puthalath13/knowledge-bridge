import { ago, post, Report, Status } from "../api";
import type { AppCtx } from "../App";
import { Dot, useAction, useToast } from "../ui";

export function Dashboard({ status, backend, refresh, go }: AppCtx) {
  const { busy, run } = useAction();
  const toast = useToast();

  if (backend && backend.state !== "running") {
    return (
      <div className="page">
        <div className="page-head"><div><h1>Dashboard</h1></div></div>
        <ServiceDown />
      </div>
    );
  }
  if (!status) return <div className="page"><div className="muted">Connecting to the local service…</div></div>;

  const { counts, activity, automation, provider } = status;
  const blocked = status.problems.length > 0;
  const startSync = (dry: boolean) =>
    run(dry ? "dry" : "sync", async () => {
      await post("/sync", { dry_run: dry });
      await refresh();
    });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <div className="sub">
            Last successful sync {ago(status.last_sync?.finished_at)}
            {automation.enabled && automation.next_run_at && !activity.running && <> · next automatic sync {ago(automation.next_run_at)}</>}
          </div>
        </div>
        <div className="row">
          <button className="btn" disabled={blocked || activity.running || !!busy} onClick={() => startSync(true)}>Dry run</button>
          <button className="btn primary" disabled={blocked || activity.running || !!busy} onClick={() => startSync(false)}>
            {activity.running ? "Syncing…" : "Sync now"}
          </button>
        </div>
      </div>

      {blocked && (
        <div className="banner warn">
          <strong>Setup needed</strong>
          <span className="grow">{status.problems.join(" ")}</span>
          <button className="btn small" onClick={() => go("setup")}>Open setup</button>
        </div>
      )}
      {!blocked && status.vault.known_to_obsidian === false && (
        <div className="banner warn">
          <strong>Not an open Obsidian vault</strong>
          <span className="grow">Pages are written to “{status.vault.path?.split("/").pop()}”, but Obsidian has never opened that folder, so nothing shows up in Obsidian. Choose the vault you use in Setup, or open this folder in Obsidian with <em>Open folder as vault</em>.</span>
          <button className="btn small" onClick={() => go("setup")}>Choose vault</button>
        </div>
      )}
      {counts.conflicts > 0 && (
        <div className="banner err">
          <strong>{counts.conflicts} conflict{counts.conflicts > 1 ? "s" : ""}</strong>
          <span className="grow">A mirrored note was changed in Obsidian and in Apple Notes. Those writes were stopped; nothing was overwritten.</span>
          <button className="btn small" onClick={() => go("history", "conflicts")}>Resolve</button>
        </div>
      )}

      <div className="grid cols-4" style={{ marginBottom: 12 }}>
        <StatusCard label="Apple Notes" ok={status.connector.ok} value={status.connector.folder || "No folder selected"}
                    detail={status.connector.message} />
        <StatusCard label="Obsidian vault" ok={status.vault.ok} value={status.vault.path ? status.vault.path.split("/").pop()! : "Not selected"}
                    detail={status.vault.path ? `${status.vault.mirror}/ · ${status.vault.generated}/` : "Choose your vault in Setup"} />
        <StatusCard label="AI provider" ok={provider.ready} warn={provider.cloud}
                    value={provider.name === "none" ? "Rules only (local)" : `${providerLabel(provider.name)} · ${provider.model}`}
                    detail={provider.cloud ? "Changed notes are sent to this cloud provider for analysis." :
                      provider.ready ? "Notes stay on this Mac." : "API key missing"} />
        <div className="card">
          <div className="card-label"><Dot tone={automation.enabled ? "ok" : "off"} pulse={activity.running} />Automation</div>
          <div className="card-value">{automation.enabled ? `Every ${automation.interval_minutes} min` : "Paused"}</div>
          <div className="card-detail">{automation.enabled ? "Runs while the Mac is awake and this app is open." : "Sync only runs when you start it."}</div>
          <button className="btn small" style={{ marginTop: 8 }} disabled={busy === "auto" || blocked}
                  onClick={() => run("auto", async () => { await post("/automation", { enabled: !automation.enabled }); await refresh(); })}>
            {automation.enabled ? "Pause" : "Resume"}
          </button>
        </div>
      </div>

      <div className="grid cols-5" style={{ marginBottom: 14 }}>
        <Metric label="Synced notes" value={counts.notes} onClick={() => go("intelligence", "notes")} />
        <Metric label="Entities" value={counts.entities} onClick={() => go("intelligence", "entities")} />
        <Metric label="Relationships" value={counts.relationships} onClick={() => go("intelligence", "relationships")} />
        <Metric label="Pending reviews" value={counts.reviews} attention={counts.reviews > 0} onClick={() => go("intelligence", "reviews")} />
        <Metric label="Notes with errors" value={counts.errors} attention={counts.errors > 0} onClick={() => go("intelligence", "notes")} />
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{activity.running ? (activity.dry_run ? "Dry run in progress" : "Sync in progress") : "Latest activity"}</h2>
          <div className="row">
            <button className="btn small" onClick={() => window.kb.openObsidian().then((ok) => !ok && toast("Select a vault first", true))}>Open Obsidian</button>
            <button className="btn small" onClick={() => window.kb.openFolder("generated").then((ok) => !ok && toast("Folder not found yet — run a sync first", true))}>Open knowledge folder</button>
            <button className="btn small" disabled={blocked || activity.running || !!busy}
                    onClick={() => run("rebuild", () => post("/rebuild"), "Knowledge pages rebuilt from the registry")}>Rebuild index</button>
          </div>
        </div>
        {activity.running ? <Progress activity={activity} /> : <LastReport report={status.last_report} go={go} />}
      </div>
    </div>
  );
}

export function providerLabel(name: string) {
  return { gemini: "Google Gemini", groq: "Groq", openai: "OpenAI", xai: "xAI Grok", ollama: "Ollama", mock: "Mock", none: "Rules only" }[name] ?? name;
}

function StatusCard({ label, ok, warn, value, detail }: { label: string; ok: boolean; warn?: boolean; value: string; detail: string }) {
  return (
    <div className="card">
      <div className="card-label"><Dot tone={!ok ? "err" : warn ? "warn" : "ok"} />{label}</div>
      <div className="card-value" title={value}>{value}</div>
      <div className="card-detail" title={detail}>{detail}</div>
    </div>
  );
}

function Metric({ label, value, attention, onClick }: { label: string; value: number; attention?: boolean; onClick?: () => void }) {
  return (
    <button className={`metric ${onClick ? "link" : ""} ${attention ? "attention" : ""}`} onClick={onClick}>
      <div className="metric-value">{value ?? "–"}</div>
      <div className="metric-label">{label}</div>
    </button>
  );
}

function Progress({ activity }: { activity: Status["activity"] }) {
  const total = activity.total ?? 0;
  const pct = total > 0 ? Math.round(((activity.current ?? 0) / total) * 100) : null;
  const phase = { starting: "Starting", discover: "Discovering notes", mirror: "Mirroring notes", analyse: "Recognising entities", render: "Writing knowledge pages", done: "Finishing" }[activity.phase ?? ""] ?? activity.phase;
  return (
    <div>
      <div className="row between"><strong>{phase}</strong><span className="muted small">{total ? `${activity.current} of ${total}` : ""}</span></div>
      <div className={`progress ${pct === null ? "indeterminate" : ""}`}><div style={pct === null ? undefined : { width: `${pct}%` }} /></div>
      <div className="muted small">{activity.message}</div>
    </div>
  );
}

function LastReport({ report, go }: { report: Report | null; go: AppCtx["go"] }) {
  if (!report) return <div className="muted">No sync has run since the app started. Use <strong>Sync now</strong>, or <strong>Dry run</strong> to preview changes without writing anything.</div>;
  const s = report.stats;
  const issues = report.errors.filter((e) => e.kind !== "warning");
  const tone = report.status === "ok" ? "ok" : report.status === "failed" ? "err" : "warn";
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <span className={`badge ${tone}`}>{report.dry_run ? "Dry run" : report.status}</span>
        <span className="muted small">
          {s.discovered} discovered · {s.mirrored} {report.dry_run ? "would be mirrored" : "mirrored"} · {s.unchanged} unchanged
          {!report.dry_run && <> · {s.processed} analysed · {s.entities_created} new / {s.entities_reused} reused entities · {s.relationships} relationships</>}
        </span>
      </div>
      {issues.length > 0 && (
        <div className="banner err" style={{ marginBottom: 8 }}>
          <span className="grow">{issues.slice(0, 3).map((e, i) => <div key={i}><strong>{e.note}:</strong> {e.message}</div>)}
            {issues.length > 3 && <div>…and {issues.length - 3} more</div>}</span>
          <button className="btn small" onClick={() => go("history")}>Details</button>
        </div>
      )}
      {report.dry_run && (
        <div className="table-wrap" style={{ maxHeight: 280 }}>
          <table>
            <thead><tr><th>Action</th><th>Note</th><th>Detail</th></tr></thead>
            <tbody>
              {report.plan.map((p, i) => (
                <tr key={i}><td><span className="badge plain">{p.action}</span></td><td>{p.note}</td>
                  <td className="muted small">{p.detail || p.path}{p.from ? ` (from ${p.from})` : ""}</td></tr>
              ))}
            </tbody>
          </table>
          {report.plan.length === 0 && <div className="muted">Nothing would change.</div>}
        </div>
      )}
    </div>
  );
}

function ServiceDown() {
  const { busy, run } = useAction();
  return (
    <div className="banner err">
      <strong>Service disconnected</strong>
      <span className="grow">
        The local Knowledge Bridge service is not running, so no syncing (manual or automatic) is happening.
        Check Diagnostics for the error, then restart it.
      </span>
      <button className="btn small" disabled={!!busy} onClick={() => run("r", () => window.kb.restartBackend())}>Restart service</button>
    </div>
  );
}
