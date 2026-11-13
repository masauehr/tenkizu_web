# 断面図の表示要素（色塗り・等値線・風矢羽）のテスト。合成データで実際に描画して、全ての組み合わせが描けることを確認する
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import custom
import xsection as xs


def test_defaults_keep_the_original_look():
    o = xs.normalize_opts()
    assert o == {"shade": "div", "contours": ("theta", "ept", "wind"), "barbs": "front", "ptop": 100}   # 指定が無ければ従来の見た目（高度は全層）


def test_normalize_orders_contours_and_validates():
    assert xs.normalize_opts({"contours": ["ept", "theta"]})["contours"] == ("theta", "ept")   # 並びを固定（同じ指定は同じ画像）
    assert xs.normalize_opts({"contours": "wind"})["contours"] == ("wind",)
    assert xs.normalize_opts({"contours": []})["contours"] == ()
    for bad in ({"shade": "x"}, {"barbs": "x"}, {"contours": ["theta", "foo"]}):
        with pytest.raises(ValueError):
            xs.normalize_opts(bad)


def test_options_text():
    assert xs.options_text({"shade": "rh", "contours": ["theta", "ept"], "barbs": "back"}) == "温位・相当温位／湿り（相対湿度）／風矢羽:背景（薄い灰色）"
    assert xs.options_text({"shade": "none", "contours": [], "barbs": "off"}) == "等値線なし／風矢羽なし"


def test_custom_validate_and_name_include_options():
    q = dict(lat1="41", lon1="126", lat2="29", lon2="146", shade="omega", contours="ept,theta", barbs="back")
    p = custom.validate("xsection", "gfs", "2026111018", "0", q)
    assert (p["shade"], p["contours"], p["barbs"]) == ("omega", "theta,ept", "back")
    assert custom.describe(p).startswith("断面 (41N,126E)→(29N,146E) ｜ ") and "上昇流" in custom.describe(p)
    d = custom.validate("xsection", "gfs", "2026111018", "0", dict(lat1="41", lon1="126", lat2="29", lon2="146"))
    assert (d["shade"], d["contours"], d["barbs"]) == ("div", "theta,ept,wind", "front")      # 省略時は従来どおり
    assert custom.out_path(p) != custom.out_path(d)                                            # 表示要素が違えば別の画像になる
    for bad in ({"shade": "x"}, {"contours": "theta,foo"}, {"barbs": "x"}):
        with pytest.raises(custom.CustomError):
            custom.validate("xsection", "gfs", "2026111018", "0", dict(lat1="41", lon1="126", lat2="29", lon2="146", **bad))


def synthetic_F():
    lats = np.arange(0.0, 60.01, 0.5)
    lons = np.arange(100.0, 160.01, 0.5)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    p = {}
    for lv, t0 in ((1000, 20.0), (850, 10.0), (700, 0.0), (500, -20.0), (300, -45.0), (200, -55.0), (100, -60.0)):
        p[lv] = dict(t=t0 - 0.2 * (la - 30), u=20.0 + 0 * la, v=5.0 * np.sin(np.deg2rad(lo * 6)), r=np.clip(70 + 25 * np.sin(np.deg2rad(la * 8)), 5, 100),
                     w=-15.0 * np.sin(np.deg2rad(la * 6)) * np.cos(np.deg2rad(lo * 5)), gh=5400.0 + 20 * (la - 30))
    return dict(lats=lats, lons=lons, sp=np.full(la.shape, 1000.0), p=p)


@pytest.mark.parametrize("opts", [
    None,
    dict(shade="rh", contours=["theta", "ept"], barbs="back"),
    dict(shade="omega", contours=["ept"], barbs="back"),
    dict(shade="none", contours=[], barbs="off"),
    dict(shade="div", contours=["wind"], barbs="front"),
])
def test_every_combination_renders(tmp_path, opts):
    out = tmp_path / "xs.png"
    xs.render_xsection(synthetic_F(), datetime(2026, 11, 10, 18), 12, (45.0, 130.0), (25.0, 130.0), str(out),
                       model="TEST", name="断面テスト", opts=opts)
    assert out.exists() and out.stat().st_size > 10_000                         # 描けている（要素を何も選ばない図でも、枠・軸・小地図があるので1万バイトは超える）


def test_section_has_humidity_and_omega_masked_above_300(tmp_path):
    S = xs.compute_section(synthetic_F(), (45.0, 130.0), (25.0, 130.0))
    lev = list(S["levels"])
    assert np.isfinite(S["rh"][lev.index(500.0)]).all()                         # 500hPaの湿りは使う
    assert np.isnan(S["rh"][lev.index(200.0)]).all()                            # 300hPaより上は湿度が信頼できないので出さない
    assert np.isfinite(S["w"]).all()                                            # ωは全層で使う
    assert "rh" in S["fine"] and "w" in S["fine"]


def test_parse_ptop_and_section_limits(tmp_path):
    """高度設定（上端の気圧）: 未指定は100、選択肢以外は拒否。上端までの層だけで計算し、描画も通る。"""
    assert xs.parse_ptop(None) == 100 and xs.parse_ptop("300") == 300
    for bad in ("abc", "50", "1000"):
        with pytest.raises(ValueError):
            xs.parse_ptop(bad)
    assert xs.options_text({"ptop": 300}).endswith("／上端300hPa") and xs.options_text({"ptop": 100}) == xs.options_text()
    S = xs.compute_section(synthetic_F(), (45.0, 130.0), (25.0, 130.0), 500)
    assert S["levels"].min() == 500
    out = tmp_path / "xs.png"
    xs.render_xsection(synthetic_F(), datetime(2026, 11, 10, 18), 12, (45.0, 130.0), (25.0, 130.0), str(out), model="TEST", opts=dict(ptop=300))
    assert out.exists() and out.stat().st_size > 10_000


def test_custom_validate_ptop():
    q = dict(lat1="41", lon1="126", lat2="29", lon2="146", lat="35", lon="135")
    for kind in ("xsection", "emagram"):
        d = custom.validate(kind, "gfs", "2026111018", "0", q)
        c = custom.validate(kind, "gfs", "2026111018", "0", {**q, "ptop": "300"})
        assert "ptop" not in d and c["ptop"] == 300 and custom.out_path(d) != custom.out_path(c)   # 既定は p に入れない（画像名が従来と同じ）
        assert "上端300hPa" in custom.describe(c)
        with pytest.raises(custom.CustomError):
            custom.validate(kind, "gfs", "2026111018", "0", {**q, "ptop": "50"})


def test_custom_validate_pt():
    """温位エマグラム(pt)は emagram だけ。既定は p に入れず画像名が従来と同じ、指定すると別の画像名・説明になる。"""
    import custom
    q = {"lat": "35", "lon": "135"}
    a = custom.validate("emagram", "gfs", "2026111018", "0", q)
    b = custom.validate("emagram", "gfs", "2026111018", "0", {**q, "pt": "1"})
    assert "pt" not in a and b["pt"] == 1 and custom.out_path(a) != custom.out_path(b)
    assert custom.describe(b).startswith("温位エマグラム") and custom.describe(a).startswith("エマグラム")
