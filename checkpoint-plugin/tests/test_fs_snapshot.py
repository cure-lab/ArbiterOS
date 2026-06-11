from checkpoint_plugin.fs.ignore import IgnoreMatcher
from checkpoint_plugin.fs.snapshot import snapshot_cwd
from checkpoint_plugin.store import CheckpointStore


def test_snapshot_respects_ignore_and_secret_denylist(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "keep.txt").write_text("keep", encoding="utf-8")
    (cwd / ".env").write_text("secret", encoding="utf-8")
    (cwd / "node_modules").mkdir()
    (cwd / "node_modules" / "pkg.js").write_text("skip", encoding="utf-8")
    (cwd / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (cwd / "ignored.txt").write_text("ignored", encoding="utf-8")

    store = CheckpointStore(tmp_path / "session")
    snapshot = snapshot_cwd(cwd, store, IgnoreMatcher(cwd))

    assert "keep.txt" in snapshot.files
    assert ".env" not in snapshot.files
    assert "node_modules/pkg.js" not in snapshot.files
    assert "ignored.txt" not in snapshot.files
