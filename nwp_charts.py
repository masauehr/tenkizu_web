#!/usr/bin/env python
# coding: utf-8
"""ECMWF / GFS（0.25°）のGRIB2から気象庁ビューア風の天気図PNGを描画する。

gsm_chart_web の gsm_charts.py をコピーして多モデル用に調整したもの（共通部品化しない方針）。
図種: surf / tenki / z500 / jet300 / t500 / t850 / t700 / t925 / t975 / ept850（975hPaはGFSのみ）
地域: japan（日本周辺・ランベルト） / okinawa（沖縄周辺・正距円筒） / asia（東アジア広域）
  GSMだけ japan3 / okinawa3（日本域0.125°・3時間毎。src="rjp"）
"""
import os
import sys

_proj = os.path.join(sys.prefix, "share", "proj")
if os.path.isdir(_proj):
    os.environ.setdefault("PROJ_LIB", _proj)

# 並列実行時のスレッド競合（sys時間の暴走）を避ける。numpy を import する前に設定
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from datetime import timedelta
from matplotlib.collections import LineCollection
from scipy.ndimage import zoom, maximum_filter, minimum_filter

PC = ccrs.PlateCarree()
KT = 1.94384  # m/s → kt

# 画像サイズ（サンプルと同じ 950×760。下部 42px はキャプション帯）
FIG_W, FIG_H, DPI = 9.5, 7.6, 100
MAP_RECT = [0.002, 42 / 760, 0.996, 716 / 760]
MAP_ASPECT = (0.996 * 9.5) / (716 / 100)  # 地図部分の縦横比（幅/高さ）

# ---------------------------------------------------------------- 地域定義
# center: 投影中心(lon,lat)  width_km: 地図幅  bbox: データ切り出し範囲(lon0,lon1,lat0,lat1)
REGIONS = {
    "japan": dict(label="日本周辺", kind="lcc", center=(133.0, 36.0), width_km=4400,
                  bbox=(95, 180, 5, 68), barb_n=20, coast="10m", zoom=2),
    "okinawa": dict(label="沖縄周辺", kind="pc", center=(128.0, 27.0), width_deg=24.0,
                    bbox=(105, 152, 8, 46), barb_n=16, coast="10m", zoom=3, exclude=("jra55",)),   # JRA-55は1.25°と粗いので作らない
    "asia": dict(label="東アジア広域", kind="lcc", center=(115.0, 33.0), width_km=8600,
                 bbox=(55, 180, -8, 80), barb_n=24, coast="50m", zoom=1),
    # GSM 日本域 0.125°（Rjp, 3時間毎）。データ範囲は 120-150E, 20-50N。**GSM（ローカル専用）だけ**が持つ地域
    "japan3": dict(label="日本域（0.125°・3時間毎）", kind="lcc", center=(135.0, 36.0), width_km=2700,
                   bbox=(120, 150, 20, 50), barb_n=20, coast="10m", zoom=1, src="rjp", models=("gsm",)),
    "okinawa3": dict(label="南西域（0.125°・3時間毎）", kind="pc", center=(133.0, 30.0), width_deg=26.0,
                     bbox=(120, 150, 20, 50), barb_n=16, coast="10m", zoom=1, src="rjp", models=("gsm",)),
}


def regions_for(model):
    """そのモデルで作れる地域（地域の models 指定があればそのモデルだけ。無ければ全モデル共通）。"""
    return [k for k, v in REGIONS.items()
            if ("models" not in v or model in v["models"]) and model not in v.get("exclude", ())]


def make_projection(region):
    """地域の投影と、地図が画像いっぱいに収まる表示範囲（投影座標）を返す。"""
    r = REGIONS[region]
    lon0, lat0 = r["center"]
    if r["kind"] == "lcc":
        proj = ccrs.LambertConformal(central_longitude=lon0, central_latitude=lat0,
                                     standard_parallels=(30, 60))
        cx, cy = proj.transform_point(lon0, lat0, PC)
        w = r["width_km"] * 1000.0
        h = w / MAP_ASPECT
    else:
        proj = ccrs.PlateCarree()
        cx, cy = lon0, lat0
        w = r["width_deg"]
        h = w / MAP_ASPECT
    return proj, (cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2)


