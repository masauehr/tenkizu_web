# ディスク使用量の制限・通知（disk_guard）のテスト。一時フォルダだけを使い、実際のデータには触れない
import json
import os
import sys
from collections import namedtuple
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import disk_guard as dg


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "ROOT", tmp_path)
    monkeypatch.setattr(dg, "LIMITS_FILE", tmp_path / "disk_limits.json")
    monkeypatch.setattr(dg, "STATE_FILE", tmp_path / "logs" / "disk_guard_state.json")
    return tmp_path


def make(root, rel, size, age):
    """size バイトのファイルを作り、更新時刻を age 秒前にする（大きいほど古い）。"""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = 1_700_000_000 - age
    os.utime(p, (t, t))
    return p


def set_limits(root, **kw):
    (root / "disk_limits.json").write_text(json.dumps(kw), encoding="utf-8")


def test_prune_deletes_oldest_first_and_keeps_newest(root):
    old = make(root, "data/gfs/2026111000/f000.grib2", 1000, 500)
    mid = make(root, "data/gfs/2026111006/f000.grib2", 1000, 300)
    new1 = make(root, "data/ecmwf/2026111012/f000.grib2", 1000, 100)
    new2 = make(root, "data/ecmwf/2026111018/f000.grib2", 1000, 10)
    set_limits(root, grib_gb=2500 / dg.GB)               # 4000バイトを 2500 以下にする → 古い2つを消す
    n, freed = dg.prune_group("grib")
    assert (n, freed) == (2, 2000)
    assert not old.exists() and not mid.exists()
    assert new1.exists() and new2.exists()
    assert not (root / "data/gfs/2026111000").exists()   # 空になった初期値のフォルダは消える
    assert (root / "data/ecmwf/2026111012").exists()


def test_prune_does_nothing_under_limit(root):
    make(root, "data/gfs/2026111000/f000.grib2", 1000, 500)
    set_limits(root, grib_gb=1.0)
    assert dg.prune_group("grib") == (0, 0)


def test_gsm_and_output_are_warning_only(root):
    f = make(root, "data/gsm/Z__C_RJTD_x.bin", 5000, 100)
    set_limits(root, gsm_gb=1000 / dg.GB)
    assert dg.prune_group("gsm") == (0, 0) and f.exists()    # GSM は作図に必要なので消さない
    rep = dg.report()
    assert rep["items"]["gsm"]["level"] == "over"
    assert any(k == "gsm" for k, _ in dg.messages(rep))


def test_custom_images_pruned_by_limit(root):
    a = make(root, "output/custom/a.png", 600, 300)
    b = make(root, "output/custom/b.png", 600, 100)
    set_limits(root, custom_mb=700 / dg.MB)
    dg.prune_group("custom")
    assert not a.exists() and b.exists()


def test_make_room_prunes_for_new_data_and_refuses_when_disk_nearly_full(root, monkeypatch):
    a = make(root, "data/gfs/2026111000/f000.grib2", 1000, 300)
    b = make(root, "data/gfs/2026111006/f000.grib2", 1000, 100)
    set_limits(root, grib_gb=2500 / dg.GB, free_min_gb=1.0)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(dg.shutil, "disk_usage", lambda p: Usage(100 * dg.GB, 10 * dg.GB, 50 * dg.GB))
    assert dg.make_room(1000) is True                        # 2000 + 1000 > 2500 → 古い方を消して余地を作る
    assert not a.exists() and b.exists()
    monkeypatch.setattr(dg.shutil, "disk_usage", lambda p: Usage(100 * dg.GB, 99 * dg.GB, int(0.5 * dg.GB)))
    assert dg.make_room(1000) is False                       # 空きが free_min 未満 → 取得を止める


def test_free_space_levels(root, monkeypatch):
    set_limits(root, free_warn_gb=20, free_min_gb=5)
    Usage = namedtuple("Usage", "total used free")
    for free_gb, level in ((50, "ok"), (10, "warn"), (2, "min")):
        monkeypatch.setattr(dg.shutil, "disk_usage", lambda p, f=free_gb: Usage(100 * dg.GB, 0, int(f * dg.GB)))
        assert dg.report()["free_level"] == level


def test_notify_once_per_day_per_kind(root, monkeypatch):
    sent = []
    monkeypatch.setattr(dg, "notify", lambda title, body: sent.append(body))
    make(root, "data/gsm/Z__C_RJTD_x.bin", 5000, 100)
    set_limits(root, gsm_gb=1000 / dg.GB, free_warn_gb=0, free_min_gb=0)
    dg.run(do_notify=True)
    assert len(sent) == 1 and "GSM" in sent[0]
    dg.run(do_notify=True)                                   # 同じ内容は24時間以内には再通知しない
    assert len(sent) == 1
    st = json.loads((root / "logs" / "disk_guard_state.json").read_text(encoding="utf-8"))
    st["gsm"] -= dg.NOTIFY_INTERVAL + 1                      # 24時間たったことにする
    (root / "logs" / "disk_guard_state.json").write_text(json.dumps(st), encoding="utf-8")
    dg.run(do_notify=True)
    assert len(sent) == 2


def test_prune_is_notified_and_limits_file_is_optional(root, monkeypatch):
    sent = []
    monkeypatch.setattr(dg, "notify", lambda title, body: sent.append(body))
    assert dg.limits()["grib_gb"] == dg.DEFAULTS["grib_gb"]   # disk_limits.json が無ければ既定値
    make(root, "data/gfs/2026111000/f000.grib2", 1000, 500)
    make(root, "data/gfs/2026111006/f000.grib2", 1000, 100)
    set_limits(root, grib_gb=1500 / dg.GB, free_warn_gb=0, free_min_gb=0)
    rep, msgs = dg.run(do_prune=True, do_notify=True)
    assert any("削除しました" in b for b in sent)
    assert rep["items"]["grib"]["level"] == "ok"
