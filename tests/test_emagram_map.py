# エマグラムの地点の小地図（●で場所を示す）のテスト。合成データで実際に描画する
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import emagram as em


def test_map_extent_is_centered_and_clipped():
    w, e, s, n = em.map_extent(35.0, 140.0)
    assert (w, e) == (128.0, 152.0) and (s, n) == (27.0, 43.0)               # 地点が中心（経度±12度・緯度±8度）
    assert em.map_extent(88.0, 10.0)[3] == 90.0                               # 北極の近くでも緯度は90度を超えない
    assert em.map_extent(-88.0, 10.0)[2] == -90.0


def synthetic_F():
    lats = np.arange(0.0, 60.01, 0.5)
    lons = np.arange(100.0, 160.01, 0.5)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    p = {}
    for lv, t0 in ((1000, 20.0), (850, 10.0), (700, 0.0), (500, -20.0), (300, -45.0), (200, -55.0), (100, -60.0)):
        p[lv] = dict(t=t0 - 0.2 * (la - 30), u=15.0 + 0 * la, v=3.0 + 0 * la, r=np.clip(60 + 0 * la, 5, 100),
                     w=0 * la, gh=5400.0 + 20 * (la - 30))
    return dict(lats=lats, lons=lons, sp=np.full(la.shape, 1000.0), p=p)


def test_render_with_location_map(tmp_path):
    out = tmp_path / "em.png"
    em.render_emagram(synthetic_F(), datetime(2026, 11, 10, 18), 12, 35.0, 135.0, str(out), model="TEST", name="テスト地点")
    assert out.exists() and out.stat().st_size > 20_000


def test_map_is_drawn_with_a_marker(tmp_path, monkeypatch):
    """地図の axes（cartopy）が1つ増え、地点（赤い●）が描かれていること。"""
    import matplotlib.pyplot as plt
    import matplotlib.axes
    plotted = []
    orig = matplotlib.axes.Axes.plot

    def spy(self, *a, **k):
        if k.get("marker") == "o" and k.get("markerfacecolor") == "red":
            plotted.append((a[0], a[1]))
        return orig(self, *a, **k)

    monkeypatch.setattr(matplotlib.axes.Axes, "plot", spy)
    em.render_emagram(synthetic_F(), datetime(2026, 11, 10, 18), 0, 33.5, 130.5, str(tmp_path / "em2.png"), model="TEST")
    assert plotted == [([130.5], [33.5])]                                     # 要求した地点に●を1つ描いた


def test_render_pt_emagram(tmp_path):
    """温位エマグラム（pt=True）も描画できる。高度設定（上端）との組み合わせも。"""
    for kw in ({}, {"ptop": 300}):
        out = tmp_path / f"pt{len(kw)}.png"
        r = em.render_emagram(synthetic_F(), datetime(2026, 11, 10, 18), 12, 35.0, 135.0, str(out), model="TEST", pt=True, **kw)
        assert out.exists() and out.stat().st_size > 20_000 and "cape" in r
