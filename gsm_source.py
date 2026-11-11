#!/usr/bin/env python
# coding: utf-8
"""気象庁 全球モデル(GSM)の取得（京都大学 RISH）と読み込み。**ローカル専用モデル**用。

gsm_chart_web の gsm_data.py をコピーして nwp_chart_web 用に調整したもの（共通部品化しない方針）。
GSMのデータ・作図画像は公開サイトには載せない（nwp_data.MODELS の local_only。publish.py が除外する）。

データ提供元: 京都大学生存圏研究所 (RISH) 気象庁データアーカイブ
  http://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original/
2系統（ECMWF/GFSと違い、1ファイル全体を取得する。索引による部分取得は無い）:
  Rgl（全球 0.5°・6時間毎）: Z__C_RJTD_{YYYYMMDDHH}0000_GSM_GPV_Rgl_FD{DDHH}_grib2.bin（1ファイル=1予報時間、約33MB）
  Rjp（日本域 0.125°・3時間毎）: ..._GSM_GPV_Rjp_Gll0p1deg_{L-pall|Lsurf}_FD{開始}-{終了}_grib2.bin
    24時間分が1ファイル（等圧面約40MB＋地上約18MB）。975hPaを含む。範囲は120-150E, 20-50N。
取得したファイルは data/gsm/ に**残す**（Rjpは複数のFTで共有されるため。ECMWF/GFSのように作図後に削除しない）。
"""
import os
import re
import sys

# pygrib/cartopy より前に PROJ のデータ場所を指定（conda 環境で必要になることがある）
_proj = os.path.join(sys.prefix, "share", "proj")
if os.path.isdir(_proj):
    os.environ.setdefault("PROJ_LIB", _proj)

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_URL = "http://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NWP-Chart-Web/0.2)"}
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data" / "gsm"
import threading
_LOCKS = {}                 # 同じファイルを並行スレッドが二重に取得しないための排他（ファイル名ごと）
_LOCKS_GUARD = threading.Lock()

# GSM(Rgl) の予報時間（時間）。0〜84h は6時間間隔（00/12UTC初期値は〜264hまで12h間隔）
FT_STEP = 6


