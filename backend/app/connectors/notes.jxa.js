// Read-only bridge to Notes.app via Apple's JavaScript for Automation (JXA).
// Invoked as: osascript -l JavaScript notes.jxa.js '<json request>'
// It never calls make/delete/set on Notes objects; it only reads properties
// and (optionally) saves attachment copies to a temporary directory.
ObjC.import('Foundation');

function iso(d) {
  try { return d ? d.toISOString() : null; } catch (e) { return null; }
}

function collectFolders(Notes) {
  // Walk every account's folder tree. account.folders() may or may not include
  // nested folders depending on macOS version, so keep the deepest path per id.
  const byId = {};
  function walk(account, folder, prefix) {
    let name, id;
    try { name = folder.name(); id = folder.id(); } catch (e) { return; }
    const path = prefix ? prefix + '/' + name : name;
    const prev = byId[id];
    if (!prev || path.split('/').length > prev.path.split('/').length) {
      byId[id] = { account: account, path: path, folder: folder };
    }
    let subs = [];
    try { subs = folder.folders(); } catch (e) {}
    subs.forEach(function (s) { walk(account, s, path); });
  }
  Notes.accounts().forEach(function (acc) {
    const accName = acc.name();
    acc.folders().forEach(function (f) { walk(accName, f, ''); });
  });
  return Object.keys(byId).map(function (k) { return byId[k]; })
    .filter(function (f) { return f.path !== 'Recently Deleted'; });
}

function findFolders(Notes, account, path, includeSub) {
  return collectFolders(Notes).filter(function (f) {
    if (account && f.account !== account) return false;
    return f.path === path || (includeSub && f.path.indexOf(path + '/') === 0);
  });
}

function listNotes(Notes, req) {
  const folders = findFolders(Notes, req.account, req.folder, req.include_subfolders);
  if (folders.length === 0) throw new Error('Apple Notes folder not found: ' + req.folder);
  const out = [];
  folders.forEach(function (f) {
    const notes = f.folder.notes;
    // Bulk property reads: one Apple Event per property instead of per note.
    const ids = notes.id(), names = notes.name(), mods = notes.modificationDate(), created = notes.creationDate();
    for (let i = 0; i < ids.length; i++) {
      out.push({ id: ids[i], title: names[i], folder: f.path, modified_at: iso(mods[i]), created_at: iso(created[i]) });
    }
  });
  return out;
}

function exportAttachment(a, dir, idx) {
  const info = { id: '', name: '', content_type: '' };
  try { info.id = a.id(); } catch (e) {}
  try { info.name = a.name() || ('attachment-' + idx); } catch (e) { info.name = 'attachment-' + idx; }
  try { const u = a.url(); if (u) info.url = String(u); } catch (e) {}
  if (!dir) return info;
  const safe = info.name.replace(/[\/:]/g, '_');
  const dest = dir + '/' + idx + '-' + safe;
  try {
    a.save({ in: Path(dest) });
    const data = $.NSData.dataWithContentsOfFile(dest);
    if (data && data.length > 0) info.data_b64 = ObjC.unwrap(data.base64EncodedStringWithOptions(0));
    $.NSFileManager.defaultManager.removeItemAtPathError(dest, null);
  } catch (e) {
    info.error = 'could not export attachment: ' + e.message;
  }
  return info;
}

function fetchNotes(Notes, req) {
  return req.ids.map(function (id) {
    const n = Notes.notes.byId(id);
    const rec = { id: id, body_format: 'html', attachments: [] };
    try {
      rec.title = n.name();
      rec.modified_at = iso(n.modificationDate());
      rec.created_at = iso(n.creationDate());
      try { rec.folder = n.container().name(); } catch (e) { rec.folder = ''; }
      rec.locked = !!n.passwordProtected();
      if (rec.locked) { rec.body = ''; return rec; }
      rec.body = n.body();
      if (req.attachments) {
        let atts = [];
        try { atts = n.attachments(); } catch (e) {}
        atts.forEach(function (a, i) { rec.attachments.push(exportAttachment(a, req.attachment_dir, i)); });
      }
    } catch (e) {
      rec.error = String(e.message || e);
    }
    return rec;
  });
}

function run(argv) {
  const req = JSON.parse(argv[0]);
  const Notes = Application('Notes');
  let result;
  switch (req.op) {
    case 'ping':
      result = { ok: true, accounts: Notes.accounts().map(function (a) { return a.name(); }) };
      break;
    case 'folders':
      result = collectFolders(Notes).map(function (f) {
        let count = null;
        try { count = f.folder.notes.length; } catch (e) {}
        return { account: f.account, path: f.path, note_count: count };
      });
      break;
    case 'list':
      result = listNotes(Notes, req);
      break;
    case 'fetch':
      result = fetchNotes(Notes, req);
      break;
    default:
      throw new Error('unknown op ' + req.op);
  }
  return JSON.stringify(result);
}
