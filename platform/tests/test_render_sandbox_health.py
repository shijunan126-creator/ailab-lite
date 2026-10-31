import pytest

from ailab_platform.sandbox_tools import render_sandbox_health as health


def test_cleanup_removes_job_files_without_following_symlinks(tmp_path, monkeypatch):
    work, temp, outside = tmp_path / "workspace", tmp_path / "tmp", tmp_path / "outside"
    (work / "inputs").mkdir(parents=True)
    (work / ".ailab").mkdir()
    temp.mkdir()
    outside.mkdir()
    (outside / "keep").write_text("outside")
    (work / "inputs" / "private").write_text("input")
    (work / ".ailab" / "result").write_text("output")
    (work / ".ailab" / "uno_convert.py").write_text("tool")
    (temp / "job").symlink_to(outside, target_is_directory=True)
    (temp / "lo-profile").mkdir()
    monkeypatch.setattr(health, "WORK", work)
    monkeypatch.setattr(health, "TEMP", temp)
    monkeypatch.setattr(health, "listener_rss", lambda: 10)
    monkeypatch.setattr(health, "used_tmp", lambda: 20)
    assert health.cleanup(20, 100) == {"rss": 10, "tmp_used": 20}
    assert list((work / "inputs").iterdir()) == []
    assert sorted(p.name for p in (work / ".ailab").iterdir()) == ["uno_convert.py"]
    assert (outside / "keep").read_text() == "outside" and not (temp / "job").exists()


@pytest.mark.parametrize("rss,used", [(100, 20), (10, 21 + health.PROFILE_GROWTH)])
def test_cleanup_rejects_unhealthy_listener_or_tmpfs(tmp_path, monkeypatch, rss, used):
    monkeypatch.setattr(health, "WORK", tmp_path / "work")
    monkeypatch.setattr(health, "TEMP", tmp_path / "tmp")
    monkeypatch.setattr(health, "listener_rss", lambda: rss)
    monkeypatch.setattr(health, "used_tmp", lambda: used)
    with pytest.raises(RuntimeError, match="health_limit"):
        health.cleanup(20, 100)


def test_cleanup_accepts_small_profile_growth(tmp_path, monkeypatch):
    # A 15-slide deck left the profile 8 KiB larger; that must not destroy the user's warm sandbox.
    monkeypatch.setattr(health, "WORK", tmp_path / "work")
    monkeypatch.setattr(health, "TEMP", tmp_path / "tmp")
    monkeypatch.setattr(health, "listener_rss", lambda: 10)
    monkeypatch.setattr(health, "used_tmp", lambda: 20 + 8192)
    assert health.cleanup(20, 100)["tmp_used"] == 20 + 8192
