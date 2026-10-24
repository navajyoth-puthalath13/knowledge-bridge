"""Source preservation: the mirror is a faithful, untouched copy of each note."""
import re

import pytest

from app.connectors.html_to_md import notes_html_to_markdown
from app.obsidian.mirror import split_frontmatter
from app.obsidian.writer import ScopedWriter, WriteOutsideRootError, sha256_bytes

from .conftest import LOCKED, RICH


def test_notes_html_conversion_preserves_text_order_and_formatting():
    html = ("<div><h1>Title</h1></div><div>First <b>bold</b> line</div><div><br></div>"
            "<ul><li>a</li><li>b</li></ul><div>Then <i>italic</i>, <u>under</u>, <strike>gone</strike>, "
            "<tt>code</tt> and <a href=\"https://x.y\">link</a></div>"
            "<table><tr><td>h1</td><td>h2</td></tr><tr><td>c1</td><td>c2</td></tr></table><div>End</div>")
    md = notes_html_to_markdown(html).markdown
    assert md.splitlines()[0] == "# Title"
    for s in ["First **bold** line", "- a", "- b", "*italic*", "<u>under</u>", "~~gone~~", "`code`",
              "[link](https://x.y)", "| h1 | h2 |", "| c1 | c2 |", "End"]:
        assert s in md, s
    order = [md.index(s) for s in ["# Title", "First", "- a", "Then", "| h1", "End"]]
    assert order == sorted(order)


def test_initial_sync_mirrors_every_readable_note(env):
    rep = env.sync()
    assert rep.status == "ok", rep.errors
    assert rep.stats.created == 6 and rep.stats.locked == 1
    titles = sorted(p.stem for p in (env.vault / "Apple Notes").glob("*.md"))
    assert "Private" not in titles  # password-protected note is never mirrored
    assert len(titles) == 6
    assert env.db.scalar("SELECT status FROM notes WHERE id=?", (LOCKED,)) == "locked"


def test_mirror_body_has_no_generated_content(env):
    env.sync()
    for p in (env.vault / "Apple Notes").glob("*.md"):
        fm, body = split_frontmatter(p.read_text("utf-8"))
        assert "kb_mirror: true" in fm and "apple_notes_id" in fm
        # the only wiki-links allowed in a mirror body are embeds of the note's own images
        assert all(l.startswith("Apple Notes/_attachments/") for l in re.findall(r"!\[\[([^\]]+)\]\]", body))
        assert not re.search(r"(?<!!)\[\[", body), f"generated link inserted into {p.name}"
        assert "Knowledge Bridge" not in body


def test_mirror_text_matches_source_exactly(env):
    env.sync()
    body = split_frontmatter(env.mirror("Design Days 2026 — Day 2").read_text("utf-8"))[1]
    assert body == ("# Design Days 2026 — Day 2\n\nGreat talks on design systems in the morning.\n"
                    "Over lunch someone at Design Days recommended Finding Flow when we talked about burnout.\n\n"
                    "- Follow up on the accessibility workshop\n- Try the critique format from the afternoon session\n")


def test_rich_text_note_and_attachments(env):
    rep = env.sync()
    text = env.mirror("Workshop notes — rich text").read_text("utf-8")
    assert "| Session | Speaker |" in text and "1. Warm-up" in text and "~~struck~~" in text
    imgs = re.findall(r"!\[\[([^\]]+\.png)\]\]", text)
    assert len(imgs) == 1 and (env.vault / imgs[0]).exists()
    assert '"name": "slides.pdf", "file": ' in text
    assert any("scan.heic" in e["message"] for e in rep.errors if e["kind"] == "warning")


def test_checksums_recorded_and_match(env):
    env.sync()
    for r in env.db.q("SELECT mirror_path, mirror_hash FROM notes WHERE mirror_path IS NOT NULL"):
        assert sha256_bytes((env.vault / r["mirror_path"]).read_bytes()) == r["mirror_hash"]


def test_intelligence_and_rebuild_never_change_mirror(env):
    from app.obsidian.render import render_all

    env.sync()
    before = env.mirror_hashes()
    render_all(env.db, env.cfg)
    env.sync(reprocess=[RICH])
    assert env.mirror_hashes() == before


def test_scoped_writer_refuses_escape(tmp_path):
    root = tmp_path / "gen"
    root.mkdir()
    (tmp_path / "mirror").mkdir()
    w = ScopedWriter(root)
    w.write_text("ok/page.md", "x")
    with pytest.raises(WriteOutsideRootError):
        w.write_text("../mirror/note.md", "evil")
    with pytest.raises(WriteOutsideRootError):
        w.write_text(tmp_path / "mirror" / "note.md", "evil")
    (root / "link").symlink_to(tmp_path / "mirror")
    with pytest.raises(WriteOutsideRootError):
        w.write_text("link/note.md", "evil")
    assert not (tmp_path / "mirror" / "note.md").exists()


def test_config_rejects_nested_output_folders(tmp_path):
    from app.config import AppConfig, SourceConfig

    cfg = AppConfig(vault_path=str(tmp_path), mirror_dir="Notes", generated_dir="Notes/KB",
                    source=SourceConfig(kind="folder", folder_path=str(tmp_path)))
    assert any("must not contain" in p for p in cfg.problems())
    with pytest.raises(ValueError):
        AppConfig(mirror_dir="../outside")


def test_validation_tool_report(tmp_path):
    from app.connectors.folder import FolderConnector
    from app.tools.validate_apple_notes import validate

    from .conftest import FIXTURES

    ok, msg = validate(FolderConnector(FIXTURES / "notes"), tmp_path / "report")
    report = (tmp_path / "report" / "report.md").read_text("utf-8")
    assert ok and "Note ids stable between listings: yes" in report
    assert "tables" in report and "inline images (data URI)" in report and "1/2 exported" in report
    assert (tmp_path / "report" / "Workshop notes — rich text.mirror.md").exists()


def test_apple_notes_connector_reports_non_macos_cleanly():
    import sys

    from app.connectors.apple_notes import AppleNotesConnector

    if sys.platform == "darwin":
        pytest.skip("covered by the macOS integration tests")
    ok, msg = AppleNotesConnector("", "Knowledge").health()
    assert not ok and "only available on macOS" in msg
