#!/usr/bin/env python
# coding: utf-8
"""実況の高層ゾンデ（ワイオミング大学）から、エマグラム・温位エマグラムを作る。

tenkizu の emagram.py（CLI・siphon 使用）の描画を移植して調整したもの（共通部品化しない）。
siphon が使っていた旧い URL（weather.arcc.uwyo.edu/cgi-bin/sounding）は 2026 年に 404 になったため、
現在の https://weather.uwyo.edu/wsgi/sounding を直接読む。応答は HTML の <PRE> の中の固定幅の表
（列: PRES HGHT TEMP DWPT RELH MIXR DRCT SPED THTA THTE THTV。各7桁。風速 SPED は m/s）。

  - 取得: fetch(dt, wmo)。その時刻・地点のデータが無ければ None（欠測・まだ公開前・未収録）。ネットワークの異常は SoundingError
  - 描画: render(d, dt, wmo, mode, outpath)。mode は emagram（気温・露点・風・持ち上げ気塊・CAPE/CIN・ホドグラフ）か pt（温位エマグラム）
  - 公開用の一括作成は update_sounding.py。ローカルで任意の地点・時刻は render_custom()（server.py の /api/sounding）

データ: University of Wyoming, Department of Atmospheric Science（weather.uwyo.edu）。出典を図とページに表示する。
アクセスは控えめに（間隔を空け、作成済みは取りにいかない）。利用条件は変わり得るので、公開を続ける間は配信元で確認すること。
"""
import json
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests

ROOT = Path(__file__).resolve().parent
SOUNDING_DIR = ROOT / "output" / "sounding"             # 公開する実況（update_sounding.py が作る）
CUSTOM_DIR = ROOT / "output" / "sounding_custom"        # ローカルで任意に作った分（公開しない）
URL = "https://weather.uwyo.edu/wsgi/sounding"
HEADERS = {"User-Agent": "tenkizu_web/1.0 (+https://github.com/masauehr/tenkizu_web; personal weather viewer)"}
MODES = ("emagram", "pt")
MODE_LABEL = {"emagram": "エマグラム", "pt": "温位エマグラム"}

# 取得できる日本の高層気象観測所（WMO番号: (名前, 地域)）。那覇（47936）・台湾（花蓮 46699・台北 46692）は
# ワイオミング大学に無い（2026-11-12 に確認。404）。
STATIONS = {
    "47401": ("稚内", "北海道"),
    "47582": ("秋田", "東北"),
    "47600": ("輪島", "北陸"),
    "47646": ("館野", "関東"),
    "47678": ("八丈島", "関東"),
    "47778": ("潮岬", "近畿"),
    "47807": ("福岡", "九州"),
    "47827": ("鹿児島", "九州"),
    "47909": ("名瀬", "南西諸島"),
    "47918": ("石垣島", "南西諸島"),
    "47945": ("南大東島", "南西諸島"),
}

LEGAL = ("高層気象観測（ラジオゾンデ）のデータ: University of Wyoming, Department of Atmospheric Science "
         "（weather.uwyo.edu）。観測値をそのまま図にしたもので、気象庁の公式な図ではありません。")

JP_FONTS = ["Hiragino Sans", "Noto Sans CJK JP", "IPAGothic", "IPAexGothic", "Arial Unicode MS", "DejaVu Sans"]
matplotlib.rcParams["font.family"] = JP_FONTS


class SoundingError(RuntimeError):
    """取得・読み取りの失敗（ネットワークの異常・形式の変化・データ不足）。メッセージは画面にそのまま出せる。"""


# ------------------------------------------------------------------ 観測時刻
def obs_times(now=None, count=6, min_age_hours=2):
    """新しい順の観測時刻（UTC・00/12UTC）。観測から min_age_hours 経っていないものは含めない（公開前のことが多い）。"""
    now = now or datetime.now(timezone.utc)
    t = (now - timedelta(hours=min_age_hours)).replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % 12)
    return [t - timedelta(hours=12 * i) for i in range(count)]


# ------------------------------------------------------------------ 取得・読み取り
def _cells(line):
    """固定幅（7桁×11列）の1行を数値にする。空欄は NaN。"""
    out = []
    for i in range(11):
        c = line[i * 7:(i + 1) * 7].strip()
        try:
            out.append(float(c))
        except ValueError:
            out.append(np.nan)
    return out