# ---------------------------------------------------------------- データ前処理
class View:
    """全球場を地域の切り出し範囲へ切り出し、等値線用に補間して返す。"""

    def __init__(self, F, region):
        r = REGIONS[region]
        lon0, lon1, lat0, lat1 = r["bbox"]
        ix = np.where((F["lons"] >= lon0) & (F["lons"] <= lon1))[0]
        iy = np.where((F["lats"] >= lat0) & (F["lats"] <= lat1))[0]
        self.sx, self.sy = slice(ix[0], ix[-1] + 1), slice(iy[0], iy[-1] + 1)
        self.lons, self.lats = F["lons"][self.sx], F["lats"][self.sy]
        self.z = r["zoom"]
        self.barb_n = r["barb_n"]
        nx, ny = len(self.lons), len(self.lats)
        nfx, nfy = (nx - 1) * self.z + 1, (ny - 1) * self.z + 1
        self._zf = (nfy / ny, nfx / nx)  # 出力形状を明示（端点が揃う）
        fx = np.linspace(self.lons[0], self.lons[-1], nfx)
        fy = np.linspace(self.lats[0], self.lats[-1], nfy)
        self.flon, self.flat = np.meshgrid(fx, fy)
        self.rlon, self.rlat = np.meshgrid(self.lons, self.lats)
        self.dx = float(self.lons[1] - self.lons[0])  # 元格子の間隔[度]（0.5 or 0.125）
        self.rmask = self.fmask = None  # 地面下マスク（set_level で設定）

    def xy(self, ax):
        """等値線・ハッチ用に、細かい格子の座標を地図の投影座標へ自前で変換して返す（結果は投影ごとに保持）。

        cartopy 0.26 の contourf(transform=PC) は帯の領域が地図全面に広がって描かれる不具合があるため、
        transform を使わず投影後の座標で描く（0.25 でも同じ結果）。"""
        key = id(ax.projection)
        if getattr(self, "_xy_key", None) != key:
            p = ax.projection.transform_points(PC, self.flon, self.flat)
            self._xy, self._xy_key = (p[..., 0], p[..., 1]), key
        return self._xy

    def set_level(self, F, level):
        """等圧面の図で、地表気圧が level より低い（=地面の下）領域をマスクする。None で解除。"""
        if level is None:
            self.rmask = self.fmask = None
            return
        under = (self.raw(F["sp"]) < level).astype(np.float32)
        self.rmask = under > 0.5
        self.fmask = zoom(under, self._zf, order=1) > 0.5

    def raw(self, a):
        """切り出しのみ（元の0.5°格子）"""
        return a[self.sy, self.sx]

    def fine(self, a, cropped=False):
        """切り出して3次補間で細かくした場（等値線・塗りつぶし用）。cropped=True なら切り出し済み"""
        z = zoom(a if cropped else self.raw(a), self._zf, order=3)
        return np.ma.masked_where(self.fmask, z) if self.fmask is not None else z


def dewpoint(t_c, rh):
    """気温[℃]・相対湿度[%] → 露点[℃]（Magnus式）"""
    rh = np.clip(rh, 1.0, 100.0)
    a, b = 17.62, 243.12
    g = np.log(rh / 100.0) + a * t_c / (b + t_c)
    return b * g / (a - g)


def vorticity(u, v, lats, lons):
    """相対渦度 [1e-6 /s]（球面格子の中心差分）"""
    R = 6.371e6
    la = np.deg2rad(lats)[:, None]
    dlam = np.deg2rad(lons[1] - lons[0])
    dphi = np.deg2rad(lats[1] - lats[0])
    cosp = np.cos(la)
    dvdx = np.gradient(v, axis=1) / (R * cosp * dlam)
    ducos = np.gradient(u * cosp, axis=0) / (R * cosp * dphi)
    return (dvdx - ducos) * 1e6


_THW_TABLES = {}


def _theta_w_to_tup(level_up):
    """湿潤断熱線（θw）上の level_up[hPa] の気温[K]を引く対応表（SSI用）。level_up ごとに一度だけ作る。"""
    if level_up not in _THW_TABLES:
        import metpy.calc as mpcalc
        from metpy.units import units
        thw = np.arange(240.0, 321.0, 1.0)
        tup = []
        for t in thw:
            r = mpcalc.moist_lapse(np.array([1000.0, float(level_up)]) * units.hPa, t * units.K)
            tup.append(float(r[-1].to("K").m))
        _THW_TABLES[level_up] = (thw, np.array(tup))
    return _THW_TABLES[level_up]


