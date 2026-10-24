from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import __version__
from ..config import CLOUD_PROVIDERS, AppConfig
from ..connectors import ConnectorError
from ..entities.types import COLLECTION_TYPE, ENTITY_TYPES, NOTE_TYPE
from ..intelligence import registry as reg
from ..intelligence.normalize import norm
from ..intelligence.reviews import describe, resolve_review
from ..obsidian.render import render_all
from ..security.secrets import set_secret
from ..service import Service
from ..storage.db import now

router = APIRouter()


def svc(request: Request) -> Service:
    return request.app.state.service


def _render(s: Service) -> None:
    if not s.cfg.problems():
        render_all(s.db, s.cfg)


# ---------------------------------------------------------------- basics
@router.get("/health")
def health():
    return {"ok": True, "version": __version__}


@router.get("/status")
def status(request: Request):
    return svc(request).status()


@router.get("/config")
def get_config(request: Request):
    return svc(request).cfg.model_dump()


@router.put("/config")
def put_config(cfg: AppConfig, request: Request):
    created = svc(request).update_config(cfg)
    return {"ok": True, "problems": cfg.problems(), "created_folders": created}


class SecretIn(BaseModel):
    value: str | None = Field(default=None, max_length=500)


@router.put("/secrets/{provider}")
def put_secret(provider: str, body: SecretIn, request: Request):
    if provider not in CLOUD_PROVIDERS:
        raise HTTPException(400, "unknown provider")
    set_secret(provider, (body.value or "").strip() or None)
    return svc(request).provider_status()


@router.post("/provider/test")
def provider_test(request: Request):
    ok, msg = svc(request).test_provider()
    return {"ok": ok, "message": msg}


@router.get("/provider/models")
def provider_models(request: Request):
    from ..providers.base import ProviderError

    try:
        return {"models": svc(request).list_models()}
    except ProviderError as e:
        raise HTTPException(502, str(e))


@router.get("/obsidian/vaults")
def obsidian_vaults():
    from ..obsidian.vaults import known_vaults

    return {"vaults": known_vaults()}


@router.get("/source/folders")
def source_folders(request: Request):
    try:
        return svc(request).list_source_folders()
    except ConnectorError as e:
        raise HTTPException(502, str(e))


# ---------------------------------------------------------------- sync
class SyncIn(BaseModel):
    dry_run: bool = False


@router.post("/sync")
def sync(body: SyncIn, request: Request):
    s = svc(request)
    if s.cfg.problems():
        raise HTTPException(400, "; ".join(s.cfg.problems()))
    if not s.start_sync("manual", dry_run=body.dry_run):
        raise HTTPException(409, "a sync is already running")
    return {"started": True}


@router.post("/sync/cancel")
def cancel(request: Request):
    svc(request).cancel.set()
    return {"ok": True}


class AutomationIn(BaseModel):
    enabled: bool


@router.post("/automation")
def automation(body: AutomationIn, request: Request):
    svc(request).set_automation(body.enabled)
    return svc(request).status()["automation"]


