import { useEffect, useState } from "react";
import { Config, get, post, put } from "../api";
import type { AppCtx } from "../App";
import { useAction, useLoad, useToast } from "../ui";

type ProviderKey = Config["provider"]["name"];
const PROVIDERS: Array<{ key: ProviderKey; label: string; local: boolean; free: boolean; placeholder: string; keyUrl?: string }> = [
  { key: "ollama", label: "Ollama · free, on this Mac", local: true, free: true, placeholder: "llama3.1:8b" },
  { key: "gemini", label: "Google Gemini · free tier", local: false, free: true, placeholder: "gemini-2.5-flash", keyUrl: "aistudio.google.com/apikey" },
  { key: "groq", label: "Groq · free tier", local: false, free: true, placeholder: "llama-3.3-70b-versatile", keyUrl: "console.groq.com/keys" },
  { key: "none", label: "Rules only · free", local: true, free: true, placeholder: "" },
  { key: "openai", label: "OpenAI · paid", local: false, free: false, placeholder: "gpt-4.1-mini" },
  { key: "xai", label: "xAI Grok · paid", local: false, free: false, placeholder: "grok-3-mini" },
];
const CLOUD: ProviderKey[] = ["gemini", "groq", "openai", "xai"];

interface Folder { account: string; path: string; note_count: number | null }

