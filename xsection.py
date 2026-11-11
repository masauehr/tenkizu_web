#!/usr/bin/env python
# coding: utf-8
"""鉛直断面図（温位・相当温位・断面平行風・風矢羽・発散）。

tenkizu の GSM_CrossSection.py の描画内容を、MetPy の cross_section に頼らず、
格子データ F（nwp_data.load_fields の形式）から経路上へ直接補間して作る。
始点・終点は任意（ローカルは自由選択、公開は SECTIONS の固定断面）。
"""
import numpy as np
import matplotlib.pyplot as plt
from datetime import timedelta
from scipy.ndimage import map_coordinates, gaussian_filter, gaussian_filter1d

R_EARTH = 6.371e6
KT = 1.94384

# 公開用の固定断面（キー: 図種名の一部）。(表示名, (始点緯度, 始点経度), (終点緯度, 終点経度))
SECTIONS = {
    "ns130": ("南北断面 130E（45N→25N）", (45.0, 130.0), (25.0, 130.0)),
    "ns140": ("南北断面 140E（45N→25N）", (45.0, 140.0), (25.0, 140.0)),
}


def _dewpoint(t_c, rh):
    rh = np.clip(rh, 1.0, 100.0)
    a, b = 17.62, 243.12
    g = np.log(rh / 100.0) + a * t_c / (b + t_c)
    return b * g / (a - g)


def _ept(p_hpa, t_c, td_c):
    """相当温位[K]（MetPy）"""
    import metpy.calc as mpcalc
    from metpy.units import units
    td = np.minimum(td_c, t_c)
    return mpcalc.equivalent_potential_temperature(
        p_hpa * units.hPa, t_c * units.degC, td * units.degC).to("K").m


def divergence(u, v, lats, lons):
    """水平発散 [1/s]（球面格子の中心差分）。lats は昇順。"""
    la = np.deg2rad(lats)[:, None]
    dlam = np.deg2rad(lons[1] - lons[0])
    dphi = np.deg2rad(lats[1] - lats[0])
    cosp = np.cos(la)
    dudx = np.gradient(u, axis=1) / (R_EARTH * cosp * dlam)
    dvdy = np.gradient(v * cosp, axis=0) / (R_EARTH * cosp * dphi)
    return dudx + dvdy


def path_points(start, end, step_deg=0.25):
    """始点・終点（緯度, 経度）を結ぶ線上の等間隔の点（緯度・経度の線形補間）。"""
    n = int(max(abs(end[0] - start[0]), abs(end[1] - start[1])) / step_deg) + 1
    n = max(n, 2)
    return np.linspace(start[0], end[0], n), np.linspace(start[1], end[1], n)


def _sample(arr, lats, lons, plat, plon):
    """2次元場 arr[lat, lon] を経路上の点へ双一次補間する。"""
    yi = (plat - lats[0]) / (lats[1] - lats[0])
    xi = ((plon - lons[0]) % 360.0) / (lons[1] - lons[0])
    return map_coordinates(np.asarray(arr, dtype=float), [yi, xi], order=1, mode="nearest")