@router.post("/rebuild")
def rebuild(request: Request):
    s = svc(request)
    if s.cfg.problems():
        raise HTTPException(400, "; ".join(s.cfg.problems()))
    try:
        return s.rebuild()
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.get("/runs")
def runs(request: Request, limit: int = 50):
    rows = svc(request).db.q("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (min(limit, 200),))
    return [{**dict(r), "stats": json.loads(r["stats"] or "{}"), "errors": json.loads(r["errors"] or "[]"),
             "plan": None} for r in rows]


# ---------------------------------------------------------------- notes
@router.get("/notes")
def notes(request: Request):
    return [dict(r) for r in svc(request).db.q(
        "SELECT id, title, folder, status, category, mirror_path, modified_at, processed_at, last_error "
        "FROM notes ORDER BY title COLLATE NOCASE")]


class ReprocessIn(BaseModel):
    note_ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/notes/reprocess")
def reprocess(body: ReprocessIn, request: Request):
    s = svc(request)
    if not s.start_sync("reprocess", reprocess=body.note_ids, mirror=False):
        raise HTTPException(409, "a sync is already running")
    return {"started": True}


@router.post("/notes/{note_id:path}/reattach")
def reattach(note_id: str, request: Request):
    try:
        svc(request).reattach(note_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


# ---------------------------------------------------------------- entities
@router.get("/entity-types")
def entity_types(request: Request):
    ov = svc(request).cfg.intelligence.enabled_types
    return [{"key": t.key, "label": t.label, "collection": t.collection, "parent_type": t.parent_type,
             "description": t.description, "enabled": ov.get(t.key, t.enabled_by_default)}
            for t in ENTITY_TYPES.values()]


class TypeToggle(BaseModel):
    enabled: bool


@router.put("/entity-types/{key}")
def toggle_type(key: str, body: TypeToggle, request: Request):
    s = svc(request)
    if key not in ENTITY_TYPES:
        raise HTTPException(404, "unknown type")
    cfg = s.cfg.model_copy(deep=True)
    cfg.intelligence.enabled_types[key] = body.enabled
    s.update_config(cfg)
    return {"ok": True}


@router.get("/entities")
def entities(request: Request, type: str | None = None, q: str | None = None):
    s = svc(request)
    sql = ("SELECT e.id, e.type, e.name, e.page_path, e.created_at, "
           "(SELECT COUNT(*) FROM relationships r WHERE r.tgt_id=e.id AND r.type IN ('mentions','about') "
           " AND r.status='accepted') AS mentions, "
           "(SELECT GROUP_CONCAT(alias, ' | ') FROM aliases a WHERE a.entity_id=e.id AND a.source!='canonical') AS aliases "
           "FROM entities e WHERE e.status='active' AND e.type NOT IN (?,?)")
    args: list = [NOTE_TYPE, COLLECTION_TYPE]
    if type:
        sql += " AND e.type=?"
        args.append(type)
    if q:
        sql += " AND (e.norm LIKE ? OR EXISTS (SELECT 1 FROM aliases a WHERE a.entity_id=e.id AND a.norm LIKE ?))"
        args += [f"%{norm(q)}%"] * 2
    return [dict(r) for r in s.db.q(sql + " ORDER BY e.type, e.name COLLATE NOCASE", args)]


@router.get("/entities/{entity_id}")
def entity(entity_id: str, request: Request):
    db = svc(request).db
    e = db.one("SELECT * FROM entities WHERE id=?", (entity_id,))
    if not e:
        raise HTTPException(404, "not found")
    rels = db.q("SELECT r.*, s.name AS src_name, s.type AS src_type, t.name AS tgt_name, t.type AS tgt_type, "
                "n.title AS note_title FROM relationships r JOIN entities s ON s.id=r.src_id "
                "JOIN entities t ON t.id=r.tgt_id LEFT JOIN notes n ON n.id=r.note_id "
                "WHERE r.src_id=? OR r.tgt_id=? ORDER BY r.type, r.id", (entity_id, entity_id))
    return {"entity": dict(e),
            "aliases": [dict(a) for a in db.q("SELECT alias, source FROM aliases WHERE entity_id=?", (entity_id,))],
            "relationships": [dict(r) for r in rels]}


class EntityPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    add_alias: str | None = Field(default=None, min_length=1, max_length=200)
    remove_alias: str | None = None


@router.patch("/entities/{entity_id}")
def patch_entity(entity_id: str, body: EntityPatch, request: Request):
    s = svc(request)
    e = s.db.one("SELECT * FROM entities WHERE id=? AND status='active'", (entity_id,))
    if not e or e["type"] not in ENTITY_TYPES:
        raise HTTPException(404, "not found")
    with s.db.tx() as c:
        if body.name and body.name != e["name"]:
            c.execute("UPDATE entities SET name=?, norm=?, updated_at=? WHERE id=?", (body.name, norm(body.name), now(), entity_id))
            c.execute("UPDATE aliases SET source='user' WHERE entity_id=? AND source='canonical'", (entity_id,))
            reg.add_alias(s.db, entity_id, body.name, "canonical")
        if body.add_alias:
            reg.add_alias(s.db, entity_id, body.add_alias, "user")
        if body.remove_alias:
            c.execute("DELETE FROM aliases WHERE entity_id=? AND alias=? AND source!='canonical'", (entity_id, body.remove_alias))
    _render(s)
    return {"ok": True}


class MergeIn(BaseModel):
    into: str


@router.post("/entities/{entity_id}/merge")
def merge(entity_id: str, body: MergeIn, request: Request):
    s = svc(request)
    try:
        reg.merge_entities(s.db, entity_id, body.into)
        reg.refresh_structure(s.db)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _render(s)
    return {"ok": True}


@router.get("/relationships")
def relationships(request: Request, status: str | None = None, type: str | None = None, limit: int = 500):
    sql = ("SELECT r.*, s.name AS src_name, s.type AS src_type, t.name AS tgt_name, t.type AS tgt_type, "
           "n.title AS note_title FROM relationships r JOIN entities s ON s.id=r.src_id JOIN entities t ON t.id=r.tgt_id "
           "LEFT JOIN notes n ON n.id=r.note_id WHERE 1=1")
    args: list = []
    if status:
        sql += " AND r.status=?"
        args.append(status)
    if type:
        sql += " AND r.type=?"
        args.append(type)
    return [dict(r) for r in svc(request).db.q(sql + " ORDER BY r.updated_at DESC LIMIT ?", (*args, min(limit, 2000)))]


# ---------------------------------------------------------------- reviews & conflicts
@router.get("/reviews")
def reviews(request: Request, status: str = "pending"):
    db = svc(request).db
    return [describe(db, r) for r in db.q("SELECT * FROM reviews WHERE status=? ORDER BY id", (status,))]


class ResolveIn(BaseModel):
    action: str
    target_id: str | None = None


@router.post("/reviews/{review_id}/resolve")
def resolve(review_id: int, body: ResolveIn, request: Request):
    s = svc(request)
    try:
        res = resolve_review(s.db, s.cfg, review_id, body.action, body.target_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    _render(s)
    return res


@router.get("/conflicts")
def conflicts(request: Request, status: str = "open"):
    s = svc(request)
    out = []
    for r in s.db.q("SELECT c.*, n.title FROM conflicts c JOIN notes n ON n.id=c.note_id WHERE c.status=? ORDER BY c.id",
                    (status,)):
        d = dict(r)
        p = s.cfg.vault() / r["mirror_path"] if s.cfg.vault() else None
        d["obsidian_text"] = p.read_text("utf-8", errors="replace") if p and p.exists() else None
        out.append(d)
    return out


class ConflictIn(BaseModel):
    action: str


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, body: ConflictIn, request: Request):
    try:
        svc(request).resolve_conflict(conflict_id, body.action)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.get("/diagnostics")
def diagnostics(request: Request):
    return svc(request).diagnostics()
