"""Actual process death and normal failure leave bounded, recoverable temp intent."""

import os
import subprocess
import sys
import time
from pathlib import Path

from ailab_platform.config import settings
from ailab_platform.owned_temporary import directory, index, root, sweep


def test_normal_exit_removes_bytes_and_intent_before_return(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with directory(prefix="test-job") as temporary:
        target = Path(temporary)
        (target / "pixels").write_bytes(b"temporary image")
        with index("worker") as connection:
            assert connection.execute("SELECT count(*) FROM leases").fetchone()[0] == 1
    assert not target.exists()
    with index("worker") as connection:
        assert connection.execute("SELECT count(*) FROM leases").fetchone()[0] == 0


def test_killed_owner_leaves_indexed_compensation_and_does_not_remove_a_live_job(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    code = (
        "import os,time; from pathlib import Path; "
        "from ailab_platform.owned_temporary import directory; "
        "context=directory(deadline=time.time()+1); path=Path(context.__enter__()); "
        "(path/'interrupted').write_bytes(b'partial'); os._exit(9)"
    )
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "AILAB_DATA_DIR": str(tmp_path)}, timeout=10)
    assert result.returncode == 9
    with index("worker") as connection:
        token, due = connection.execute("SELECT token,deadline FROM leases").fetchone()
    orphan = root("worker") / token
    assert orphan.exists()
    with directory(deadline=due + 100) as live:
        assert sweep(now=due - 1) == 0
        assert sweep(now=due + 1) == 1
        assert not orphan.exists() and Path(live).exists()


def test_failed_normal_cleanup_does_not_receive_crash_grace(tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    real = shutil.rmtree
    with monkeypatch.context() as failure:
        failure.setattr(shutil, "rmtree", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk unavailable")))
        with directory(deadline=time.time() + 600) as temporary:
            target = Path(temporary)
            (target / "partial").write_bytes(b"bytes")
    assert target.exists()
    assert sweep(now=time.time() + 1) == 1
    assert not target.exists()
    assert real is shutil.rmtree
