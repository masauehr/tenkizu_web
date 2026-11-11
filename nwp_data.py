#!/usr/bin/env python
# coding: utf-8
"""ECMWF オープンデータ / NOAA GFS（ともに 0.25°）の取得と読み込み。

どちらも「1予報時間=1ファイル」で全変数入りの大きなGRIB2（ECMWF 約100MB、GFS 約540MB）だが、
索引ファイル（ECMWF: .index、GFS: .idx）から必要な変数のバイト範囲だけを HTTP Range で取得できる。
必要な変数だけなら ECMWF 約22MB、GFS 約40MB/ステップ。取得した小さなGRIB2は作図後に捨ててよい
（サーバーに残すのは完成したPNGだけ）。認証は不要。

  ECMWF: https://data.ecmwf.int/forecasts/{YYYYMMDD}/{HH}z/ifs/0p25/oper/{YYYYMMDDHH}0000-{step}h-oper-fc.{grib2,index}
         利用条件: CC-BY-4.0（出典明記）。00/12UTCは360hまで、06/18UTCは90hまで。
  AIFS : 同じ構造で ifs → aifs-single（ECMWFのAI予報モデル。00/06/12/18UTC、6時間刻みで360hまで）。
         相対湿度 r が無く比湿 q があるため、読み込み時に q から相対湿度を計算する。ライセンスはIFSと同じCC-BY-4.0。
  GFS  : https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}{,.idx}
         NOAAの成果物（米国政府、出典明記を推奨）。

load_fields() は gsm_chart_web と同じ形式の辞書を返す（描画側が同じコードで動く）。
"""
import json
import os
import re
import sys
import time

_proj = os.path.join(sys.prefix, "share", "proj")
if os.path.isdir(_proj):
    os.environ.setdefault("PROJ_LIB", _proj)

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NWP-Chart-Web/0.1)"}
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"

MODELS = {
    # publish_hours / publish_keep: 公開する初期時刻（UTC）と残す個数（公開サイトは1GB以内が目安。約2日分）
    "ecmwf": dict(label="ECMWF", publish_hours=(0, 12, 18), publish_keep=5),   # 2026-11-10: 6→5（jet200・t300を足す分の容量確保）
    "gfs": dict(label="GFS", publish_hours=(0, 12), publish_keep=4),
    # 気象庁GSM（京大RISH配信）。**ローカル専用**: 公開サイト（gh-pages）には載せない。publish.py と CI の対象から除外する
    "gsm": dict(label="JMA GSM", local_only=True),
}


def public_models():
    """公開サイトに載せてよいモデル（local_only でないもの）。"""
    return [m for m, v in MODELS.items() if not v.get("local_only")]


def auto_models():
    """自動更新(update_latest)の対象モデル（auto=False のもの＝JRA-55 などは除く）。"""
    return [m for m, v in MODELS.items() if v.get("auto", True)]


def default_interval(model: str) -> int:
    """モデルごとの既定のFT間隔[h]。GSMは日本域0.125°が3時間毎のため3、他は6。"""
    return 3 if model == "gsm" else 6

# ECMWFのオープンデータ配信（索引は .index、積算降水 tp は前ステップとの差分、雲量は0-1）の形式を持つモデル → 配信元のパスのモデル名
ECMWF_LIKE = {"ecmwf": "ifs"}
# 積算降水 tp を mm にする係数。IFSは単位が m、AIFSは kg/m²(=mm)（実データの units 属性で確認。2026-11-04）
TP_TO_MM = {"ecmwf": 1000.0}

CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
ECMWF_TERMS_URL = "https://apps.ecmwf.int/datasets/licences/general/"
GFS_REGISTRY_URL = "https://registry.opendata.aws/noaa-gfs-bdp-pds/"


