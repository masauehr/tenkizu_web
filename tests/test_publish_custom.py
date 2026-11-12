# 自由選択の画像を公開サイトに載せる処理（publish.sync_custom）のテスト。一時フォルダだけを使い、git や公開サイトには触れない
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import custom
import publish


@pytest.fixture
def env(tmp_path, monkeypatch):
    src = tmp_path / "local" / "output" / "custom"
    src.mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(custom, "CUSTOM_DIR", src)
    return src, wt


def make(src, i, age, label=None):
    """条件ファイル付きの画像を作る。i が大きいほど新しい。"""
    name = f"emagram_gfs_2026111018_{i:03d}_{i:012x}.png"
    p = src / name
    p.write_bytes(b"png" + bytes([i]))
    t = 1_700_000_000 - age
    os.utime(p, (t, t))
    meta = dict(kind="emagram", mk="gfs", ik="2026111018", ft=i, label=label or f"地点{i}", model="GFS", initText="2026-11-10 18Z")
    p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return name


def public_list(wt):
    return json.loads((wt / "output" / "custom" / "list.json").read_text(encoding="utf-8"))["items"]


def test_publish_adds_to_front_and_copies_image(env):
    src, wt = env
    a, b = make(src, 1, 300), make(src, 2, 100)
    assert publish.sync_custom(wt, [a], []) == [a]
    assert publish.sync_custom(wt, [b], []) == [b, a]                    # 新しく載せたものが先頭
    assert (wt / "output" / "custom" / a).exists() and (wt / "output" / "custom" / b).exists()
    items = public_list(wt)
    assert [x["label"] for x in items] == ["地点2", "地点1"]
    assert all("published" not in x for x in items)                      # ローカル専用の項目は公開側に載せない
    assert items[0]["url"] == f"output/custom/{b}" and items[0]["mk"] == "gfs" and items[0]["ft"] == 2


def test_publish_keeps_only_newest_max_and_removes_dropped_images(env):
    src, wt = env
    n_max = publish.CUSTOM_MAX
    names = [make(src, i, 2000 - i) for i in range(n_max + 2)]
    for n in names:                                                       # 上限+2回載せる → 古い2件は公開側から消える
        publish.sync_custom(wt, [n], [])
    got = publish.sync_custom(wt, [], [])
    assert n_max == 20 and len(got) == n_max and got[0] == names[-1] and names[0] not in got and names[1] not in got
    pngs = sorted(p.name for p in (wt / "output" / "custom").glob("*.png"))
    assert pngs == sorted(got)                                            # 一覧と公開側の画像ファイルが一致


def test_republish_same_image_moves_to_front_without_duplicate(env):
    src, wt = env
    a, b = make(src, 1, 300), make(src, 2, 100)
    publish.sync_custom(wt, [a], [])
    publish.sync_custom(wt, [b], [])
    assert publish.sync_custom(wt, [a], []) == [a, b]


def test_unpublish_removes_from_list_and_deletes_file(env):
    src, wt = env
    a, b = make(src, 1, 300), make(src, 2, 100)
    publish.sync_custom(wt, [a, b], [])
    assert publish.sync_custom(wt, [], [a]) == [b]
    assert not (wt / "output" / "custom" / a).exists()
    assert [x["label"] for x in public_list(wt)] == ["地点2"]


def test_refuses_unknown_and_legacy_images(env):
    src, wt = env
    with pytest.raises(RuntimeError):
        publish.sync_custom(wt, ["emagram_gfs_2026111018_099_0123456789ab.png"], [])      # 手元に無い
    legacy = src / "xsection_gfs_2026111018_012_0123456789ab.png"                         # 条件ファイルの無い古い画像
    legacy.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        publish.sync_custom(wt, [legacy.name], [])


def test_other_public_files_are_untouched(env):
    src, wt = env
    (wt / "output" / "ecmwf" / "2026111018").mkdir(parents=True)
    keep = wt / "output" / "ecmwf" / "2026111018" / "x.png"
    keep.write_bytes(b"model image")
    (wt / "index.html").write_text("<html>", encoding="utf-8")
    publish.sync_custom(wt, [make(src, 1, 100)], [])
    assert keep.read_bytes() == b"model image" and (wt / "index.html").read_text(encoding="utf-8") == "<html>"


def test_record_published_marks_list_as_published(env, monkeypatch, tmp_path):
    src, wt = env
    a, b = make(src, 1, 300), make(src, 2, 100)
    monkeypatch.setattr(publish, "ROOT", src.parent.parent)               # tmp/local
    publish.record_published([a])
    flags = {Path(x["url"]).name: x["published"] for x in custom.list_recent()}
    assert flags == {a: True, b: False}


def test_cli_validates_image_names(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["publish.py", "--custom", "../etc/passwd"])
    with pytest.raises(SystemExit):
        publish.parse_args()
    monkeypatch.setattr(sys, "argv", ["publish.py", "--custom", "emagram_gfs_2026111018_000_0123456789ab.png"])
    assert publish.parse_args().custom == ["emagram_gfs_2026111018_000_0123456789ab.png"]


def test_local_publish_item_has_same_shape_as_api(env):
    """ローカルからの公開も、同じ形: ts＝作図した時刻（条件ファイルの created）・pub＝公開した時刻（登録日時）。
    ファイルの最終使用時刻（再利用のたびに変わる）は ts に使わない。"""
    import time
    src, wt = env
    name = make(src, 1, 10)                                              # 画像の更新時刻は 1_700_000_000 付近
    meta_p = (src / name).with_suffix(".json")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["created"] = 1_650_000_000.0                                    # 作図したのは、ずっと前
    meta_p.write_text(json.dumps(meta), encoding="utf-8")
    t0 = time.time()
    publish.sync_custom(wt, [name], [])
    item = public_list(wt)[0]
    assert item["ts"] == 1_650_000_000.0 and "created" not in item and "published" not in item
    assert t0 - 1 <= item["pub"] <= time.time() + 1                      # 登録日時＝公開した時刻
    assert {"kind", "mk", "ik", "ft", "label", "model", "initText", "url", "ts", "pub"} <= set(item)


def test_old_items_keep_their_registration_time_and_publisher(env):
    """すでに公開されている項目（API 経由の pub・by つき）は、ローカルの公開で壊れず、そのまま残る。"""
    src, wt = env
    d = wt / "output" / "custom"
    d.mkdir(parents=True)
    legacy = {"kind": "xsection", "mk": "gfs", "ik": "2026111012", "ft": 12, "label": "断面 x", "model": "GFS", "initText": "x",
              "url": "output/custom/xsection_gfs_2026111012_012_0123456789ab.png", "ts": 1.0, "pub": 2.0, "by": "tanaka"}
    (d / "list.json").write_text(json.dumps({"items": [legacy]}), encoding="utf-8")
    (d / "xsection_gfs_2026111012_012_0123456789ab.png").write_bytes(b"png")
    publish.sync_custom(wt, [make(src, 5, 10)], [])
    items = public_list(wt)
    assert items[0]["ft"] == 5 and items[1] == legacy
