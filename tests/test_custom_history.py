# 自由選択の「最近の作図」を、保存した画像ファイル（output/custom/）から作る仕組みのテスト。一時フォルダだけを使う
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import custom
import disk_guard as dg


@pytest.fixture
def cdir(tmp_path, monkeypatch):
    d = tmp_path / "output" / "custom"
    d.mkdir(parents=True)
    monkeypatch.setattr(custom, "CUSTOM_DIR", d)
    monkeypatch.setattr(dg, "ROOT", tmp_path)
    monkeypatch.setattr(dg, "LIMITS_FILE", tmp_path / "disk_limits.json")
    return d


def png(d, name, age, meta=None):
    """age 秒前に更新された画像を作る。meta があれば隣に .json も置く。"""
    p = d / name
    p.write_bytes(b"x" * 100)
    t = 1_700_000_000 - age
    os.utime(p, (t, t))
    if meta is not None:
        (d / name).with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return p


def meta(label, ft=0):
    return dict(kind="emagram", mk="gfs", ik="2026111018", ft=ft, label=label, model="GFS", initText="2026-11-10 18Z", params={"lat": 1})


def test_list_is_newest_first_and_limited_to_10(cdir):
    for i in range(12):
        png(cdir, f"emagram_gfs_2026111018_{i:03d}_{i:012x}.png", age=1000 - i * 10, meta=meta(f"地点{i}", ft=i))
    items = custom.list_recent()
    assert len(items) == 10
    assert [x["label"] for x in items][:3] == ["地点11", "地点10", "地点9"]      # 新しいものが先頭
    assert items[-1]["label"] == "地点2"                                           # 古い2件は一覧から外れる
    assert items[0]["url"] == "output/custom/emagram_gfs_2026111018_011_00000000000b.png"
    assert "params" not in items[0]                                                # 画面に不要な条件の詳細は返さない


def test_legacy_images_without_json_are_listed_from_filename(cdir):
    png(cdir, "xsection_gfs_2026111018_012_0123456789ab.png", age=100)            # .json の無い（以前に作った）画像
    items = custom.list_recent()
    assert len(items) == 1
    assert items[0]["kind"] == "xsection" and items[0]["mk"] == "gfs" and items[0]["ft"] == 12
    assert "条件は不明" in items[0]["label"] and items[0]["initText"] == "2026-11-10 18Z"


def test_ignores_temp_orphan_json_and_unknown_files(cdir):
    png(cdir, "emagram_gfs_2026111018_000_0123456789ab.tmp.png", age=10, meta=None)   # 作図途中の一時ファイル
    (cdir / "orphan.json").write_text("{}", encoding="utf-8")                          # 画像が無い .json
    png(cdir, "memo.png", age=10)                                                      # 名前の形式が違う画像
    assert custom.list_recent() == []


def test_reused_image_moves_to_front(cdir):
    a = png(cdir, "emagram_gfs_2026111018_000_aaaaaaaaaaaa.png", age=500, meta=meta("A"))
    png(cdir, "emagram_gfs_2026111018_000_bbbbbbbbbbbb.png", age=100, meta=meta("B"))
    assert [x["label"] for x in custom.list_recent()] == ["B", "A"]
    os.utime(a, None)                                                                  # 使った（render が更新時刻を現在にする）
    assert [x["label"] for x in custom.list_recent()] == ["A", "B"]


def test_describe_and_meta_roundtrip(cdir):
    p = dict(kind="xsection", model="gfs", init="2026111018", ft=12, lat1=40.0, lon1=125.0, lat2=30.0, lon2=145.0)
    assert custom.describe(p) == "断面 (40N,125E)→(30N,145E)"
    out = cdir / "xsection_gfs_2026111018_012_0123456789ab.png"
    out.write_bytes(b"x")
    custom.write_meta(out, p)
    m = json.loads(custom.meta_path(out).read_text(encoding="utf-8"))
    assert m["label"] == "断面 (40N,125E)→(30N,145E)" and m["initText"] == "2026-11-10 18Z" and m["params"]["lat2"] == 30.0
    assert custom.list_recent()[0]["label"] == "断面 (40N,125E)→(30N,145E)"


def test_disk_prune_removes_sidecar_json_too(cdir):
    old = png(cdir, "emagram_gfs_2026111018_000_aaaaaaaaaaaa.png", age=500, meta=meta("A"))
    new = png(cdir, "emagram_gfs_2026111018_000_bbbbbbbbbbbb.png", age=100, meta=meta("B"))
    (cdir.parent.parent / "disk_limits.json").write_text(json.dumps({"custom_mb": 150 / dg.MB}), encoding="utf-8")
    dg.prune_group("custom")
    assert not old.exists() and not old.with_suffix(".json").exists()                  # 古い方は画像と条件ファイルの両方が消える
    assert new.exists() and new.with_suffix(".json").exists()