def ssi(F, lv_low=850, lv_up=500):
    """ショワルター安定指数 SSI = T(lv_up) − lv_low から持ち上げた気塊の気温。負で不安定。"""
    import metpy.calc as mpcalc
    from metpy.units import units
    d = F["p"][lv_low]
    td = np.minimum(dewpoint(d["t"], d["r"]), d["t"])
    thw = mpcalc.wet_bulb_potential_temperature(
        lv_low * units.hPa, d["t"] * units.degC, td * units.degC).to("K").m
    tp_up = np.interp(thw, *_theta_w_to_tup(lv_up))
    return (F["p"][lv_up]["t"] + 273.15) - tp_up


def ept(F, lv=850):
    """相当温位[K]"""
    import metpy.calc as mpcalc
    from metpy.units import units
    d = F["p"][lv]
    td = np.minimum(dewpoint(d["t"], d["r"]), d["t"])
    return mpcalc.equivalent_potential_temperature(
        lv * units.hPa, d["t"] * units.degC, td * units.degC).to("K").m


# ---------------------------------------------------------------- 描画部品
def lines(ax, V, a, levels, major=None, color="k", lw=0.8, lw_major=1.8, ls="-",
          label_levels=None, fmt="%d", fs=8):
    """細線＋太線(major)の等値線。label_levels の線にはラベルを付ける。"""
    lv = np.array(levels, dtype=float)
    z = V.fine(a)
    if lv.min() > np.nanmax(z) or lv.max() < np.nanmin(z):
        return
    cs = ax.contour(*V.xy(ax), z, levels=lv, colors=color, linewidths=lw,
                    linestyles=ls, zorder=3)
    if major is not None and len(major):
        m = [x for x in major if np.nanmin(z) <= x <= np.nanmax(z)]
        if m:
            ax.contour(*V.xy(ax), z, levels=m, colors=color, linewidths=lw_major,
                       linestyles=ls, zorder=3)
    ll = label_levels if label_levels is not None else major
    if ll:
        ll = [x for x in ll if np.nanmin(z) <= x <= np.nanmax(z)]
        if ll:
            try:
                ax.clabel(cs, levels=ll, fmt=fmt, fontsize=fs, inline=True, inline_spacing=2)
            except Exception:
                pass


def hatch(ax, V, a, levels, hatches, color, zorder=2):
    """ハッチ（斜線・点）塗り。levels は境界、hatches は各区間のパターン。"""
    z = V.fine(a)
    with matplotlib.rc_context({"hatch.color": color, "hatch.linewidth": 0.8}):
        ax.contourf(*V.xy(ax), z, levels=levels, colors="none", hatches=hatches, zorder=zorder, extend="neither")


def fill(ax, V, a, levels, colors, zorder=1, extend="neither"):
    z = V.fine(a)
    ax.contourf(*V.xy(ax), z, levels=levels, colors=colors,
                zorder=zorder, extend=extend)


def barbs(ax, V, u, v, color, lw=0.7, length=5.2):
    """風矢羽（kt）。regrid_shape で間引く。"""
    uu, vv = V.raw(u) * KT, V.raw(v) * KT
    if V.rmask is not None:
        uu, vv = np.ma.masked_where(V.rmask, uu), np.ma.masked_where(V.rmask, vv)
    ax.barbs(V.rlon, V.rlat, uu, vv, transform=PC, regrid_shape=V.barb_n,
             length=length, linewidth=lw, color=color, zorder=5)