def legal_text(model: str, year: int, accessed: str) -> dict:
    """出典・ライセンス・免責・改変の表示文言を返す（配信元の利用条件に沿った内容）。

    short : 画面に常時出す短い表示（出典・ライセンス・改変・免責を各1文）
    notice: 詳細（段落のリスト）
    根拠: ECMWF = CC BY 4.0 + ECMWF Terms of Use（著作権表示・出典・ライセンス・免責・改変の明示が必要）
          NOAA GFS = AWS Open Data の注記（出典の要請、改変物を原データと示さない、NOAA/NWSの推奨を示唆しない）
    規約は変わり得るため、公開を続ける間は定期的に配信元で再確認すること。
    """
    if model in ECMWF_LIKE:
        what = "IFS open data" if model == "ecmwf" else "AIFS open data (ECMWF's machine-learning forecast model)"
        short = (f"© {year} ECMWF (www.ecmwf.int). This service is based on data and products of the European Centre "
                 f"for Medium-Range Weather Forecasts (ECMWF), {what}, licensed under CC BY 4.0. "
                 "Modified: the data were processed into charts. "
                 "ECMWF does not accept any liability for any error or omission in the data.")
        notice = [
            f"Copyright © {year} European Centre for Medium-Range Weather Forecasts (ECMWF). Source: www.ecmwf.int.",
            "Licence: Creative Commons Attribution 4.0 International (CC BY 4.0), " + CC_BY_URL
            + " （ECMWF Terms of Use: " + ECMWF_TERMS_URL + "）",
            "This service is based on data and products of the European Centre for Medium-Range Weather Forecasts (ECMWF) "
            f"({what}, 0.25°).",
            "Modification: the original data were modified — only selected variables were used and they were processed "
            "(interpolated, derived quantities computed) into the charts shown here. These charts are not ECMWF products.",
            "Disclaimer: ECMWF does not accept any liability whatsoever for any error or omission in the data, their "
            "availability, or for any loss or damage arising from their use. The data are provided on an \"as is\" basis.",
        ]
        return dict(short=short, notice=notice, license_url=CC_BY_URL)
    if model == "gsm":
        short = ("気象庁 全球モデル(GSM) GPV（京都大学生存圏研究所 RISH 配信）を加工して作図。気象庁の公式な図ではありません。"
                 "本アプリはローカルで動かす調査用で、この図は公開サイトには含まれません。データの利用条件は配信元（RISH・気象庁）に従ってください。")
        notice = [
            "データ: 気象庁 全球モデル(GSM) GPV。京都大学生存圏研究所 (RISH) のアーカイブ "
            "http://database.rish.kyoto-u.ac.jp/arch/jmadata/ から取得。",
            "改変: 元データの一部の変数を使い、本アプリが加工して図にしたもの。気象庁の公式な図ではありません。",
            "このモデルの図はローカル（調査用）専用で、公開サイトには載せません。再配布・公開する場合は、配信元（RISH・気象庁）の利用条件を確認してください。",
            "免責: 図の内容・利用による損害について作成者は責任を負いません。防災・安全に関わる判断には気象庁の公式情報を使ってください。",
        ]
        return dict(short=short, notice=notice, license_url="http://database.rish.kyoto-u.ac.jp/arch/jmadata/")
    short = (f"NOAA/NCEP Global Forecast System (GFS), accessed {accessed} from registry.opendata.aws/noaa-gfs-bdp-pds. "
             "Modified: the data were processed into charts and are not original NOAA data. "
             "Not endorsed by or affiliated with NOAA/NWS. Provided as is; use at your own risk.")
    notice = [
        f"NOAA/NCEP Global Forecast System (GFS) 0.25° was accessed on {accessed} from {GFS_REGISTRY_URL}",
        "Modification: the original data were modified — only selected variables were used and they were processed "
        "(derived quantities computed) into the charts shown here. These charts are not original, unaltered NOAA data "
        "and are not official government materials.",
        "This site is not endorsed by, and is not affiliated with, NOAA or the National Weather Service. "
        "NOAA/NWS names and logos are not used.",
        "Disclaimer: the information is provided \"as is\" without warranty of any kind; the user assumes the entire risk "
        "of its use.",
    ]
    return dict(short=short, notice=notice, license_url=GFS_REGISTRY_URL)


ECM_BASE = "https://data.ecmwf.int/forecasts"
GFS_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"