def parse_sounding(html):
    """応答の HTML を読む。返り値: dict(p, h, T, Td, drct, sped, name, lat, lon)（配列は numpy・欠測は NaN）。形式が違えば SoundingError。"""
    m = re.search(r"<pre>(.*?)</pre>", html, re.I | re.S)
    if not m:
        raise SoundingError("応答に観測値の表（PRE）がありません（配信元の形式が変わった可能性があります）")
    rows = [_cells(l) for l in m.group(1).split("\n") if re.match(r"\s*\d", l)]
    if len(rows) < 10:
        raise SoundingError(f"観測値の行が少なすぎます（{len(rows)}行）")
    a = np.array(rows, dtype=float)
    name = re.search(r"<h3>(.*?)</h3>", html, re.I | re.S)
    ll = re.search(r"Latitude:\s*(-?[\d.]+)\s*Longitude:\s*(-?[\d.]+)", html)
    d = dict(p=a[:, 0], h=a[:, 1], T=a[:, 2], Td=a[:, 3], drct=a[:, 6], sped=a[:, 7],
             name=(name.group(1).strip() if name else ""), lat=float(ll.group(1)) if ll else None, lon=float(ll.group(2)) if ll else None)
    ok = np.isfinite(d["p"]) & np.isfinite(d["T"])
    if ok.sum() < 10 or not np.nanmax(d["p"][ok]) >= 800:
        raise SoundingError("気温の観測値が足りません（地上付近のデータが無い）")
    return d


def fetch(dt, wmo, session=None, retries=2, wait=3.0):
    """指定時刻・地点の観測を取得して parse_sounding の結果を返す。データが無ければ None。ネットワークの異常は SoundingError。"""
    s = session or requests
    last = None
    for k in range(retries + 1):
        try:
            r = s.get(URL, params={"datetime": f"{dt:%Y-%m-%d %H}:00:00", "id": wmo, "src": "UNKNOWN", "type": "TEXT:LIST"},
                      headers=HEADERS, timeout=45)
        except requests.RequestException as e:
            last = f"接続できません: {e.__class__.__name__}"
        else:
            if r.status_code in (400, 404) and "Unable to retrieve" in r.text:
                return None                                   # その時刻・地点のデータが無い（欠測・まだ公開前・未収録）
            if r.status_code == 200:
                return parse_sounding(r.text)
            last = f"HTTP {r.status_code}"                    # 503 など一時的な異常は取り直す
        if k < retries:
            time.sleep(wait * (k + 1))
    raise SoundingError(f"ワイオミング大学から取得できません（{last}）")


# ------------------------------------------------------------------ 描画
def _profile(d):
    """描画用に整える: 気圧の降順・重複なし・気温のある行（熱力学）と、風のある行（風・ホドグラフ）。"""
    ok = np.isfinite(d["p"]) & np.isfinite(d["T"])
    p, T, Td, h = d["p"][ok], d["T"][ok], d["Td"][ok], d["h"][ok]
    order = np.argsort(-p, kind="stable")
    p, T, Td, h = p[order], T[order], Td[order], h[order]
    keep = np.concatenate([[True], np.diff(p) < 0])
    p, T, Td, h = p[keep], T[keep], Td[keep], h[keep]
    w = np.isfinite(d["p"]) & np.isfinite(d["drct"]) & np.isfinite(d["sped"]) & np.isfinite(d["h"])
    ow = np.argsort(-d["p"][w], kind="stable")
    wp, wh, wd, ws = d["p"][w][ow], d["h"][w][ow], d["drct"][w][ow], d["sped"][w][ow]
    return p, T, Td, h, wp, wh, wd, ws


def _quantize(path):
    """PNGを256色パレットに減色して容量を小さくする（線画・塗り主体なので見た目はほぼ変わらない）。"""
    try:
        from PIL import Image
    except ImportError:
        return
    im = Image.open(path).convert("RGB")
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)


def _plot_mixing_lines_pt(self, mixing_ratio=None, pressure=None, **kwargs):
    """SkewT に温位エマグラム用の等飽和混合比曲線（横軸＝温位）を足す（tenkizu/emagram.py と同じ）。"""
    import metpy.calc as mpcalc
    from matplotlib.collections import LineCollection
    from metpy.units import units
    if self.mixing_lines:
        self.mixing_lines.remove()
    if mixing_ratio is None:
        mixing_ratio = np.array([0.0004, 0.001, 0.002, 0.004, 0.007, 0.01, 0.016, 0.024, 0.032]).reshape(-1, 1)
    if pressure is None:
        pressure = units.Quantity(np.linspace(600, max(self.ax.get_ylim())), "mbar")
    td = mpcalc.dewpoint(mpcalc.vapor_pressure(pressure, mixing_ratio))
    pt = mpcalc.potential_temperature(pressure, td).to(units.degree_Celsius)
    linedata = [np.vstack((t.m, pressure.m)).T for t in pt]
    kwargs.setdefault("colors", "g")
    kwargs.setdefault("linestyles", "dashed")
    kwargs.setdefault("alpha", 0.8)
    self.mixing_lines = self.ax.add_collection(LineCollection(linedata, **kwargs))
    return self.mixing_lines