export function Setup({ status, refresh, sub }: AppCtx) {
  const { data: saved, reload } = useLoad(() => get<Config>("/config"));
  const [cfg, setCfg] = useState<Config | null>(null);
  const [folders, setFolders] = useState<Folder[] | null>(null);
  const [key, setKey] = useState("");
  const [login, setLogin] = useState<{ supported: boolean; enabled: boolean }>({ supported: false, enabled: false });
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);
  const { busy, run } = useAction();
  const toast = useToast();

  useEffect(() => { if (saved) setCfg(structuredClone(saved)); }, [saved]);
  useEffect(() => { void window.kb.loginItem().then(setLogin); }, []);
  if (!cfg) return <div className="page muted">Loading…</div>;

  const dirty = JSON.stringify(cfg) !== JSON.stringify(saved);
  const set = (fn: (c: Config) => void) => setCfg((c) => { const n = structuredClone(c!); fn(n); return n; });
  const provider = PROVIDERS.find((p) => p.key === cfg.provider.name) ?? PROVIDERS[0];
  const save = () => run("save", async () => {
    const r = await put<{ problems: string[] }>("/config", cfg);
    await reload();
    await refresh();
    toast(r.problems.length ? `Saved. Still needed: ${r.problems.join(" ")}` : "Settings saved");
  });
  const pick = async (title: string, apply: (c: Config, path: string) => void) => {
    const p = await window.kb.pickFolder(title);
    if (p) set((c) => apply(c, p));
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Setup</h1><div className="sub">Settings are stored on this Mac, outside the repository and your vault.</div></div>
        <div className="row">
          {dirty && <span className="muted small">Unsaved changes</span>}
          <button className="btn" disabled={!dirty} onClick={() => saved && setCfg(structuredClone(saved))}>Revert</button>
          <button className="btn primary" disabled={!dirty || busy === "save"} onClick={save}>Save</button>
        </div>
      </div>

      {sub === "first-run" && (
        <div className="banner info">
          <strong>Welcome</strong>
          <span className="grow">Choose your Apple Notes folder and Obsidian vault, pick how notes are analysed, then save and run a dry run from the Dashboard. Apple Notes is only ever read — nothing in it is changed.</span>
        </div>
      )}

      <section className="panel">
        <h2>Apple Notes source</h2>
        <div className="field">
          <label>Read notes from</label>
          <div>
            <div className="segmented">
              <button className={cfg.source.kind === "apple_notes" ? "on" : ""} onClick={() => set((c) => { c.source.kind = "apple_notes"; })}>Apple Notes folder</button>
              <button className={cfg.source.kind === "folder" ? "on" : ""} onClick={() => set((c) => { c.source.kind = "folder"; })}>Exported files folder</button>
            </div>
            <div className="hint">
              {cfg.source.kind === "apple_notes"
                ? "Reads Notes.app directly (read-only). macOS will ask once to let Knowledge Bridge control Notes."
                : "Fallback: reads .html, .md or .txt files exported from Notes into a folder."}
            </div>
          </div>
        </div>
        {cfg.source.kind === "apple_notes" ? (
          <>
            <div className="field">
              <label>Folder</label>
              <div>
                <div className="input-row">
                  <select value={`${cfg.source.account}\u0000${cfg.source.folder}`}
                          onChange={(e) => { const [a, f] = e.target.value.split("\u0000"); set((c) => { c.source.account = a; c.source.folder = f; }); }}>
                    <option value={`${cfg.source.account}\u0000${cfg.source.folder}`}>
                      {cfg.source.folder ? `${cfg.source.account ? cfg.source.account + " › " : ""}${cfg.source.folder}` : "Choose a folder…"}
                    </option>
                    {(folders ?? []).map((f) => (
                      <option key={f.account + f.path} value={`${f.account}\u0000${f.path}`}>
                        {f.account} › {f.path}{f.note_count != null ? ` (${f.note_count})` : ""}
                      </option>
                    ))}
                  </select>
                  <button className="btn" disabled={busy === "folders"}
                          onClick={() => run("folders", async () => setFolders(await get<Folder[]>("/source/folders")))}>
                    {busy === "folders" ? "Loading…" : "Load folders"}
                  </button>
                </div>
                <div className="hint">Pick a small, dedicated folder for your knowledge notes.</div>
              </div>
            </div>
            <div className="field">
              <label>Options</label>
              <div>
                <label className="check"><input type="checkbox" checked={cfg.source.include_subfolders} onChange={(e) => set((c) => { c.source.include_subfolders = e.target.checked; })} />Include subfolders</label>
                <label className="check"><input type="checkbox" checked={cfg.source.export_attachments} onChange={(e) => set((c) => { c.source.export_attachments = e.target.checked; })} />Copy attachments into the vault (best effort)</label>
              </div>
            </div>
          </>
        ) : (
          <div className="field">
            <label>Folder path</label>
            <div className="input-row">
              <input type="text" value={cfg.source.folder_path} onChange={(e) => set((c) => { c.source.folder_path = e.target.value; })} />
              <button className="btn" onClick={() => pick("Choose the exported notes folder", (c, p) => { c.source.folder_path = p; })}>Browse…</button>
            </div>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Obsidian vault</h2>
        <div className="field">
          <label>Vault</label>
          <div>
            <div className="input-row">
              <input type="text" placeholder="/Users/you/Documents/Vault" value={cfg.vault_path} onChange={(e) => set((c) => { c.vault_path = e.target.value; })} />
              <button className="btn" onClick={() => pick("Choose your Obsidian vault", (c, p) => { c.vault_path = p; })}>Browse…</button>
            </div>
          </div>
        </div>
        <div className="field">
          <label>Mirrored notes folder</label>
          <div>
            <input type="text" value={cfg.mirror_dir} onChange={(e) => set((c) => { c.mirror_dir = e.target.value; })} />
            <div className="hint">Faithful copies of your Apple Notes. Knowledge Bridge never adds links or AI text to these.</div>
          </div>
        </div>
        <div className="field">
          <label>Generated knowledge folder</label>
          <div>
            <input type="text" value={cfg.generated_dir} onChange={(e) => set((c) => { c.generated_dir = e.target.value; })} />
            <div className="hint">Entity pages, collections and the index. The AI layer can only write here.</div>
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>AI analysis</h2>
        <div className="field">
          <label>Provider</label>
          <div>
            <div className="segmented">
              {PROVIDERS.map((p) => (
                <button key={p.key} className={cfg.provider.name === p.key ? "on" : ""} onClick={() => { set((c) => { c.provider.name = p.key; c.provider.model = ""; }); setTest(null); }}>{p.label}</button>
              ))}
            </div>
            {provider.local ? (
              <div className="banner ok" style={{ marginTop: 10, marginBottom: 0 }}>
                <span className="grow">{cfg.provider.name === "none"
                  ? "Local only. No AI is used: entities you have already confirmed are recognised by name, but new books, people and events are not discovered."
                  : "Local only. Note text is sent to the Ollama server on this Mac and nowhere else."}</span>
              </div>
            ) : (
              <div className="banner warn" style={{ marginTop: 10, marginBottom: 0 }}>
                <span className="grow">
                  <strong>{provider.free ? "Free cloud tier." : "Paid cloud service."}</strong> The text of new or changed notes in the selected folder is sent to {provider.label.split(" ·")[0]} for entity recognition. Unchanged notes are not re-sent; results are cached locally.
                  {provider.free && " Free tiers have rate limits (large first syncs are slowed down automatically), and Google may use free-tier Gemini requests to improve its products."}
                  {!provider.free && " You are billed by the provider per use."}
                </span>
              </div>
            )}
          </div>
        </div>
        {cfg.provider.name !== "none" && (
          <div className="field">
            <label>Model</label>
            <div>
              <input type="text" placeholder={provider.placeholder} value={cfg.provider.model} onChange={(e) => set((c) => { c.provider.model = e.target.value; })} />
              {cfg.provider.name === "ollama" && (
                <input type="text" style={{ marginTop: 6 }} placeholder="Server URL (default http://127.0.0.1:11434/v1)" value={cfg.provider.base_url} onChange={(e) => set((c) => { c.provider.base_url = e.target.value; })} />
              )}
            </div>
          </div>
        )}
        {CLOUD.includes(cfg.provider.name) && (
          <div className="field">
            <label>API key</label>
            <div>
              <div className="input-row">
                <input type="password" autoComplete="off" placeholder={status?.provider.name === cfg.provider.name && status.provider.key_set ? "•••••••• saved" : "Paste your API key"} value={key} onChange={(e) => setKey(e.target.value)} />
                <button className="btn" disabled={!key || busy === "key"}
                        onClick={() => run("key", async () => { await put(`/secrets/${cfg.provider.name}`, { value: key }); setKey(""); await refresh(); }, "API key saved")}>Save key</button>
              </div>
              <div className="hint">
                {provider.keyUrl && <>Get a free key at <span className="mono">{provider.keyUrl}</span> (no payment card needed). </>}
                Stored in {status?.provider.key_storage ?? "the macOS Keychain"}. Never written to the repository, the vault or the settings file.
              </div>
            </div>
          </div>
        )}
        <div className="field">
          <label>Connection</label>
          <div>
            <div className="row">
              <button className="btn" disabled={dirty || busy === "test"} onClick={() => run("test", async () => setTest(await post("/provider/test")))}>
                {busy === "test" ? "Testing…" : "Test connection"}
              </button>
              {dirty && <span className="muted small">Save first to test these settings.</span>}
            </div>
            {test && <div className={`banner ${test.ok ? "ok" : "err"}`} style={{ marginTop: 8, marginBottom: 0 }}><span className="grow">{test.message}</span></div>}
          </div>
        </div>
        <div className="field">
          <label>Suggestions</label>
          <div>
            <label className="check"><input type="checkbox" checked={cfg.intelligence.semantic_suggestions} onChange={(e) => set((c) => { c.intelligence.semantic_suggestions = e.target.checked; })} />Suggest related notes (always reviewed by you before they appear in the graph)</label>
            <div className="row small muted" style={{ marginTop: 6 }}>
              Auto-accept entities at confidence ≥
              <input type="number" min={0.5} max={1} step={0.05} style={{ width: 80 }} value={cfg.intelligence.auto_accept_confidence}
                     onChange={(e) => set((c) => { c.intelligence.auto_accept_confidence = Number(e.target.value); })} />
            </div>
          </div>
        </div>
      </section>

      <section className="panel">
        <h2>Automation</h2>
        <div className="field">
          <label>Automatic sync</label>
          <div>
            <label className="check"><input type="checkbox" checked={cfg.automation_enabled} onChange={(e) => set((c) => { c.automation_enabled = e.target.checked; })} />Sync automatically while the app is open</label>
            <div className="row small muted" style={{ marginTop: 6 }}>
              Every <input type="number" min={1} max={1440} style={{ width: 80 }} value={cfg.sync_interval_minutes}
                           onChange={(e) => set((c) => { c.sync_interval_minutes = Number(e.target.value); })} /> minutes
            </div>
            <div className="hint">Sync cannot run while the Mac is asleep or the app is quit. It resumes on the next launch.</div>
          </div>
        </div>
        {login.supported && (
          <div className="field">
            <label>Start at login</label>
            <label className="check"><input type="checkbox" checked={login.enabled} onChange={async (e) => setLogin(await window.kb.loginItem(e.target.checked))} />Open Knowledge Bridge when I log in</label>
          </div>
        )}
      </section>
    </div>
  );
}
