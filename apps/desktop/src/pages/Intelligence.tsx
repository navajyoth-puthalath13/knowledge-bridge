import { useEffect, useMemo, useState } from "react";
import { get, patch, post, put, TYPE_LABEL } from "../api";
import type { AppCtx } from "../App";
import { Empty, ErrorBanner, Evidence, useAction, useLoad } from "../ui";

type Tab = "reviews" | "entities" | "relationships" | "notes" | "types";
const TABS: Array<[Tab, string]> = [
  ["reviews", "Review queue"], ["entities", "Entities"], ["relationships", "Relationships"], ["notes", "Notes"], ["types", "Entity types"],
];

export function Intelligence({ status, refresh, sub }: AppCtx) {
  const [tab, setTab] = useState<Tab>((TABS.find(([k]) => k === sub)?.[0]) ?? "reviews");
  useEffect(() => { if (sub && TABS.some(([k]) => k === sub)) setTab(sub as Tab); }, [sub]);
  const after = async () => { await refresh(); };
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Intelligence</h1><div className="sub">What the AI layer recognised, and the decisions waiting for you.</div></div>
      </div>
      <div className="tabs">
        {TABS.map(([k, label]) => (
          <button key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>
            {label}{k === "reviews" && status?.counts.reviews ? ` (${status.counts.reviews})` : ""}
          </button>
        ))}
      </div>
      {tab === "reviews" && <Reviews onChange={after} />}
      {tab === "entities" && <Entities onChange={after} />}
      {tab === "relationships" && <Relationships />}
      {tab === "notes" && <Notes onChange={after} running={!!status?.activity.running} />}
      {tab === "types" && <Types />}
    </div>
  );
}

// ------------------------------------------------------------------ reviews
function Reviews({ onChange }: { onChange: () => Promise<void> }) {
  const { data, error, reload } = useLoad(() => get<any[]>("/reviews"));
  const { busy, run } = useAction();
  const resolve = (id: number, action: string, target_id?: string) =>
    run(`r${id}`, async () => { await post(`/reviews/${id}/resolve`, { action, target_id }); await reload(); await onChange(); });

  if (error) return <ErrorBanner error={error} />;
  if (!data) return null;
  if (!data.length) return <div className="panel"><Empty>Nothing to review. Uncertain matches and suggested links will appear here.</Empty></div>;
  return (
    <div className="panel flush">
      {data.map((r) => (
        <div className="review" key={r.id}>
          <ReviewBody r={r} />
          <div className="row" style={{ justifyContent: "flex-end", maxWidth: 360 }}>
            {r.kind === "entity_match" && <>
              <button className="btn small primary" disabled={!!busy} onClick={() => resolve(r.id, "same")}>Same as “{r.payload.existing_name}”</button>
              <button className="btn small" disabled={!!busy} onClick={() => resolve(r.id, "different")}>Different</button>
            </>}
            {r.kind === "ambiguous_entity" && <AmbiguousActions r={r} busy={!!busy} resolve={resolve} />}
            {r.kind === "relationship" && <>
              <button className="btn small primary" disabled={!!busy} onClick={() => resolve(r.id, "approve")}>Approve</button>
              <button className="btn small" disabled={!!busy} onClick={() => resolve(r.id, "reject")}>Reject</button>
            </>}
          </div>
        </div>
      ))}
    </div>
  );
}

function ReviewBody({ r }: { r: any }) {
  const c = r.payload.candidate;
  if (r.kind === "relationship") {
    const rel = r.relationship;
    if (!rel) return <div className="muted">Relationship no longer exists.</div>;
    return (
      <div>
        <div className="row"><span className="badge">{rel.origin === "semantic" ? "Suggested link" : "Uncertain relationship"}</span>
          <span className="muted small">from “{r.note_title}” · confidence {Math.round((rel.confidence ?? 0) * 100)}%</span></div>
        <div style={{ marginTop: 6 }}>
          <strong>{rel.src_name}</strong> <span className="muted">— {rel.label || rel.type.replace("_", " ")} —</span> <strong>{rel.tgt_name}</strong>
        </div>
        <Evidence text={rel.evidence} />
      </div>
    );
  }
  return (
    <div>
      <div className="row">
        <span className="badge warn">{r.kind === "entity_match" ? "Possible duplicate" : "Ambiguous reference"}</span>
        <span className="muted small">{TYPE_LABEL[c.type] ?? c.type} in “{r.note_title}”</span>
      </div>
      <div style={{ marginTop: 6 }}><strong>{c.name}</strong>{c.surface && c.surface !== c.name && <span className="muted"> (written “{c.surface}”)</span>}
        {r.kind === "entity_match" && <span className="muted"> — {Math.round(r.payload.score * 100)}% similar to <strong>{r.payload.existing_name}</strong></span>}
      </div>
      <Evidence text={c.evidence} />
    </div>
  );
}

