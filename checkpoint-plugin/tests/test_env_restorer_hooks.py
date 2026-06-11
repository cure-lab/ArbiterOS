import json
from pathlib import Path

from checkpoint_plugin.env.restorer import _restore_blob_to
from checkpoint_plugin.store import CheckpointStore


def test_restore_blob_skips_path_rewrite_for_plugin_hooks(tmp_path):
    store = CheckpointStore(tmp_path / "session")
    python = tmp_path / "venv" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hooks = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{python} -m checkpoint_plugin.integrations.codex_hook turn_end",
                        }
                    ]
                }
            ]
        }
    }
    blob = (json.dumps(hooks, indent=2) + "\n").encode("utf-8")
    sha = store.store_blob(blob)
    dest = tmp_path / "runtime" / "codex" / "hooks.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    path_map = {str(python.parent): str(tmp_path / "external" / "venv" / "bin")}

    _restore_blob_to(
        sha,
        dest,
        store,
        tmp_path / "backup",
        [],
        preserve_plugin_hooks=True,
        path_map=path_map,
    )

    restored = json.loads(dest.read_text(encoding="utf-8"))
    command = restored["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert str(python) in command
    assert "/external/" not in command