def ft_to_ddhh(ft: int) -> int:
    """予報時間[h] → FD の DDHH 数値（例: 18h→18, 24h→100, 36h→112）"""
    return (ft // 24) * 100 + (ft % 24)


def ddhh_to_ft(ddhh: int) -> int:
    return (ddhh // 100) * 24 + (ddhh % 100)


def gsm_filename(init: datetime, ft: int) -> str:
    return (f"Z__C_RJTD_{init:%Y%m%d%H}0000_GSM_GPV_Rgl_"
            f"FD{ft_to_ddhh(ft):04d}_grib2.bin")


def parse_init(text: str) -> datetime:
    """YYYYMMDDHH（UTC）→ datetime"""
    return datetime.strptime(text, "%Y%m%d%H").replace(tzinfo=timezone.utc)


RJP_RE = re.compile(r"Z__C_RJTD_(\d{10})0000_GSM_GPV_Rjp_Gll0p1deg_(L-pall|Lsurf)_"
                    r"FD(\d{4})-(\d{4})_grib2\.bin")


def rjp_range(name: str):
    """Rjpファイル名 → (初期値, 種別, 開始FT, 終了FT)。該当しなければ None。"""
    m = RJP_RE.fullmatch(name)
    if not m:
        return None
    return m.group(1), m.group(2), ddhh_to_ft(int(m.group(3))), ddhh_to_ft(int(m.group(4)))


def pick_rjp(names, init_s: str, kind: str, ft: int):
    """ファイル名の一覧から、指定の初期値・種別で ft を含むものを返す。無ければ None。"""
    for n in names:
        r = rjp_range(n)
        if r and r[0] == init_s and r[1] == kind and r[2] <= ft <= r[3]:
            return n
    return None


def _get_text(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        return r.text if r.status_code == 200 else None
    except requests.RequestException as e:
        print(f"  RISHへの接続に失敗: {e}")
        return None


def list_remote(day: datetime) -> dict:
    """指定日(UTC)のRISHディレクトリを調べ、{初期時刻(YYYYMMDDHH): [FT,...]}（Rglのみ）を返す。"""
    text = _get_text(f"{BASE_URL}/{day:%Y/%m/%d}/")
    if text is None:
        return {}
    found = {}
    for init, ddhh in set(re.findall(
            r"Z__C_RJTD_(\d{10})0000_GSM_GPV_Rgl_FD(\d{4})_grib2\.bin", text)):
        found.setdefault(init, []).append(ddhh_to_ft(int(ddhh)))
    return {k: sorted(v) for k, v in found.items()}


def list_remote_rjp(init: datetime) -> list:
    """初期値のRISHディレクトリにあるRjpファイル名の一覧。"""
    text = _get_text(f"{BASE_URL}/{init:%Y/%m/%d}/")
    if text is None:
        return []
    return sorted({n for n in re.findall(r"Z__C_RJTD_\d{10}0000_GSM_GPV_Rjp_[^\"'<> ]+", text)
                   if rjp_range(n) and rjp_range(n)[0] == f"{init:%Y%m%d%H}"})


def find_latest_init(min_fts: int = 5, days_back: int = 3):
    """RISH 上で FT が min_fts 個以上揃っている最新の初期時刻を返す。(datetime, FTリスト)"""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(days_back + 1):
        found = list_remote(today - timedelta(days=d))
        for init in sorted(found, reverse=True):
            if len(found[init]) >= min_fts:
                return parse_init(init), found[init]
    return None, []


def remote_status(init: datetime, max_ft: int, rgl_step: int = 6, rjp_step: int = 3) -> dict:
    """RISH 上で、この初期値の必要ファイルが max_ft まで揃っているかを調べる。

    返り値: {"rgl_missing": [FT...], "rjp_missing": [FT...], "complete": bool}
    公開の途中（一部のFTだけ先に出ている状態）で作図を始めないための判定に使う。
    """
    rgl = set(list_remote(init).get(f"{init:%Y%m%d%H}", []))
    names = list_remote_rjp(init)
    init_s = f"{init:%Y%m%d%H}"
    rgl_missing = [ft for ft in range(0, max_ft + 1, rgl_step) if ft not in rgl]
    rjp_missing = [ft for ft in range(0, max_ft + 1, rjp_step)
                   if pick_rjp(names, init_s, "L-pall", ft) is None
                   or pick_rjp(names, init_s, "Lsurf", ft) is None]
    return {"rgl_missing": rgl_missing, "rjp_missing": rjp_missing,
            "complete": not rgl_missing and not rjp_missing}


def _fetch(url: str, dest: Path) -> Path | None:
    """URLをdestへ保存。既存ならスキップ。失敗時は None（壊れたファイルは残さない）。
    並行スレッドが同じファイルを要求しても、1つだけが取得し、残りは完了を待って既存として扱う。"""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(dest), threading.Lock())
    with lock:
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            tmp.rename(dest)
            return dest
        except requests.RequestException as e:
            print(f"  ダウンロード失敗 {dest.name}: {e}")
            tmp.unlink(missing_ok=True)
            return None


def download(init: datetime, ft: int, data_dir: Path = DEFAULT_DATA_DIR) -> Path | None:
    """Rgl 1ファイルをダウンロード。"""
    fn = gsm_filename(init, ft)
    return _fetch(f"{BASE_URL}/{init:%Y/%m/%d}/{fn}", Path(data_dir) / fn)


def download_rjp(init: datetime, fts, data_dir: Path = DEFAULT_DATA_DIR) -> set:
    """指定FT群に必要なRjpファイル（等圧面・地上）を取得し、取得できたFTの集合を返す。"""
    names = list_remote_rjp(init)
    init_s = f"{init:%Y%m%d%H}"
    ok = set()
    got = {}
    for ft in fts:
        pair = []
        for kind in ("L-pall", "Lsurf"):
            n = pick_rjp(names, init_s, kind, ft)
            if n is None:
                break
            if n not in got:
                got[n] = _fetch(f"{BASE_URL}/{init:%Y/%m/%d}/{n}", Path(data_dir) / n)
            pair.append(got[n])
        if len(pair) == 2 and all(pair):
            ok.add(ft)
    return ok


def local_rjp(data_dir: Path, init: datetime, kind: str, ft: int) -> Path | None:
    """手元のRjpファイルから ft を含むものを探す。"""
    names = [p.name for p in Path(data_dir).glob(f"Z__C_RJTD_{init:%Y%m%d%H}0000_GSM_GPV_Rjp_*")]
    n = pick_rjp(names, f"{init:%Y%m%d%H}", kind, ft)
    return Path(data_dir) / n if n else None


# ------------------------------------------------------------------ 読み込み
SURFACE = {
    "msl": ("prmsl", "meanSea", 0),
    "sp": ("sp", "surface", 0),
    "u10": ("10u", "heightAboveGround", 10),
    "v10": ("10v", "heightAboveGround", 10),
    "t2": ("2t", "heightAboveGround", 2),
}
PLEVELS = (975, 925, 850, 700, 500, 300, 200, 100)  # 存在する面だけが使われる（Rglに975は無い）。200/100hPaはjet200/jet100用
PVARS = {"gh": "gh", "t": "t", "u": "u", "v": "v", "r": "r", "w": "w"}


def _scan(path: Path, step, tp_steps=()):
    """GRIB2を1回走査して生の場を集める。

    step: 瞬時値として採る予報時間（None なら全メッセージ。Rglは1ファイル1時間なので None）
    tp_steps: 積算降水(APCP)を集める終了ステップの集合（開始0の積算のみ）
    """
    import numpy as np
    import pygrib

    want = {v: ("s", k) for k, v in SURFACE.items()}
    for lv in PLEVELS:
        for k, sn in PVARS.items():
            want[(sn, "isobaricInhPa", lv)] = ("p", (lv, k))

    out = {"p": {}, "tp": {}, "tcc": None}
    lats = lons = None
    grbs = pygrib.open(str(path))
    try:
        for m in grbs:
            end = int(m.endStep)
            if m.typeOfLevel == "surface" and m.shortName == "unknown" \
                    and m.parameterCategory == 1 and m.parameterNumber == 8:
                # 積算降水量 APCP（初期時刻からの積算 mm）。pygribの表に無く name が unknown になる
                if int(m.startStep) == 0 and (not tp_steps or end in tp_steps):
                    out["tp"][end] = np.asarray(m.values, dtype=np.float32)
                continue
            if step is not None and end != step:
                continue
            key = (m.shortName, m.typeOfLevel, m.level)
            if key in want:
                kind, k = want[key]
                vals = np.asarray(m.values, dtype=np.float32)
                if lats is None:
                    la, lo = m.latlons()
                    lats, lons = la[:, 0], lo[0, :]
                if kind == "s":
                    out[k] = vals
                else:
                    out["p"].setdefault(k[0], {})[k[1]] = vals
            elif (m.typeOfLevel == "surface" and m.shortName == "unknown"
                  and m.parameterCategory == 6 and m.parameterNumber == 1):
                out["tcc"] = np.asarray(m.values, dtype=np.float32)  # 全雲量 %
    finally:
        grbs.close()
    out["lats"], out["lons"] = lats, lons
    return out


def _finish(parts: list, rr, rr_h):
    """複数ファイルの走査結果を統合し、単位変換・緯度昇順化して描画用の辞書にする。"""
    import numpy as np
    F = {"p": {}, "tcc": None, "rr": rr, "rr_h": rr_h}
    for d in parts:
        for k in ("msl", "sp", "u10", "v10", "t2", "tcc", "lats", "lons"):
            if d.get(k) is not None:
                F[k] = d[k]
        for lv, dd in d["p"].items():
            F["p"].setdefault(lv, {}).update(dd)
    # 必要な変数が揃った面だけ残す
    F["p"] = {lv: d for lv, d in F["p"].items() if all(k in d for k in ("gh", "t", "u", "v"))}
    F["msl"] = F["msl"] / 100.0           # Pa → hPa
    F["sp"] = F["sp"] / 100.0             # 地表気圧 Pa → hPa（地面下のマスクに使う）
    F["t2"] = F["t2"] - 273.15            # K → ℃
    for lv, d in F["p"].items():
        d["t"] = d["t"] - 273.15
        if "w" in d:
            d["w"] = d["w"] * 36.0        # Pa/s → hPa/h（上昇が負）
        else:
            d["w"] = np.zeros_like(d["t"])
        if "r" not in d:
            d["r"] = np.full_like(d["t"], 50.0)
    if F["lats"][0] > F["lats"][-1]:      # 緯度を昇順（南→北）へ
        F["lats"] = F["lats"][::-1]
        for k in ("msl", "sp", "u10", "v10", "t2", "tcc", "rr"):
            if F.get(k) is not None:
                F[k] = F[k][::-1, :]
        for d in F["p"].values():
            for k in d:
                d[k] = d[k][::-1, :]
    return F


def load_fields(path: Path, prev_path: Path | None = None) -> dict:
    """Rgl（全球0.5°）1ファイルを読む。prev_path は1つ前のFT（6時間前）で、あれば直前6時間の平均降水 rr[mm/h] を作る。

    返り値: {"lats","lons","msl"(hPa),"sp"(hPa),"u10","v10","t2"(℃),"tcc"(%),"rr"(mm/h|None),"rr_h"(降水の平均時間),
             "p": {975..300: {"gh"(m),"t"(℃),"u","v","r"(%),"w"(hPa/h)}}}
    """
    cur = _scan(path, None)
    rr = None
    if cur["tp"]:
        tp = next(iter(cur["tp"].values()))
        end_cur = max(cur["tp"])
        if prev_path is not None and Path(prev_path).exists():
            pv = _scan(prev_path, None)
            if pv["tp"]:
                rr = (tp - next(iter(pv["tp"].values()))) / 6.0
        elif end_cur == 6:           # FT=6 は 0-6h の積算がそのまま6時間降水
            rr = tp / 6.0
    return _finish([cur], rr if rr is None else rr.clip(min=0), 6)


def load_fields_rjp(data_dir: Path, init: datetime, ft: int) -> dict | None:
    """Rjp（日本域0.125°）から ft の場を読む。直前1時間降水 rr[mm/h] を作る。ファイルが無ければ None。"""
    pall = local_rjp(data_dir, init, "L-pall", ft)
    surf = local_rjp(data_dir, init, "Lsurf", ft)
    if pall is None or surf is None:
        return None
    a = _scan(pall, ft)
    b = _scan(surf, ft, tp_steps={ft, ft - 1})
    rr = None
    if ft >= 1 and ft in b["tp"] and (ft - 1) in b["tp"]:
        rr = (b["tp"][ft] - b["tp"][ft - 1]).clip(min=0)       # 直前1時間 mm/h
    elif ft == 1 and ft in b["tp"]:
        rr = b["tp"][ft]
    return _finish([a, b], rr, 1)


# ------------------------------------------------------------------ nwp_chart_web 用の入口
def find_latest_complete(max_ft: int, interval: int = 3, days_back: int = 3, hours=None):
    """Rgl(6時間毎)とRjp(3時間毎)が max_ft まで両方揃っている最新の初期値を返す。無ければ None。

    hours を指定すると、その時刻（UTC）の初期値だけを対象にする（例: [0, 12]）。
    """
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(days_back + 1):
        found = list_remote(today - timedelta(days=d))
        for init_s in sorted(found, reverse=True):
            init = parse_init(init_s)
            if hours and init.hour not in hours:
                continue
            if remote_status(init, max_ft, 6, interval)["complete"]:
                return init
    return None


def fetch(init: datetime, ft: int, data_dir: Path, want_main: bool, want_rjp: bool) -> bool:
    """予報時間 ft に必要なファイルを取得する。main=Rgl（ftが6の倍数のとき）、rjp=日本域。1つでも使えれば True。

    Rglの降水は直前6時間の差分なので、ft>6 では1つ前（ft-6）のRglも取得する（排他により二重取得しない）。
    """
    ok = False
    if want_main and ft % 6 == 0:
        ok = download(init, ft, data_dir) is not None
        if ok and ft > 6:
            download(init, ft - 6, data_dir)
    if want_rjp and ft % 3 == 0:
        ok = (ft in download_rjp(init, [ft], data_dir)) or ok
    return ok


def load(init: datetime, ft: int, src: str, data_dir: Path) -> dict | None:
    """src='main'(Rgl全球0.5°) または 'rjp'(日本域0.125°) の場を、描画用の辞書として読む。無ければ None。"""
    if src == "rjp":
        return load_fields_rjp(data_dir, init, ft)
    path = Path(data_dir) / gsm_filename(init, ft)
    if ft % 6 != 0 or not path.exists():
        return None
    prev = Path(data_dir) / gsm_filename(init, ft - 6)
    return load_fields(path, prev if ft > 6 else None)
