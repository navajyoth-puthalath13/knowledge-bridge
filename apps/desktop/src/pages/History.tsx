import { useEffect, useState } from "react";
import { get, post } from "../api";
import type { AppCtx } from "../App";
import { Empty, ErrorBanner, useAction, useLoad } from "../ui";

export function History({ status, refresh }: AppCtx) {
  const runs = useLoad(() => get<any[]>("/runs"));
  const conflicts = useLoad(() => get<any[]>("/conflicts"));
  const [open, setOpen] = useState<number | null>(null);
  const { busy, run } = useAction();
  const running = !!status?.activity.running;
  useEffect(() => {
    if (!running) { void runs.reload(); void conflicts.reload(); }
  }, [running]);

  const resolve = (id: number, action: string) =>
    run(`c${id}`, async () => { await post(`/conflicts/${id}/resolve`, { action }); await conflicts.reload(); await refresh(); },
      action === "use_apple_notes" ? "Apple Notes version restored; your Obsidian edit was backed up in Logs/backups."
        : "Obsidian version kept. This note will no longer sync until you re-attach it.");

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Sync history</h1><div className="sub">Every run, what changed, and anything that needs attention.</div></div>
        <button className="btn primary" disabled={running || !!busy || !!status?.problems.length}
                onClick={() => run("retry", async () => { await post("/sync", {}); await refresh(); })}>
          {running ? "Syncing…" : "Sync now (retries failed notes)"}
        </button>
      </div>

      {(conflicts.data?.length ?? 0) > 0 && (
        <section className="panel">
          <h2>Conflicts</h2>
          <div className="muted small" style={{ marginBottom: 10 }}>
            These mirrored notes were changed in Obsidian <em>and</em> in Apple Notes. The write was stopped, so both versions still exist. Choose which one the mirror should keep.
          </div>
          {conflicts.data!.map((c) => (
            <div key={c.id} style={{ borderTop: "1px solid var(--border)", padding: "12px 0" }}>
              <div className="row between">
                <div><strong>{c.title}</strong> <span className="muted small">· {c.mirror_path} · {c.kind.replace(/_/g, " ")}</span></div>
                <div className="row">
                  <button className="btn small primary" disabled={!!busy} onClick={() => resolve(c.id, "use_apple_notes")}>Use Apple Notes version</button>
                  <button className="btn small" disabled={!!busy} onClick={() => resolve(c.id, "keep_obsidian")}>Keep Obsidian version</button>
                </div>
              </div>
              <div className="split" style={{ marginTop: 8 }}>
                <div><div className="muted small">Current file in Obsidian</div><pre>{c.obsidian_text ?? "(file was deleted)"}</pre></div>
                <div><div className="muted small">Incoming from Apple Notes</div><pre>{c.incoming_text}</pre></div>
              </div>
            </div>
          ))}
        </section>
      )}

      <ErrorBanner error={runs.error} />
      <div className="panel flush">
        <table>
          <thead><tr><th>Started</th><th>Trigger</th><th>Status</th><th className="num">Discovered</th><th className="num">Mirrored</th>
            <th className="num">Skipped</th><th className="num">Entities new / reused</th><th className="num">Relationships</th><th className="num">Issues</th></tr></thead>
          <tbody>
            {(runs.data ?? []).map((r) => {
              const s = r.stats ?? {};
              const issues = (r.errors ?? []).filter((e: any) => e.kind !== "warning").length;
              const tone = r.status === "ok" ? "ok" : r.status === "running" ? "plain" : r.status === "partial" ? "warn" : "err";
              return [
                <tr key={r.id} className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)}>
                  <td>{new Date(r.started_at).toLocaleString()}</td><td className="muted">{r.trigger}</td>
                  <td><span className={`badge ${tone}`}>{r.status}</span></td>
                  <td className="num">{s.discovered ?? "–"}</td><td className="num">{s.mirrored ?? "–"}</td>
                  <td className="num">{(s.unchanged ?? 0) + (s.skipped ?? 0) + (s.locked ?? 0)}</td>
                  <td className="num">{s.entities_created ?? 0} / {s.entities_reused ?? 0}</td><td className="num">{s.relationships ?? 0}</td>
                  <td className="num">{issues || ""}{(r.errors ?? []).length > issues ? <span className="faint"> +{(r.errors ?? []).length - issues}w</span> : ""}</td>
                </tr>,
                open === r.id && (
                  <tr key={`${r.id}-d`}><td colSpan={9} style={{ background: "var(--bg)" }}>
                    <div className="kv small" style={{ marginBottom: 8 }}>
                      <div>Finished</div><div>{r.finished_at ? new Date(r.finished_at).toLocaleString() : "—"}</div>
                      <div>Notes</div><div>{s.created ?? 0} created · {s.updated ?? 0} updated · {s.renamed ?? 0} renamed · {s.unchanged ?? 0} unchanged · {s.missing ?? 0} missing · {s.locked ?? 0} locked · {s.conflicts ?? 0} conflicts</div>
                      <div>Analysis</div><div>{s.processed ?? 0} notes analysed · {s.llm_calls ?? 0} AI calls · {s.reviews ?? 0} new reviews · {s.pages_written ?? 0} pages written</div>
                    </div>
                    {(r.errors ?? []).length === 0 ? <div className="muted small">No errors or warnings.</div> :
                      (r.errors as any[]).map((e, i) => (
                        <div key={i} className="small" style={{ padding: "2px 0" }}>
                          <span className={`badge ${e.kind === "warning" ? "plain" : "err"}`}>{e.kind}</span> <strong>{e.note}</strong>: {e.message}
                        </div>))}
                  </td></tr>
                ),
              ];
            })}
          </tbody>
        </table>
        {runs.data && !runs.data.length && <Empty>No syncs yet.</Empty>}
      </div>
    </div>
  );
}
