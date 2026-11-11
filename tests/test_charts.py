# 新規図種の計算部の単体テスト（GRIB2不要・合成データ）
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nwp_charts as nc


def test_charts_are_new_only():
    # nwp_chart_web と重複する図種を持たない
    assert set(nc.CHARTS) == {"instab", "fax57", "fax78", "qvec850", "polar300", "polar500", "surffax"}


def test_passes_scale_with_grid():
    class V:
        dx = 0.25
    assert nc._passes(V, 16) == 64          # 0.25° は 0.5° の4倍の回数
    V.dx = 0.5
    assert nc._passes(V, 16) == 16


def test_dewpoint_saturated():
    assert abs(nc.dewpoint(np.array(10.0), np.array(100.0)) - 10.0) < 0.05