def compute_section(F, start, end):
    """断面上の各量を返す。levels は下（大きい気圧）から上へ並べる。"""
    lats, lons = F["lats"], F["lons"]
    plat, plon = path_points(start, end)
    levels = sorted(F["p"], reverse=True)
    # 経路の向き（東・北成分の単位ベクトル）。断面平行・垂直の風の分解に使う
    dlat, dlon = end[0] - start[0], end[1] - start[1]
    ex, ey = dlon * np.cos(np.deg2rad(plat.mean())), dlat
    nrm = np.hypot(ex, ey) or 1.0
    ex, ey = ex / nrm, ey / nrm
    out = {k: [] for k in ("theta", "ept", "u", "v", "div", "t")}
    for lv in levels:
        d = F["p"][lv]
        t_c = _sample(d["t"], lats, lons, plat, plon)
        u = gaussian_filter1d(_sample(d["u"], lats, lons, plat, plon), 2.0, mode="nearest")
        v = gaussian_filter1d(_sample(d["v"], lats, lons, plat, plon), 2.0, mode="nearest")
        r = _sample(d["r"], lats, lons, plat, plon)
        out["t"].append(t_c)
        out["theta"].append((t_c + 273.15) * (1000.0 / lv) ** 0.2857)
        # 相当温位は水蒸気の信頼できる 300hPa 以下だけ（tenkizu と同じ）
        out["ept"].append(_ept(lv, t_c, _dewpoint(t_c, r)) if lv >= 300 else np.full_like(t_c, np.nan))
        out["u"].append(u)
        out["v"].append(v)
        div = gaussian_filter(divergence(d["u"], d["v"], lats, lons), sigma=4.0)   # 0.25°格子で約1°の平滑化
        out["div"].append(_sample(div, lats, lons, plat, plon))
    res = {k: np.array(v) for k, v in out.items()}
    res.update(levels=np.array(levels, dtype=float), plat=plat, plon=plon,
               sp=_sample(F["sp"], lats, lons, plat, plon),
               tw=(res["u"] * ex + res["v"] * ey), nw=(-res["u"] * ey + res["v"] * ex))
    # 地面より下（気圧が地表気圧より大きい）はマスクする
    below = res["levels"][:, None] > res["sp"][None, :]
    for k in ("theta", "ept", "u", "v", "div", "tw", "nw", "t"):
        res[k] = np.where(below, np.nan, res[k])
        res[k + "_raw"] = res[k]
    res["ax"] = "lat" if abs(dlat) >= abs(dlon) else "lon"
    res["fine"] = _vertical_fine(res, levels_raw=np.array(levels, dtype=float))
    return res


def _vertical_fine(res, levels_raw, n=70):
    """等値線を滑らかにするため、鉛直を log(気圧) で線形補間して細かい層にする（地面より下はマスク）。"""
    lf = np.exp(np.linspace(np.log(levels_raw.max()), np.log(levels_raw.min()), n))
    la = np.log(levels_raw)[::-1]                      # np.interp は昇順が必要
    out = {"levels": lf}
    for k in ("theta", "ept", "tw", "div", "t"):
        a = res[k + "_raw"][::-1]                      # 地面より下を埋める前の値
        f = np.full((n, a.shape[1]), np.nan)
        for j in range(a.shape[1]):
            ok = np.isfinite(a[:, j])
            if ok.sum() >= 2:
                f[:, j] = np.interp(np.log(lf)[::-1], la[ok], a[ok, j], left=np.nan, right=np.nan)[::-1]
        out[k] = np.where(lf[:, None] > res["sp"][None, :], np.nan, f)
    return out