def _draw_emagram(fig, prof, dt, label):
    import metpy.calc as mpcalc
    from metpy.plots import Hodograph, SkewT
    from metpy.units import units
    from mpl_toolkits.axes_grid1.inset_locator import inset_axes
    p, T, Td, h, wp, wh, wd, ws = prof
    pq, Tq = p * units.hPa, T * units.degC
    skew = SkewT(fig, rotation=0, aspect=120)
    skew.ax.set_xlim(-70, 40)
    skew.ax.set_ylim(max(1020.0, float(p[0]) + 5.0), 100)
    skew.ax.set_xlabel("気温 (℃)")
    skew.ax.set_ylabel("気圧 (hPa)")
    skew.plot_dry_adiabats(t0=np.arange(203, 533, 10) * units.K, alpha=0.25, color="orangered")
    skew.plot_moist_adiabats(t0=np.arange(203, 400, 5) * units.K, alpha=0.25, color="tab:green")
    skew.plot_mixing_lines(pressure=np.arange(1000, 99, -20) * units.hPa, linestyle="dotted", color="tab:blue")
    skew.plot(pq, Tq, "r", linewidth=1.8, label="気温")
    tdok = np.isfinite(Td)
    if tdok.sum() >= 2:
        skew.plot(pq[tdok], Td[tdok] * units.degC, "g", linewidth=1.8, label="露点温度")
    note = ""
    # 850hPa以下で最大相当温位の高さの気塊を持ち上げる。露点の無い高さ（上層）は気温と同じ（飽和）として扱う（気塊の診断に影響しない高さ）
    try:
        Tdf = np.where(tdok, Td, T)
        Tdq = Tdf * units.degC
        i85 = next((i for i, pi in enumerate(p) if pi <= 850.0), len(p) - 1)
        ept = mpcalc.equivalent_potential_temperature(pq[:i85 + 1], Tq[:i85 + 1], Tdq[:i85 + 1])
        i0 = int(np.nanargmax(ept.magnitude))
        parcel = mpcalc.parcel_profile(pq[i0:], Tq[i0], Tdq[i0]).to("degC")
        lcl_p, lcl_t = mpcalc.lcl(pq[i0], Tq[i0], Tdq[i0])
        skew.plot(pq[i0:], parcel, "k", linewidth=2, alpha=0.55, label="持ち上げ気塊")
        skew.plot(pq[i0], Tq[i0], "ko", markerfacecolor="black")
        skew.plot(lcl_p, lcl_t, "ko", markerfacecolor="blue")
        cape, cin = mpcalc.cape_cin(pq[i0:], Tq[i0:], Tdq[i0:], parcel)
        if float(cape.magnitude) > 0:        # 自由対流高度が無い（安定）ときは塗らない（CIN の塗りが広範囲になり誤解を招く）
            skew.shade_cin(pq[i0:], Tq[i0:], parcel, Tdq[i0:])
            skew.shade_cape(pq[i0:], Tq[i0:], parcel)
        note = (f"Parcel start: {float(p[i0]):.0f} hPa\nLCL: {float(lcl_p.magnitude):.0f} hPa\n"
                f"CAPE: {float(cape.magnitude):.0f} J/kg\nCIN: {float(cin.magnitude):.0f} J/kg")
    except Exception:
        note = "（持ち上げ気塊の診断は計算できませんでした）"
    if len(wp):
        u, v = mpcalc.wind_components(ws * units("m/s"), wd * units.degree)
        skew.plot_barbs(wp * units.hPa, u, v, y_clip_radius=0.03)
    skew.ax.legend(loc="lower left", bbox_to_anchor=(0.02, 0.17), fontsize=8, framealpha=0.85, prop={"family": JP_FONTS, "size": 8})
    skew.ax.text(0.02, 0.02, note, transform=skew.ax.transAxes, fontsize=8, va="bottom", ha="left",
                 bbox={"boxstyle": "round,pad=0.3", "fc": "white", "alpha": 0.8})
    if len(wp) >= 2:
        ax_hod = inset_axes(skew.ax, "40%", "40%", loc=1)
        hd = Hodograph(ax_hod, component_range=80.0)
        hd.add_grid(increment=20)
        hd.plot_colormapped(u, v, wh * units.m)
    skew.ax.set_title(f"{label}  Emagram", loc="left")
    skew.ax.set_title(f"{dt:%Y-%m-%d %H}UTC", loc="right")
    return skew