def extrema(ax, V, a, kind, win, color="k", fs=11, dec=0, label=None, show_val=True):
    """高気圧(H)・低気圧(L)のスタンプ。近接する重複は強い方だけ残す。
    win は 0.5°格子での格子点数。格子間隔が違えば度数が同じになるよう換算する。"""
    win = max(3, int(round(win * 0.5 / V.dx)))
    z = V.raw(a)
    if V.dx < 0.4:  # 細かい格子では小さな凹凸が極値になるため、3度程度に平滑化してから探す
        from scipy.ndimage import uniform_filter
        z = uniform_filter(z, size=max(3, int(round(1.5 / V.dx))), mode="nearest")
    filt = maximum_filter if kind == "H" else minimum_filter
    ys, xs = np.where(filt(z, size=win, mode="nearest") == z)
    mg = max(3, int(round(1.5 / V.dx)))  # 切り出し端から1.5度は除く
    cand = sorted(((z[j, i], i, j) for j, i in zip(ys, xs)
                   if mg < i < len(V.lons) - mg - 1 and mg < j < len(V.lats) - mg - 1),
                  reverse=(kind == "H"))
    ex = ax.get_extent()
    mx = (ex[1] - ex[0]) * 0.02  # 画面端から2%は除外
    my = (ex[3] - ex[2]) * 0.02
    chosen = []
    for val, i, j in cand:
        if any((i - ci) ** 2 + (j - cj) ** 2 < win ** 2 for _, ci, cj in chosen):
            continue
        px, py = ax.projection.transform_point(V.lons[i], V.lats[j], PC)
        if not (ex[0] + mx < px < ex[1] - mx and ex[2] + my < py < ex[3] - my):
            continue
        chosen.append((val, i, j))
        lo, la = V.lons[i], V.lats[j]
        ax.text(lo, la, label or kind, transform=PC, ha="center", va="center", fontsize=fs + 3,
                fontweight="bold", color=color, zorder=8)
        if show_val:
            ax.text(lo, la - 0.9, f"{val:.{dec}f}", transform=PC, ha="center", va="top",
                    fontsize=fs - 3, color=color, zorder=8)


def legend_box(ax, boxes, labels, center=False, rect=(0.724, 0.008, 0.272, 0.078)):
    """右下の凡例。boxes=[(facecolor, hatch, hatchcolor)...]、labels は境界値の文字列（len(boxes)+1）。
    rect: 凡例の位置 [x, y, 幅, 高さ]（軸座標）。2つ並べるときは左側の凡例をずらす。"""
    ia = ax.inset_axes(list(rect), transform=ax.transAxes, zorder=20)
    ia.set_xlim(0, 1)
    ia.set_ylim(0, 1)
    ia.set_xticks([])
    ia.set_yticks([])
    for s in ia.spines.values():
        s.set_linewidth(1.0)
    ia.set_facecolor("white")
    n = len(boxes)
    x0, x1 = 0.04, 0.96
    w = (x1 - x0) / n
    for k, (fc, ht, hc) in enumerate(boxes):
        # ハッチ色は edgecolor で決まるため、ハッチ用と枠線用の2枚を重ねる
        ia.add_patch(plt.Rectangle((x0 + k * w, 0.52), w, 0.36, facecolor=fc,
                                   edgecolor=hc or "k", linewidth=0, hatch=ht))
        ia.add_patch(plt.Rectangle((x0 + k * w, 0.52), w, 0.36, fill=False,
                                   edgecolor="k", linewidth=0.6))
    for k, lab in enumerate(labels):
        # center=True: 各ボックスの中央に区分名を書く（日本語は和文フォントへフォールバック）
        ia.text(x0 + (k + 0.5 if center else k) * w, 0.30, lab, ha="center", va="center", fontsize=7,
                family=["Hiragino Sans", "Arial Unicode MS", "DejaVu Sans"])


# ---------------------------------------------------------------- 各図の描画
# ---------------------------------------------------------------- 新規図種（tenkizu 由来）
# nwp_chart_web に無い図だけを作る方針。計算は MetPy。0.25°の格子に合わせて平滑化の回数を換算する。
def _passes(V, base):
    """tenkizu は 0.5° 格子向けの平滑回数。格子間隔が違っても同じ空間スケールになるよう換算（回数 ∝ (0.5/dx)^2）。"""
    return max(1, int(round(base * (0.5 / V.dx) ** 2)))


def _smooth(V, a, base):
    import metpy.calc as mpcalc
    return np.asarray(mpcalc.smooth_n_point(np.asarray(a, dtype=float), 9, _passes(V, base)))


def _embed(V, F, crop):
    """切り出し範囲で計算した場を、View.fine が受け取れる全球サイズの配列へ戻す。"""
    full = np.zeros_like(F["msl"], dtype=float)
    full[V.sy, V.sx] = crop
    return full


def _deltas(V):
    import metpy.calc as mpcalc
    return mpcalc.lat_lon_grid_deltas(V.lons, V.lats)


