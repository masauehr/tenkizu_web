# 新規図種の計算部の単体テスト（GRIB2不要・合成データ）
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nwp_charts as nc


def test_charts_are_new_only():
    # nwp_chart_web と重複する図種を持たない
    import emagram
    base = {"instab", "fax57", "fax78", "qvec850", "polar300", "polar500", "surffax", "xs130", "xs140"}
    assert set(nc.CHARTS) == base | {f"em_{k}" for k in emagram.STATIONS}


def test_passes_scale_with_grid():
    class V:
        dx = 0.25
    assert nc._passes(V, 16) == 64          # 0.25° は 0.5° の4倍の回数
    V.dx = 0.5
    assert nc._passes(V, 16) == 16


def test_dewpoint_saturated():
    assert abs(nc.dewpoint(np.array(10.0), np.array(100.0)) - 10.0) < 0.05


def test_xsection_path_and_components():
    import xsection
    plat, plon = xsection.path_points((45.0, 130.0), (25.0, 130.0))
    assert len(plat) == 81 and plat[0] == 45.0 and plat[-1] == 25.0 and set(plon) == {130.0}
    lats = np.arange(0, 60.5, 0.5)
    lons = np.arange(100, 160.5, 0.5)
    u = np.ones((len(lats), len(lons)))
    v = np.zeros_like(u)
    d = xsection.divergence(u, v, lats, lons)
    assert abs(d[60, 60]) < 1e-12            # 一様な東風は発散ゼロ


def test_emagram_profile_and_refine():
    import emagram
    lats = np.arange(20, 50.1, 1.0)
    lons = np.arange(120, 150.1, 1.0)
    shape = (len(lats), len(lons))
    p = {}
    for lv, t in ((925, 15.0), (850, 10.0), (700, 0.0), (500, -20.0), (300, -45.0)):
        p[lv] = dict(t=np.full(shape, t), r=np.full(shape, 60.0), u=np.full(shape, 10.0),
                     v=np.zeros(shape), gh=np.full(shape, 1000.0))
    F = dict(lats=lats, lons=lons, sp=np.full(shape, 880.0), p=p)    # 925hPaは地面より下
    prof = emagram.profile_from_F(F, 35.2, 135.1)
    assert list(prof["p"]) == [850, 700, 500, 300]                    # 地面より下の層は除く
    assert prof["grid_lat"] == 35.0 and prof["grid_lon"] == 135.0
    fine = emagram.refine(prof)
    assert fine["p"][0] == 850 and fine["p"][-1] >= 300 and len(fine["p"]) > 40