def _draw_pt(fig, prof, dt, label):
    import metpy.calc as mpcalc
    import metpy.plots
    from metpy.plots import SkewT
    from metpy.units import units
    metpy.plots.SkewT.plot_mixing_lines_pt = _plot_mixing_lines_pt
    p, T, Td, h, wp, wh, wd, ws = prof
    pq, Tq = p * units.hPa, T * units.degC
    tdok = np.isfinite(Td)
    # 横軸は「θ[K] − 273.15」の数値で扱う（目盛りの表示だけケルビンにする）。単位つきで軸を決めると、MetPy の版によって範囲が壊れる
    K0 = 273.15
    pt = mpcalc.potential_temperature(pq, Tq).to("K").magnitude - K0
    sept = mpcalc.saturation_equivalent_potential_temperature(pq, Tq).to("K").magnitude - K0
    has_td = tdok.sum() >= 2                                 # 露点が（ほとんど）無い観測では、露点を使う θe・θw は描かない
    spt = mpcalc.potential_temperature(pq[tdok], Td[tdok] * units.degC).to("K").magnitude - K0 if has_td else np.array([])
    ept = mpcalc.equivalent_potential_temperature(pq[tdok], Tq[tdok], Td[tdok] * units.degC).to("K").magnitude - K0 if has_td else np.array([])
    shown = p >= 100.0                                       # 表示するのは 100hPa より下だけ。それより上（成層圏）は温位が700K台になるので、範囲の決定に含めない
    vals = np.concatenate([pt[shown], spt[shown[tdok]] if has_td else [], ept[shown[tdok]] if has_td else [], sept[shown]]) + K0
    vals = vals[np.isfinite(vals)]
    lo = int(np.floor((vals.min() - 5) / 10) * 10)           # 全プロファイルの値から横軸の範囲を決める（10K単位・5Kマージン）
    hi = int(np.ceil((vals.max() + 5) / 10) * 10)
    skew = SkewT(fig, rotation=0, aspect=120)
    skew.ax.set_xlim(lo - K0, hi - K0)
    skew.ax.set_ylim(max(1020.0, float(p[0]) + 5.0), 100)
    skew.ax.set_xticks(np.arange(lo, hi + 1, 10) - K0)
    skew.ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: "%d" % round(x + K0)))
    skew.ax.set_xlabel("温位 (K)")
    skew.ax.set_ylabel("気圧 (hPa)")
    mr = np.array([0.0004, 0.001, 0.002, 0.004, 0.007, 0.01, 0.016, 0.024, 0.032]).reshape(-1, 1)
    skew.plot_mixing_lines_pt(pressure=np.arange(1000, 99, -20) * units.hPa, mixing_ratio=mr, linestyle="dotted", color="tab:blue")
    skew.plot(p, pt, "black", label="θ (温位)")
    if has_td:
        skew.plot(p[tdok], ept, "g", label="θe (相当温位)")
    skew.plot(p, sept, "r", label="θes (飽和相当温位)")
    if has_td:
        skew.plot(p[tdok], spt, "purple", label="θw (飽和温位)")
    skew.ax.legend(loc="upper right", fontsize=8, prop={"family": JP_FONTS, "size": 8})
    if len(wp):
        u, v = mpcalc.wind_components(ws * units("m/s"), wd * units.degree)
        skew.plot_barbs(wp * units.hPa, u, v, y_clip_radius=0.03)
    skew.ax.set_title(f"{label}  PT-Emagram", loc="left")
    skew.ax.set_title(f"{dt:%Y-%m-%d %H}UTC", loc="right")
    return skew


def station_label(wmo, d=None):
    """図の題名の地点名。登録した地点は日本語名、それ以外は配信元の名前。"""
    if wmo in STATIONS:
        return f"{STATIONS[wmo][0]}（{wmo}）"
    nm = (d or {}).get("name") or ""
    return f"{nm}（{wmo}）" if nm else str(wmo)