def _blue_lines(ax, V, a, step, major_step, fs=8, color="blue"):
    """気温の青線（細線＋太線）。FAX天気図風。"""
    lv = list(range(-80, 61, step))
    lines(ax, V, a, lv, major=list(range(-80, 61, major_step)), color=color, lw=1.0, lw_major=2.0,
          label_levels=lv if step >= 3 else None, fmt="%d", fs=fs)


def fill_ext(ax, V, a, lev, cols):
    """範囲外（補間のはみ出しを含む）も両端の色で塗る。"""
    fill(ax, V, a, [-1e6] + list(lev) + [1e6], [cols[0]] + list(cols) + [cols[-1]])


def _cmap_colors(name, n):
    cm = plt.get_cmap(name)
    return [matplotlib.colors.to_hex(cm((i + 0.5) / n)) for i in range(n)]


def chart_qvec850(ax, V, F, ctx):
    """850hPa Qベクトル発散（シェード）・Qベクトル（矢印）・気温（灰破線）・高度（黒）"""
    import metpy.calc as mpcalc
    from metpy.units import units
    d = F["p"][850]
    V.set_level(F, 850)
    dx, dy = _deltas(V)
    gh = _smooth(V, V.raw(d["gh"]), 16)
    tk = _smooth(V, V.raw(d["t"]) + 273.15, 8)
    lat2d = V.rlat * units.degrees
    ug, vg = mpcalc.geostrophic_wind(gh * units.m, dx=dx, dy=dy, latitude=lat2d)
    qu, qv = mpcalc.q_vector(ug, vg, tk * units.K, 850 * units.hPa, dx=dx, dy=dy)
    qdiv = mpcalc.divergence(qu, qv, dx=dx, dy=dy).m * 1e18      # 1e-18 m s-1 kg-1
    # 発散(+)=赤系・収束(−)=青系。中央(-2〜2)はほぼ白
    lev = [-30, -25, -20, -15, -10, -5, -2, 2, 5, 10, 15, 20, 25, 30]
    cols = _cmap_colors("bwr_r", len(lev) - 1)
    cols[len(lev) // 2 - 1] = "#ffffff"
    fill_ext(ax, V, _embed(V, F, qdiv), lev, cols)
    tc = _embed(V, F, tk - 273.15)
    lines(ax, V, tc, list(range(-60, 46, 3)), color="#707070", lw=1.0, ls="--",
          label_levels=list(range(-60, 46, 3)), fmt="%d", fs=8)
    lines(ax, V, d["gh"], np.arange(1000, 2000, 30), major=list(range(1200, 1800, 120)),
          color="k", lw=0.9, lw_major=1.8, label_levels=list(range(1200, 1800, 60)), fmt="%d", fs=8)
    qm = np.ma.masked_where(V.rmask, qu.m) if V.rmask is not None else qu.m
    qn = np.ma.masked_where(V.rmask, qv.m) if V.rmask is not None else qv.m
    ax.quiver(V.rlon, V.rlat, qm, qn, transform=PC, regrid_shape=max(10, V.barb_n // 1),
              pivot="mid", color="k", scale=1.5e-11, scale_units="inches", width=0.0025, zorder=5)
    ctx["title"] = "QVEC850,DIV,T850,Z850"
    legend_box(ax, [(c, None, None) for c in cols],
               [str(x) if x in (-30, -10, -2, 2, 10, 30) else "" for x in lev])


def chart_instab(ax, V, F, ctx):
    """不安定域: 300hPa飽和相当温位 − 850hPa以下の最大相当温位（負が不安定）＋ 300hPa気温（赤一点鎖線）"""
    import metpy.calc as mpcalc
    from metpy.units import units
    top = 300
    low = [lv for lv in sorted(F["p"]) if lv >= 850]
    sp = V.raw(F["sp"])
    emax = None
    for lv in low:
        d = F["p"][lv]
        t, r = V.raw(d["t"]), V.raw(d["r"])
        td = np.minimum(dewpoint(t, r), t)
        e = mpcalc.equivalent_potential_temperature(lv * units.hPa, t * units.degC, td * units.degC).to("K").m
        e = np.where(sp >= lv, e, -np.inf)               # 地面より下の層は使わない
        emax = e if emax is None else np.maximum(emax, e)
    emax = np.where(np.isfinite(emax), emax, np.nan)
    st = mpcalc.saturation_equivalent_potential_temperature(top * units.hPa, (V.raw(F["p"][top]["t"]) + 273.15) * units.K).m
    diff = np.nan_to_num(st - emax, nan=12.0)
    V.set_level(F, None)
    lev = list(range(-12, 13, 3))
    cols = _cmap_colors("jet_r", len(lev) - 1)
    fill_ext(ax, V, _embed(V, F, diff), lev, cols)
    tl = list(range(-60, 30, 3))
    lines(ax, V, F["p"][top]["t"], tl, color="red", lw=1.0, ls="-.", label_levels=tl, fmt="%d", fs=8)
    ctx["title"] = f"SEPT{top}-maxEPT{low[0] if low else 850},T{top}"
    legend_box(ax, [(c, None, None) for c in cols], [str(x) for x in lev])


def chart_fax57(ax, V, F, ctx):
    """FAX57相当: 500hPa気温（青）＋ 700hPa湿数（緑=湿り・灰=3〜6℃・黄=乾燥）"""
    d7, d5 = F["p"][700], F["p"][500]
    V.set_level(F, 700)
    ttd = np.maximum(d7["t"] - dewpoint(d7["t"], d7["r"]), 0.0)
    lev = [-0.1, 3, 6, 18, 200]
    cols = ["#2fa02f", "#666666", "#ffffff", "#ffe45a"]
    fill(ax, V, ttd, lev, cols)
    lines(ax, V, ttd, list(range(3, 30, 3)), color="#808080", lw=0.8)
    V.set_level(F, 500)
    _blue_lines(ax, V, d5["t"], 3, 15, fs=9)
    lines(ax, V, d5["t"], [-30], color="purple", lw=2.2)
    ctx["title"] = "TTD700,T500"
    legend_box(ax, [(c, None, None) for c in cols], ["0", "3", "6", "18", ""], center=False)


def chart_fax78(ax, V, F, ctx):
    """FAX78相当: 700hPa発散・収束（シェード）＋ 850hPa気温（青）・風 ＋ W/Cスタンプ"""
    import metpy.calc as mpcalc
    from metpy.units import units
    d8, d7 = F["p"][850], F["p"][700]
    V.set_level(F, 700)
    dx, dy = _deltas(V)
    div = mpcalc.divergence(V.raw(d7["u"]) * units("m/s"), V.raw(d7["v"]) * units("m/s"), dx=dx, dy=dy)
    div = _smooth(V, div.m, 16) * 1e5                      # 1e-5 /s
    lev = [-20, -10, -5, -2, 2, 5, 10]
    cols = ["#ff3030", "#ff9a20", "#a0a0a0", "#ffffff", "#ffe45a", "#87ceeb"]
    fill_ext(ax, V, _embed(V, F, div), lev, cols)
    V.set_level(F, 850)
    _blue_lines(ax, V, d8["t"], 3, 15, fs=9)
    extrema(ax, V, d8["t"], "H", 20, color="red", label="W", show_val=False)
    extrema(ax, V, d8["t"], "L", 20, color="blue", label="C", show_val=False)
    barbs(ax, V, d8["u"], d8["v"], "k")
    ctx["title"] = "DIV700,T850,Wind850"
    legend_box(ax, [(c, None, None) for c in cols], [str(x) for x in lev])


CHARTS = {
    # 並びは 高層→地上。regions: 作る地域を限る（容量のため。省略=全地域）。levels: 無いモデルでは作らない気圧面
    "instab": dict(label="不安定域（SEPT300−maxEPT・T300）", fn=chart_instab, levels=[300, 850], regions=("japan",)),
    "fax57": dict(label="FAX57相当（500hPa気温・700hPa湿数）", fn=chart_fax57, levels=[500, 700], regions=("japan",)),
    "fax78": dict(label="FAX78相当（850hPa気温・風・700hPa発散）", fn=chart_fax78, levels=[700, 850], regions=("japan",)),
    "qvec850": dict(label="850hPa Qベクトル発散", fn=chart_qvec850, levels=[850], regions=("japan",)),
}


# ---------------------------------------------------------------- 海岸線・国境（地域ごとに一度だけ投影）
_BASE_CACHE = {}


def base_lines(region, proj):
    """海岸線・国境を地域の範囲で切り出して投影した線分リスト（coast, border）を返す。"""
    if region in _BASE_CACHE:
        return _BASE_CACHE[region]
    from shapely.geometry import box
    r = REGIONS[region]
    lon0, lon1, lat0, lat1 = r["bbox"]
    clip = box(lon0, lat0, lon1, lat1)

    def project(feature):
        segs = []
        for g in feature.geometries():
            g = g.intersection(clip)
            if g.is_empty:
                continue
            pg = proj.project_geometry(g, PC)
            for part in getattr(pg, "geoms", [pg]):
                if part.geom_type == "LineString":
                    segs.append(np.asarray(part.coords))
        return segs

    coast = project(cfeature.COASTLINE.with_scale(r["coast"]))
    border = project(cfeature.NaturalEarthFeature("cultural", "admin_0_boundary_lines_land", "50m"))
    _BASE_CACHE[region] = (coast, border)
    return coast, border


# ---------------------------------------------------------------- 枠・見出し
def _caption(fig, parts, y):
    """色の違う文字を横に並べる（実際の描画幅で位置を決める）。"""
    r = fig.canvas.get_renderer()
    x = 4 / (FIG_W * DPI)
    for text, color, fs in parts:
        t = fig.text(x, y, text, color=color, fontsize=fs, family="serif",
                     va="center", ha="left")
        bb = t.get_window_extent(r)
        x += bb.width / (FIG_W * DPI) + 8 / (FIG_W * DPI)


def render_chart(chart, region, F, init, ft, outpath, model="", quantize=True):
    """1枚描画して outpath に保存する。init は UTC の datetime。"""
    proj, extent = make_projection(region)
    V = View(F, region)
    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI)
    ax = fig.add_axes(MAP_RECT, projection=proj)
    ax.set_extent(extent, crs=proj)

    coast, border = base_lines(region, proj)
    ax.add_collection(LineCollection(coast, colors="k", linewidths=0.8, zorder=4))
    ax.add_collection(LineCollection(border, colors="#555555", linewidths=0.4, zorder=4))
    gl = ax.gridlines(crs=PC, draw_labels=False, linewidth=0.7, color="k", alpha=1.0,
                      xlocs=np.arange(-180, 361, 10), ylocs=np.arange(-80, 91, 10))
    gl.set_zorder(4)
    for s in ax.spines.values():
        s.set_edgecolor("#1010e0")
        s.set_linewidth(2.5)

    ctx = dict(ft=ft, title=chart)
    CHARTS[chart]["fn"](ax, V, F, ctx)

    valid = init + timedelta(hours=ft)
    jst = valid + timedelta(hours=9)
    ax.text(0.02, 0.975, f"{valid:%d %H}", transform=ax.transAxes, fontsize=27, family="serif",
            color="#5a3410", va="top", zorder=30)
    ax.text(0.145, 0.955, "UTC", transform=ax.transAxes, fontsize=9,
            family="serif", color="#5a3410", va="top", zorder=30)
    ax.text(0.985, 0.975, f"{jst:%d %H}", transform=ax.transAxes, fontsize=27,
            family="serif", color="red", va="top", ha="right", zorder=30)
    ax.text(0.985, 0.915, "JST", transform=ax.transAxes, fontsize=9, family="serif",
            color="red", va="top", ha="right", zorder=30)
    stamp = f"ANAL:{valid:%Y/%m/%d %H:%M}" if F.get("no_cloud") else f"INIT:{init:%m/%d %H:%M}"   # 解析値(JRA-55)は解析時刻を年つきで出す（昔の日付のため）
    ax.text(0.006, 0.012, f"{model} {stamp}(UTC)".strip(), transform=ax.transAxes, fontsize=10,
            color="k", va="bottom", zorder=30)

    _caption(fig, [(f"{init:%d%H}", "#1010e0", 17), (f"<{ft}>", "#d020d0", 17),
                   (ctx["title"], "#7a5a30", 17)], y=21 / 760)
    fig.savefig(outpath, dpi=DPI)
    plt.close(fig)
    if quantize:
        _quantize_png(outpath)


def _quantize_png(path):
    """PNGを256色パレットへ減色して容量を約1/3にする（線画・塗り主体なので見た目はほぼ変わらない）。"""
    try:
        from PIL import Image
    except ImportError:
        return
    im = Image.open(path).convert("RGB")
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)