PLEVELS = (1000, 975, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100)  # 存在する面だけが使われる（ECMWFオープンデータに975は無い）。断面図・エマグラムの鉛直解像度のため 2026-11-11 に 1000/600/400/250/150/100 を追加

# 読み込み時に切り出す範囲（全地域の bbox の和集合に余白を足したもの）。全球のまま処理すると遅い
CROP = dict(lon0=0.0, lon1=360.0, lat0=-15.0, lat1=90.0)   # 極座標図のため北半球全体（経度は全周）


def parse_init(text: str) -> datetime:
    return datetime.strptime(text, "%Y%m%d%H").replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ URL
def base_url(model: str, init: datetime, ft: int) -> str:
    """索引・拡張子を除いたURL。"""
    if model in ECMWF_LIKE:
        return (f"{ECM_BASE}/{init:%Y%m%d}/{init:%H}z/{ECMWF_LIKE[model]}/0p25/oper/"
                f"{init:%Y%m%d%H}0000-{ft}h-oper-fc")
    return f"{GFS_BASE}/gfs.{init:%Y%m%d}/{init:%H}/atmos/gfs.t{init:%H}z.pgrb2.0p25.f{ft:03d}"


def index_url(model: str, init: datetime, ft: int) -> str:
    return base_url(model, init, ft) + (".index" if model in ECMWF_LIKE else ".idx")


def data_url(model: str, init: datetime, ft: int) -> str:
    return base_url(model, init, ft) + (".grib2" if model in ECMWF_LIKE else "")


def max_step(model: str, init: datetime) -> int:
    """その初期値で提供される最大予報時間。"""
    if model == "ecmwf":
        return 360 if init.hour in (0, 12) else 90
    if model == "aifs":
        return 360                       # 00/06/12/18UTC とも6時間刻みで360hまで
    return 384


# ------------------------------------------------------------------ 取得可否
def _head_ok(url: str) -> bool:
    try:
        r = requests.head(url, headers=HEADERS, timeout=30, allow_redirects=True)
        return r.status_code == 200
    except requests.RequestException:
        return False


def is_available(model: str, init: datetime, ft: int) -> bool:
    return _head_ok(index_url(model, init, ft)) and _head_ok(data_url(model, init, ft))


def candidate_inits(days_back: int = 3):
    """新しい順の初期値候補（00/06/12/18UTC）。"""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    now -= timedelta(hours=now.hour % 6)
    return [now - timedelta(hours=6 * i) for i in range(days_back * 4 + 1)]