function AmbiguousActions({ r, busy, resolve }: { r: any; busy: boolean; resolve: (id: number, a: string, t?: string) => void }) {
  const type = r.payload.candidate.type;
  const [target, setTarget] = useState<string>(r.payload.options?.[0]?.id ?? "");
  const [others, setOthers] = useState<any[] | null>(null);
  useEffect(() => { void get<any[]>(`/entities?type=${type}`).then(setOthers); }, [type]);
  const options = useMemo(() => {
    const seen = new Set<string>();
    return [...(r.payload.options ?? []), ...(others ?? [])].filter((o) => !seen.has(o.id) && seen.add(o.id));
  }, [r, others]);
  return (
    <>
      <button className="btn small primary" disabled={busy} onClick={() => resolve(r.id, "create")}>Create new</button>
      {options.length > 0 && (
        <span className="row" style={{ gap: 4 }}>
          <select style={{ width: 160 }} value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Link to existing…</option>
            {options.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          <button className="btn small" disabled={busy || !target} onClick={() => resolve(r.id, "link", target)}>Link</button>
        </span>
      )}
      <button className="btn small ghost" disabled={busy} onClick={() => resolve(r.id, "dismiss")}>Ignore</button>
    </>
  );
}

// ------------------------------------------------------------------ entities
function Entities({ onChange }: { onChange: () => Promise<void> }) {
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const params = new URLSearchParams();
  if (type) params.set("type", type);
  if (q) params.set("q", q);
  const qs = params.toString();
  const { data, error, reload } = useLoad(() => get<any[]>(`/entities${qs ? "?" + qs : ""}`), [qs]);
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <select style={{ width: 200 }} value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All types</option>
          {Object.entries(TYPE_LABEL).filter(([k]) => !["note", "collection"].includes(k)).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input type="search" placeholder="Search names and aliases" style={{ maxWidth: 320 }} value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="muted small">{data ? `${data.length} entities` : ""}</span>
      </div>
      <ErrorBanner error={error} />
      <div className="panel flush">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Type</th><th>Also written as</th><th className="num">Mentions</th></tr></thead>
            <tbody>
              {(data ?? []).map((e) => (
                <tr key={e.id} className={`clickable ${selected === e.id ? "selected" : ""}`} onClick={() => setSelected(e.id)}>
                  <td><strong>{e.name}</strong></td><td>{TYPE_LABEL[e.type] ?? e.type}</td>
                  <td className="muted truncate">{e.aliases}</td><td className="num">{e.mentions}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && !data.length && <Empty>No entities yet. Run a sync to recognise books, podcasts, people and events.</Empty>}
        </div>
      </div>
      {selected && <EntityDrawer id={selected} close={() => setSelected(null)} changed={async () => { await reload(); await onChange(); }} />}
    </>
  );
}

function EntityDrawer({ id, close, changed }: { id: string; close: () => void; changed: () => Promise<void> }) {
  const { data, reload } = useLoad(() => get<any>(`/entities/${id}`), [id]);
  const [alias, setAlias] = useState("");
  const [name, setName] = useState("");
  const [into, setInto] = useState("");
  const [peers, setPeers] = useState<any[]>([]);
  const { busy, run } = useAction();
  const e = data?.entity;
  useEffect(() => {
    if (e) {
      setName(e.name);
      void get<any[]>(`/entities?type=${e.type}`).then((l) => setPeers(l.filter((x) => x.id !== e.id)));
    }
  }, [e?.id, e?.name]);
  if (!data) return <div className="drawer muted">Loading…</div>;
  const act = (k: string, fn: () => Promise<unknown>, msg: string) => run(k, async () => { await fn(); await reload(); await changed(); }, msg);
  return (
    <div className="drawer">
      <div className="row between"><span className="badge">{TYPE_LABEL[e.type] ?? e.type}</span><button className="btn small ghost" onClick={close}>Close</button></div>
      <h1 style={{ margin: "10px 0 2px" }}>{e.name}</h1>
      <div className="muted small mono">{e.id}</div>
      {e.page_path && <div className="muted small" style={{ marginTop: 4 }}>{e.page_path}</div>}

      <h3 style={{ marginTop: 18 }}>Canonical name</h3>
      <div className="input-row">
        <input type="text" value={name} onChange={(ev) => setName(ev.target.value)} />
        <button className="btn" disabled={!name || name === e.name || !!busy} onClick={() => act("n", () => patch(`/entities/${id}`, { name }), "Renamed")}>Rename</button>
      </div>

      <h3 style={{ marginTop: 16 }}>Aliases</h3>
      <div>
        {data.aliases.filter((a: any) => a.source !== "canonical").map((a: any) => (
          <span className="chip" key={a.alias}>{a.alias}
            <button title="Remove alias" onClick={() => act("ra", () => patch(`/entities/${id}`, { remove_alias: a.alias }), "Alias removed")}>×</button></span>
        ))}
      </div>
      <div className="input-row" style={{ marginTop: 6 }}>
        <input type="text" placeholder="Add another way this is written" value={alias} onChange={(ev) => setAlias(ev.target.value)} />
        <button className="btn" disabled={!alias || !!busy} onClick={() => act("a", async () => { await patch(`/entities/${id}`, { add_alias: alias }); setAlias(""); }, "Alias added")}>Add</button>
      </div>

      <h3 style={{ marginTop: 16 }}>Relationships</h3>
      {data.relationships.length === 0 && <div className="muted">None.</div>}
      {data.relationships.map((r: any) => {
        const out = r.src_id === id;
        return (
          <div key={r.id} style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
            <div className="row">
              <span className={`badge ${r.status === "accepted" ? "plain" : r.status === "pending" ? "warn" : "err"}`}>{r.label || r.type.replace("_", " ")}</span>
              <span>{out ? "→" : "←"} <strong>{out ? r.tgt_name : r.src_name}</strong></span>
              <span className="faint small">{TYPE_LABEL[out ? r.tgt_type : r.src_type]}</span>
            </div>
            {r.note_title && <div className="muted small">from “{r.note_title}”{r.confidence != null ? ` · ${Math.round(r.confidence * 100)}%` : ""} · {r.origin}</div>}
            <Evidence text={r.evidence} />
          </div>
        );
      })}

      <h3 style={{ marginTop: 16 }}>Merge duplicate</h3>
      <div className="muted small" style={{ marginBottom: 6 }}>Moves all aliases and relationships into another {TYPE_LABEL[e.type]?.toLowerCase()} and retires this one.</div>
      <div className="input-row">
        <select value={into} onChange={(ev) => setInto(ev.target.value)}>
          <option value="">Merge into…</option>
          {peers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button className="btn danger" disabled={!into || !!busy}
                onClick={() => confirm(`Merge “${e.name}” into “${peers.find((p) => p.id === into)?.name}”?`) &&
                  run("m", async () => { await post(`/entities/${id}/merge`, { into }); await changed(); close(); }, "Merged")}>Merge</button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ relationships
function Relationships() {
  const [status, setStatus] = useState("accepted");
  const [type, setType] = useState("");
  const qs = new URLSearchParams({ status, ...(type ? { type } : {}) }).toString();
  const { data, error } = useLoad(() => get<any[]>(`/relationships?${qs}`), [qs]);
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="segmented">
          {["accepted", "pending", "rejected"].map((s) => <button key={s} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>{s[0].toUpperCase() + s.slice(1)}</button>)}
        </div>
        <select style={{ width: 180 }} value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All relationship types</option>
          {["mentions", "about", "belongs_to", "related_to", "references"].map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <span className="muted small">{data ? `${data.length} shown` : ""}</span>
      </div>
      <ErrorBanner error={error} />
      <div className="panel flush">
        <div className="table-wrap">
          <table>
            <thead><tr><th>From</th><th>Relationship</th><th>To</th><th>Evidence</th><th>Source note</th></tr></thead>
            <tbody>
              {(data ?? []).map((r) => (
                <tr key={r.id}>
                  <td><strong>{r.src_name}</strong><div className="faint small">{TYPE_LABEL[r.src_type]}</div></td>
                  <td><span className="badge plain">{r.label || r.type}</span><div className="faint small">{r.origin}{r.confidence != null ? ` · ${Math.round(r.confidence * 100)}%` : ""}</div></td>
                  <td><strong>{r.tgt_name}</strong><div className="faint small">{TYPE_LABEL[r.tgt_type]}</div></td>
                  <td className="muted small" style={{ maxWidth: 300 }}>{r.evidence}</td>
                  <td className="muted small">{r.note_title ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && !data.length && <Empty>No {status} relationships.</Empty>}
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ notes
function Notes({ onChange, running }: { onChange: () => Promise<void>; running: boolean }) {
  const { data, error, reload } = useLoad(() => get<any[]>("/notes"));
  const [sel, setSel] = useState<Set<string>>(new Set());
  const { busy, run } = useAction();
  useEffect(() => { if (!running) void reload(); }, [running]);
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const tone = (s: string) => (s === "active" ? "ok" : s === "missing" || s === "detached" ? "plain" : "err");
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <button className="btn primary" disabled={!sel.size || running || !!busy}
                onClick={() => run("rp", async () => { await post("/notes/reprocess", { note_ids: [...sel] }); setSel(new Set()); await onChange(); }, "Reprocessing started")}>
          Reprocess {sel.size || ""} selected
        </button>
        <span className="muted small">Reprocessing asks the AI again for the selected notes and replaces what they previously contributed. Your review decisions are kept.</span>
      </div>
      <ErrorBanner error={error} />
      <div className="panel flush">
        <div className="table-wrap">
          <table>
            <thead><tr><th style={{ width: 28 }} /><th>Note</th><th>Status</th><th>Category</th><th>Analysed</th><th /></tr></thead>
            <tbody>
              {(data ?? []).map((n) => (
                <tr key={n.id}>
                  <td><input type="checkbox" disabled={n.status !== "active"} checked={sel.has(n.id)} onChange={() => toggle(n.id)} /></td>
                  <td><strong>{n.title}</strong><div className="faint small">{n.mirror_path}</div>
                    {n.last_error && <div className="small" style={{ color: "var(--err)" }}>{n.last_error}</div>}</td>
                  <td><span className={`badge ${tone(n.status)}`}>{n.status}</span></td>
                  <td className="muted">{n.category ?? "—"}</td>
                  <td className="muted small">{n.processed_at ? new Date(n.processed_at).toLocaleString() : "not yet"}</td>
                  <td>{n.status === "detached" && <button className="btn small" disabled={!!busy}
                        onClick={() => run("ra", async () => { await post(`/notes/${encodeURIComponent(n.id)}/reattach`); await reload(); },
                          "Re-attached. The Obsidian copy was backed up; the next sync restores the Apple Notes version.")}>Re-attach</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data && !data.length && <Empty>No notes synced yet.</Empty>}
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ types
function Types() {
  const { data, reload } = useLoad(() => get<any[]>("/entity-types"));
  const { run } = useAction();
  return (
    <div className="panel">
      <div className="muted small" style={{ marginBottom: 10 }}>Disabled types are not extracted from notes. Existing entities are kept. Changes apply to notes analysed from now on (reprocess notes to apply them to older notes).</div>
      {(data ?? []).map((t) => (
        <div className="field" key={t.key}>
          <label className="check" style={{ fontWeight: 550 }}>
            <input type="checkbox" checked={t.enabled} onChange={(e) => run(t.key, async () => { await put(`/entity-types/${t.key}`, { enabled: e.target.checked }); await reload(); })} />
            {t.label}
          </label>
          <div className="muted">{t.description}
            <div className="faint small">{t.collection ? `Collection: ${t.collection}` : "No collection"}{t.parent_type ? ` · belongs to ${TYPE_LABEL[t.parent_type]}` : ""}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