def render_xsection(F, init, ft, start, end, outpath, model="", name="", quantize=True, wind="uv"):
    """断面図を1枚描画して outpath へ保存する。start/end は (緯度, 経度)。wind: uv=風矢羽(UV) / tn=断面平行・垂直"""
    import nwp_charts as nc
    S = compute_section(F, start, end)
    x = S["plat"] if S["ax"] == "lat" else S["plon"]
    lev = S["levels"]
    W, H = 9.5, 7.6
    fig = plt.figure(figsize=(W, H), dpi=nc.DPI)
    ax = fig.add_axes([0.075, 0.115, 0.70, 0.80])
    X, Y = np.meshgrid(x, lev)
    Fi = S["fine"]
    XF, YF = np.meshgrid(x, Fi["levels"])

    # 発散（シェード）
    dv = np.ma.masked_invalid(Fi["div"] * 1e5)
    cf = ax.contourf(XF, YF, np.clip(dv, -4.9, 4.9), levels=[-5, -4, -2, -1, 1, 2, 4, 5],
                     cmap=plt.cm.bwr, alpha=0.55, extend="neither")
    # 地面（地表気圧の線の下を灰色で）
    ax.fill_between(x, S["sp"], 1100, color="#9a8f80", zorder=6)
    # 温位（黒）・相当温位（赤）・断面平行風（青、kt）
    th = np.ma.masked_invalid(Fi["theta"])
    c1 = ax.contour(XF, YF, th, levels=np.arange(252, 450, 3), colors="k", linewidths=1.6, zorder=3)
    ax.clabel(c1, c1.levels[1::2], fontsize=7, fmt="%d", inline=True, inline_spacing=6)
    ep = np.ma.masked_invalid(Fi["ept"])
    if ep.count():
        c2 = ax.contour(XF, YF, ep, levels=np.arange(252, 450, 3), colors="r", linewidths=0.9, zorder=3)
        ax.clabel(c2, c2.levels[1::2], fontsize=7, fmt="%d", inline=True, inline_spacing=6)
    tw = np.ma.masked_invalid(Fi["tw"] * KT)
    c3 = ax.contour(XF, YF, tw, levels=np.arange(-150, 151, 10), colors="b", linewidths=0.9, zorder=3)
    ax.clabel(c3, c3.levels[1::2], fontsize=7, fmt="%d", inline=True, inline_spacing=6)
    # 風矢羽
    sl = slice(2, None, 4)
    uu, vv = (S["u"], S["v"]) if wind == "uv" else (S["tw"], S["nw"])
    ax.barbs(x[sl], lev, (uu * KT)[:, sl], (vv * KT)[:, sl], length=5.2, linewidth=0.6,
             color="k", zorder=7)

    ax.set_yscale("log")
    ax.set_ylim(1000, lev.min())
    ticks = [t for t in (1000, 925, 850, 700, 500, 400, 300, 250, 200, 150, 100) if t >= lev.min() - 1]
    ax.set_yticks(ticks)
    ax.set_yticklabels([str(t) for t in ticks])
    ax.minorticks_off()
    ax.set_ylabel("Pressure (hPa)")
    ax.set_xlabel("Latitude (°N)" if S["ax"] == "lat" else "Longitude (°E)")
    ax.grid(alpha=0.25)
    if S["ax"] == "lat" and x[0] > x[-1]:
        ax.invert_xaxis()    # 左が北→右が南にならないよう、始点が左にくる向きに合わせる（緯度は値が減る向きのまま）

    # 凡例（右側）
    fig.text(0.79, 0.50, "黒: 温位 (K)\n赤: 相当温位 (K)\n青: 断面平行風 (kt)\n色: 水平発散\n    (赤=発散, 青=収束)\n矢羽: " +
             ("風 (kt)" if wind == "uv" else "断面平行/垂直 (kt)"),
             fontsize=8, va="top", family=["Hiragino Sans", "Noto Sans CJK JP", "Arial Unicode MS", "DejaVu Sans"])
    cax = fig.add_axes([0.80, 0.28, 0.16, 0.014])
    cb = fig.colorbar(cf, cax=cax, orientation="horizontal")
    cb.set_label("発散 (×1e-5 /s)", fontsize=7, family=["Hiragino Sans", "Noto Sans CJK JP", "Arial Unicode MS", "DejaVu Sans"])
    cb.ax.tick_params(labelsize=6)

    # 小地図（500hPa高度＋断面位置）
    import cartopy.crs as ccrs
    PC = ccrs.PlateCarree()
    lo0, lo1 = min(start[1], end[1]) - 15, max(start[1], end[1]) + 15
    la0, la1 = min(start[0], end[0]) - 8, max(start[0], end[0]) + 8
    ia = fig.add_axes([0.795, 0.60, 0.19, 0.30], projection=PC)
    ia.set_extent((lo0, lo1, la0, la1), crs=PC)
    if 500 in F["p"]:
        ia.contour(F["lons"], F["lats"], F["p"][500]["gh"], levels=np.arange(4800, 6000, 60),
                   colors="#888888", linewidths=0.6, transform=PC)
    ia.coastlines(resolution="50m", linewidth=0.6)
    ia.plot(S["plon"], S["plat"], c="r", lw=1.6, transform=PC)
    ia.scatter([start[1], end[1]], [start[0], end[0]], c="k", s=10, zorder=5, transform=PC)

    valid = init + timedelta(hours=ft)
    fig.text(0.075, 0.955, name or f"断面 ({start[0]:g}N,{start[1]:g}E)→({end[0]:g}N,{end[1]:g}E)",
             fontsize=13, family=["Hiragino Sans", "Noto Sans CJK JP", "Arial Unicode MS", "DejaVu Sans"], va="center")
    fig.text(0.775, 0.955, f"{valid:%m/%d %H}UTC", fontsize=13, family="serif", color="#5a3410",
             va="center", ha="right")
    fig.text(0.006, 0.012, f"{model} INIT:{init:%m/%d %H:%M}(UTC)".strip(), fontsize=9, va="bottom")
    nc._caption(fig, [(f"{init:%d%H}", "#1010e0", 16), (f"<{ft}>", "#d020d0", 16),
                      ("CrossSection", "#7a5a30", 16)], y=0.045, fw=W, fh=H)
    fig.savefig(outpath, dpi=nc.DPI)
    plt.close(fig)
    if quantize:
        nc._quantize_png(outpath)