def find_latest_complete(model: str, max_ft: int, interval: int, days_back: int = 3, hours=None):
    """必要な全予報時間が揃っている最新の初期値を返す（公開途中の初期値は避ける）。無ければ None。

    ステップは順に公開されるため、最後の予報時間と最初の予報時間の存在確認で代表させる。
    GSM（RISH）は Rgl・Rjp の全ファイルの存在を一覧から確認する（gsm_source）。
    """
    if model == "gsm":
        import gsm_source
        return gsm_source.find_latest_complete(max_ft, interval, days_back, hours)
    for init in candidate_inits(days_back):
        if hours and init.hour not in hours:
            continue
        if max_ft > max_step(model, init):
            continue
        last = (max_ft // interval) * interval
        if is_available(model, init, last) and is_available(model, init, 0):
            return init
    return None


# ------------------------------------------------------------------ 索引 → バイト範囲
def _ecmwf_rows(model, init, ft):
    r = requests.get(index_url(model, init, ft), headers=HEADERS, timeout=60)
    r.raise_for_status()
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


def _ecmwf_select(rows, model="ecmwf"):
    """必要なメッセージの (offset, length) を返す。AIFSは相対湿度 r が無いので比湿 q も取る。"""
    pl = {str(l) for l in PLEVELS}
    pvars = ("gh", "t", "u", "v", "r", "w") + (("q",) if model == "aifs" else ())
    out = []
    for r in rows:
        p, lt = r["param"], r.get("levtype")
        if lt == "pl" and p in pvars and r.get("levelist") in pl:
            out.append((r["_offset"], r["_length"]))
        elif lt == "sfc" and p in ("msl", "sp", "10u", "10v", "2t", "tcc", "tp"):
            out.append((r["_offset"], r["_length"]))
    return out


def _gfs_select(init, ft):
    r = requests.get(index_url("gfs", init, ft), headers=HEADERS, timeout=60)
    r.raise_for_status()
    lines = r.text.strip().splitlines()
    rows = []
    for i, l in enumerate(lines):
        p = l.split(":")
        off = int(p[1])
        nxt = int(lines[i + 1].split(":")[1]) if i + 1 < len(lines) else None
        rows.append((p[3], p[4], p[5], off, None if nxt is None else nxt - off))
    lv = {f"{x} mb" for x in PLEVELS}
    out, apcp = [], []
    for var, level, desc, off, ln in rows:
        if var in ("HGT", "TMP", "UGRD", "VGRD", "RH", "VVEL") and level in lv:
            out.append((off, ln))
        elif var == "PRMSL" and level == "mean sea level":
            out.append((off, ln))
        elif var in ("TMP", "RH") and level == "2 m above ground":
            out.append((off, ln))
        elif var in ("UGRD", "VGRD") and level == "10 m above ground":
            out.append((off, ln))
        elif var == "PRES" and level == "surface":
            out.append((off, ln))
        elif var == "TCDC" and level == "entire atmosphere" and "ave" not in desc and "-" not in desc:
            out.append((off, ln))
        elif var == "APCP":
            m = re.search(r"(\d+)-(\d+) hour acc", desc)
            if m:
                apcp.append((int(m.group(2)) - int(m.group(1)), off, ln))
    if apcp:                                  # 窓が最短の積算（3h窓があれば3h、無ければ6h）を採る
        w, off, ln = sorted(apcp)[0]
        out.append((off, ln))
    return out


def _merge(ranges):
    """隣り合うバイト範囲を結合して Range 要求の回数を減らす。"""
    rs = sorted((o, l) for o, l in ranges)
    merged = []
    for o, l in rs:
        if merged and l is not None and merged[-1][1] is not None and merged[-1][0] + merged[-1][1] == o:
            merged[-1] = (merged[-1][0], merged[-1][1] + l)
        else:
            merged.append((o, l))
    return merged


def _download_ranges(url: str, ranges, dest: Path) -> Path | None:
    """指定バイト範囲を連結して dest に保存（GRIB2はメッセージ連結でそのまま読める）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        with open(tmp, "wb") as f:
            for off, ln in _merge(ranges):
                hdr = dict(HEADERS, Range=f"bytes={off}-{'' if ln is None else off + ln - 1}")
                for attempt in range(3):
                    try:
                        r = requests.get(url, headers=hdr, timeout=120)
                        if r.status_code in (200, 206):
                            f.write(r.content)
                            break
                        raise requests.RequestException(f"HTTP {r.status_code}")
                    except requests.RequestException:
                        if attempt == 2:
                            raise
                        time.sleep(2 * (attempt + 1))
        tmp.rename(dest)
        return dest
    except requests.RequestException as e:
        print(f"  ダウンロード失敗 {dest.name}: {e}")
        tmp.unlink(missing_ok=True)
        return None


def fetch_subset(model: str, init: datetime, ft: int, data_dir: Path = DEFAULT_DATA_DIR) -> Path | None:
    """必要な変数だけを取得して data_dir/{model}/{初期値}/f{FFF}.grib2 に保存。既存ならスキップ。"""
    dest = Path(data_dir) / model / f"{init:%Y%m%d%H}" / f"f{ft:03d}.grib2"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        sel = _ecmwf_select(_ecmwf_rows(model, init, ft), model) if model in ECMWF_LIKE else _gfs_select(init, ft)
    except requests.RequestException as e:
        print(f"  索引の取得に失敗 {model} FT{ft}: {e}")
        return None
    if not sel:
        print(f"  必要な変数が索引に見つかりません {model} FT{ft}")
        return None
    return _download_ranges(data_url(model, init, ft), sel, dest)


def fetch_tp_only(init: datetime, ft: int, data_dir: Path = DEFAULT_DATA_DIR, model: str = "ecmwf") -> Path | None:
    """ECMWF系（IFS/AIFS）の積算降水(tp)だけを取る（直前ステップとの差分で時間降水を作るため。数百KB）。"""
    dest = Path(data_dir) / model / f"{init:%Y%m%d%H}" / f"tp_f{ft:03d}.grib2"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        rows = _ecmwf_rows(model, init, ft)
    except requests.RequestException:
        return None
    sel = [(r["_offset"], r["_length"]) for r in rows if r["param"] == "tp" and r.get("levtype") == "sfc"]
    return _download_ranges(data_url(model, init, ft), sel, dest) if sel else None


# ------------------------------------------------------------------ 読み込み
def _read(path: Path):
    """GRIB2 を走査し、(key→2次元配列, lats, lons) を返す。範囲を CROP に切り出す。"""
    import numpy as np
    import pygrib

    sfc = {("msl", "meanSea"): "msl", ("prmsl", "meanSea"): "msl", ("sp", "surface"): "sp",
           ("10u", "heightAboveGround"): "u10", ("10v", "heightAboveGround"): "v10",
           ("2t", "heightAboveGround"): "t2", ("tcc", "atmosphere"): "tcc", ("tcc", "entireAtmosphere"): "tcc", ("tcc", "surface"): "tcc",
           ("tp", "surface"): "tp"}
    out = {"p": {}}
    lats = lons = None
    ix = iy = None
    grbs = pygrib.open(str(path))
    try:
        for m in grbs:
            sn, tl, lv = m.shortName, m.typeOfLevel, m.level
            key = None
            if tl == "isobaricInhPa" and sn in ("gh", "t", "u", "v", "r", "w", "q") and lv in PLEVELS:
                key = ("p", lv, sn)
            elif tl == "heightAboveGround" and lv == 2 and sn in ("2r",):
                continue
            elif (sn, tl) in sfc:
                key = ("s", sfc[(sn, tl)])
            elif sn == "unknown" and tl == "atmosphere":
                continue
            if key is None:
                # GFSのTCDC(entire atmosphere)・APCP(surface) は shortName が tcc / tp 以外になる場合の保険
                if m.name == "Total Cloud Cover" or sn == "tcc":
                    key = ("s", "tcc")
                elif m.name in ("Total Precipitation", "Total precipitation") or sn in ("tp", "apcp"):
                    key = ("s", "tp")
                else:
                    continue
            if lats is None:
                la, lo = m.latlons()
                la1, lo1 = la[:, 0], lo[0, :]
                # 経度は 0〜360 に正規化して昇順に並べ替える（ECMWF のオープンデータは -180〜180 度で並び、pygrib/eccodes の版によっては
                # そのまま返るため、0〜360 で切り出すと西半球が落ちる。2026-11-11 に公開版の極座標図が半分になって発覚）
                lo1 = np.mod(lo1, 360.0)
                order = np.argsort(lo1, kind="stable")
                iy = np.where((la1 >= CROP["lat0"]) & (la1 <= CROP["lat1"]))[0]
                ix = order[(lo1[order] >= CROP["lon0"]) & (lo1[order] <= CROP["lon1"])]
                lats, lons = la1[iy[0]:iy[-1] + 1], lo1[ix]
            v = np.asarray(m.values, dtype=np.float32)[iy[0]:iy[-1] + 1][:, ix]
            if key[0] == "p":
                out["p"].setdefault(key[1], {})[key[2]] = v
            else:
                out[key[1]] = v
    finally:
        grbs.close()
    out["lats"], out["lons"] = lats, lons
    return out


def load_fields(model: str, path: Path, tp_prev_path: Path | None = None, interval: int = 6,
                tp_prev_zero: bool = False) -> dict:
    """取得済みの小さなGRIB2を gsm_chart_web と同形式の辞書にする。

    tp_prev_path: ECMWFの1つ前の予報時間の積算降水(tp)。tp_prev_zero: 最初の区間なら True（積算0を前とみなす）。
    返り値: {"lats","lons","msl"(hPa),"sp"(hPa),"u10","v10","t2"(℃),"tcc"(%),"rr"(mm/h|None),"rr_h"(降水の平均時間),
             "p": {975..300: {"gh"(m),"t"(℃),"u","v","r"(%),"w"(hPa/h)}}}
    """
    import numpy as np
    d = _read(Path(path))
    F = {"p": {}, "lats": d["lats"], "lons": d["lons"], "rr": None, "rr_h": interval}
    F["msl"] = d["msl"] / 100.0
    F["sp"] = d["sp"] / 100.0
    F["u10"], F["v10"] = d["u10"], d["v10"]
    F["t2"] = d["t2"] - 273.15
    tcc = d.get("tcc")
    if tcc is not None:
        F["tcc"] = tcc * 100.0 if model in ECMWF_LIKE else tcc  # ECMWF/AIFSは0-1、GFSは%
    else:
        F["tcc"] = np.zeros_like(F["msl"])
    tp = d.get("tp")
    if tp is not None:
        if model in ECMWF_LIKE:                               # 初期時刻からの積算（単位は TP_TO_MM で mm へ）
            if tp_prev_path is not None and Path(tp_prev_path).exists():
                pv = _read(Path(tp_prev_path)).get("tp")
                if pv is not None:
                    F["rr"] = ((tp - pv) * TP_TO_MM[model] / interval).clip(min=0)
            elif tp_prev_zero:                                 # 最初の区間（FT=interval）は前の積算が0
                F["rr"] = (tp * TP_TO_MM[model] / interval).clip(min=0)
        else:                                                  # GFS: 窓付き積算 [kg/m2=mm]。窓幅は _gfs_select が最短を選ぶ
            F["rr"] = (tp / _gfs_window(Path(path))).clip(min=0)
            F["rr_h"] = _gfs_window(Path(path))
    for lv, dd in d["p"].items():
        if not all(k in dd for k in ("gh", "t", "u", "v")):
            continue
        z = {"gh": dd["gh"], "t": dd["t"] - 273.15, "u": dd["u"], "v": dd["v"]}
        if "r" in dd:
            z["r"] = dd["r"]
        elif "q" in dd:                                       # AIFS: 比湿から相対湿度を計算
            z["r"] = _rh_from_q(dd["q"], dd["t"] - 273.15, float(lv))
        else:
            z["r"] = np.full_like(dd["t"], 50.0)
        z["w"] = dd["w"] * 36.0 if "w" in dd else np.zeros_like(dd["t"])   # Pa/s → hPa/h
        F["p"][lv] = z
    if F["lats"][0] > F["lats"][-1]:       # 緯度を昇順（南→北）へ
        F["lats"] = F["lats"][::-1]
        for k in ("msl", "sp", "u10", "v10", "t2", "tcc", "rr"):
            if F.get(k) is not None:
                F[k] = F[k][::-1, :]
        for z in F["p"].values():
            for k in z:
                z[k] = z[k][::-1, :]
    return F


def _rh_from_q(q, t_c, p_hpa: float):
    """比湿 q[kg/kg]・気温[℃]・気圧[hPa] → 相対湿度[%]。AIFS（相対湿度が無い）用。
    e = q·p / (0.622 + 0.378·q)、飽和水蒸気圧は Bolton(1980) の式 es = 6.112·exp(17.67·T/(T+243.5))。"""
    import numpy as np
    q = np.clip(q, 0.0, None)
    e = q * p_hpa / (0.622 + 0.378 * q)
    es = 6.112 * np.exp(17.67 * t_c / (t_c + 243.5))
    return np.clip(100.0 * e / es, 0.0, 100.0).astype(np.float32)


def _gfs_window(path: Path) -> int:
    """GFSのAPCPメッセージの積算窓幅[h]を読む（取得時に最短窓を選んでいる）。"""
    import pygrib
    g = pygrib.open(str(path))
    try:
        for m in g:
            if m.shortName in ("tp", "apcp") or m.name in ("Total Precipitation", "Total precipitation"):
                return max(1, int(m.endStep) - int(m.startStep))
    finally:
        g.close()
    return 6