def render(d, dt, wmo, mode, outpath):
    """観測 d（parse_sounding の結果）から図を1枚作って outpath に保存する。mode: emagram / pt。"""
    if mode not in MODES:
        raise ValueError(f"mode は {MODES} のどちらかです")
    prof = _profile(d)
    if len(prof[0]) < 10:
        raise SoundingError("気温の観測値が足りません")
    fig = plt.figure(figsize=(7, 12))
    try:
        skew = (_draw_emagram if mode == "emagram" else _draw_pt)(fig, prof, dt, station_label(wmo, d))
        skew.ax.text(0.0, -0.075, "Data: University of Wyoming (weather.uwyo.edu)", transform=skew.ax.transAxes,
                     fontsize=7, color="#555555", va="top", ha="left")
        outpath = Path(outpath)
        outpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = outpath.with_suffix(".tmp.png")
        fig.savefig(tmp, dpi=100, bbox_inches="tight")
    finally:
        plt.close(fig)
    _quantize(tmp)
    tmp.replace(outpath)
    return outpath


# ------------------------------------------------------------------ 公開用の一覧（manifest）と整理
def _manifest_body(root):
    times = []
    for td in sorted((d for d in Path(root).iterdir() if d.is_dir() and re.fullmatch(r"\d{10}", d.name)), reverse=True) if Path(root).is_dir() else []:
        sts = {}
        for f in sorted(td.glob("*_*.png")):
            m = re.fullmatch(r"(\d{5})_(emagram|pt)\.png", f.name)
            if m:
                sts.setdefault(m.group(1), []).append(m.group(2))
        if sts:
            times.append({"time": td.name, "stations": {k: sorted(v, key=MODES.index) for k, v in sorted(sts.items())}})
    return {"legal": LEGAL, "stations": [{"id": k, "name": v[0], "region": v[1]} for k, v in STATIONS.items()], "times": times}


def write_manifest(root=SOUNDING_DIR):
    """root の画像を走査して manifest.json を作る。中身が前回と同じなら更新しない（無駄な公開コミットを避ける）。変更したら True。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    body = _manifest_body(root)
    mp = root / "manifest.json"
    try:
        old = json.loads(mp.read_text(encoding="utf-8"))
        old.pop("updated", None)
        if old == body:
            return False
    except (OSError, ValueError):
        pass
    mp.write_text(json.dumps({**body, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, ensure_ascii=False), encoding="utf-8")
    return True


def prune(root, keep):
    """新しい観測時刻を keep 個だけ残して、古い時刻のフォルダを消す。消した時刻の一覧を返す。"""
    root = Path(root)
    if not root.is_dir():
        return []
    names = sorted((d.name for d in root.iterdir() if d.is_dir() and re.fullmatch(r"\d{10}", d.name)), reverse=True)
    gone = names[keep:]
    for n in gone:
        shutil.rmtree(root / n)
    return gone


# ------------------------------------------------------------------ ローカルで任意の地点・時刻（server.py の /api/sounding）
def validate_request(wmo, time_s, mode, now=None):
    """入力を検証して (wmo, dt, mode) を返す。不正なら ValueError（メッセージは画面に出せる）。"""
    if not re.fullmatch(r"\d{5}", wmo or ""):
        raise ValueError("地点は WMO 地点番号（5桁の数字。例: 47918）で指定してください")
    if not re.fullmatch(r"\d{10}", time_s or ""):
        raise ValueError("観測時刻は YYYYMMDDHH（UTC）で指定してください")
    try:
        dt = datetime.strptime(time_s, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("観測時刻が日時として不正です")
    if dt.hour not in (0, 6, 12, 18):
        raise ValueError("観測時刻は 00/06/12/18UTC のいずれかです（ふつうの観測は 00 と 12UTC）")
    if dt > (now or datetime.now(timezone.utc)):
        raise ValueError("観測時刻が未来です")
    if mode not in MODES:
        raise ValueError("図は emagram（エマグラム）か pt（温位エマグラム）です")
    return wmo, dt, mode


def render_custom(wmo, dt, mode, out_dir=CUSTOM_DIR, session=None):
    """任意の地点・時刻の図を作る（作成済みならそれを返す）。データが無ければ SoundingError。"""
    out = Path(out_dir) / f"{dt:%Y%m%d%H}_{wmo}_{mode}.png"
    if out.exists():
        return out
    d = fetch(dt, wmo, session)
    if d is None:
        raise SoundingError(f"{wmo} の {dt:%Y-%m-%d %H}UTC の観測データがありません"
                            "（欠測、まだ公開前、またはワイオミング大学に無い地点です）")
    return render(d, dt, wmo, mode, out)
