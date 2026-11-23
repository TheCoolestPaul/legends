import sys
from pathlib import Path

import pytest

# The repair modules import each other as top-level modules.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "repair"))


@pytest.fixture(autouse=True)
def snapshot_dir(tmp_path, monkeypatch):
    """Send debug snapshots to a temp folder, never the real debug_frames/."""
    import snapshots
    monkeypatch.setattr(snapshots, "SNAPSHOT_DIR", tmp_path / "debug_frames")
    monkeypatch.setattr(snapshots, "recording", False)
    snapshots.reset()
    yield tmp_path / "debug_frames"
    snapshots.reset()
