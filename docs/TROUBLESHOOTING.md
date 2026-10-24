# Troubleshooting

**"Service disconnected" in the app**
Diagnostics shows the service's last error and its process output. The most common cause is a missing Python environment: run `scripts/setup.sh`. If you moved the repository after building the `.app`, rebuild with `scripts/build-mac.sh`. *Restart service* tries again.

**"macOS blocked access to Notes"**
System Settings → Privacy & Security → Automation → enable *Notes* for Knowledge Bridge (or for Terminal when you run from source). Then click *Run checks again* in Diagnostics.

**"Apple Notes folder not found"**
Setup → *Load folders* and pick the folder from the list. Paths are `Parent/Child` for nested folders. If the same folder name exists in two accounts, choose the one with the right account.

**A note shows as a conflict**
You edited its copy in Obsidian, and the Apple Note changed too. Nothing was overwritten. (Missing copies are not conflicts; they are restored automatically.) In Sync history you can:
- *Use Apple Notes version*: your Obsidian edit is saved to `Knowledge Bridge/Logs/backups/…` first.
- *Keep Obsidian version*: the note stops syncing ("detached") until you choose *Re-attach* under Intelligence → Notes, which also backs up the Obsidian copy first.

**I deleted a mirrored file in Obsidian**
The next sync restores it from Apple Notes (a warning in Sync history says so). Deleting a copy is never treated as a conflict, because restoring it cannot lose anything. To stop a note from syncing, move it out of the Apple Notes folder you selected.

**A note is "locked"**
Password-protected notes cannot be read through Notes' scripting interface and are skipped.

**Entities look wrong or duplicated**
- Intelligence → Entities → open one → *Merge duplicate*, rename it, or edit its aliases.
- Intelligence → Notes → select notes → *Reprocess*. This asks the AI again. Your review decisions are re-applied automatically.

**Provider errors (401, 429, timeouts)**
401/403 means a wrong key: re-save it in Setup and press *Test connection*. Rate limits and timeouts are retried with backoff. A note that still fails is marked with an error and retried automatically on the next sync, and mirroring is never blocked by AI failures.

**Automatic sync didn't run**
It only runs while the Mac is awake and the app is open (closing the window on macOS keeps it running; quitting stops it). The sidebar shows whether automation is on. The Dashboard shows the next scheduled run.

**Start over with the generated layer**
Diagnostics → *Rebuild generated graph* re-renders every page from the registry. You can also delete `<vault>/Knowledge Bridge/` and rebuild; the result is identical.

**Start completely fresh**
Quit the app and delete `~/Library/Application Support/Knowledge Bridge/knowledge-bridge.sqlite3*`. That removes entities, decisions and sync state. Your vault files and Apple Notes are untouched. Delete the two vault folders too if you want a clean vault.

**Logs**
`~/Library/Application Support/Knowledge Bridge/logs/knowledge-bridge.log` (also shown in Diagnostics), plus `Knowledge Bridge/Logs/Sync history.md` in the vault.
