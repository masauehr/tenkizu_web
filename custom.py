#!/usr/bin/env python
# coding: utf-8
"""ローカル専用: 任意の断面線・任意の地点で、断面図／エマグラムをその場で作る。

公開サイトは固定（xsection.SECTIONS・emagram.STATIONS）。こちらは server.py の /api/custom から呼ばれ、
output/custom/ にPNGを作って返す（同じ条件なら作り直さない）。
データ: ECMWF/GFS は data/<model>/<初期値>/fNNN.grib2（無ければその場で取得して data/ に残す。1ステップ約20〜40MB）、
GSM は手元にあるものだけ（自動取得しない。600MB と大きいため）。
"""
import hashlib
import re
from datetime import timedelta
from pathlib import Path

import disk_guard
import nwp_data

ROOT = Path(__file__).resolve().parent
CUSTOM_DIR = ROOT / "output" / "custom"
KINDS = ("xsection", "emagram")


class CustomError(ValueError):
    """入力不正・データ無し。メッセージは画面にそのまま出す。"""


def _num(v, name, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise CustomError(f"{name} が数値ではありません")
    if not (lo <= x <= hi):
        raise CustomError(f"{name} は {lo:g}〜{hi:g} の範囲で指定してください")
    return x


def validate(kind, model, init_s, ft, q):
    """入力を検証して正規化した dict を返す。"""
    if kind not in KINDS:
        raise CustomError("kind は xsection / emagram のどちらかです")
    if model not in nwp_data.MODELS:
        raise CustomError("モデルが不正です")
    if not re.fullmatch(r"\d{10}", init_s or ""):
        raise CustomError("初期値は YYYYMMDDHH（UTC）で指定してください")
    ft = int(_num(ft, "予報時間", 0, 360))
    p = dict(kind=kind, model=model, init=init_s, ft=ft)
    if kind == "xsection":
        p.update(lat1=_num(q.get("lat1"), "始点の緯度", -15, 89), lon1=_num(q.get("lon1"), "始点の経度", 0, 360),
                 lat2=_num(q.get("lat2"), "終点の緯度", -15, 89), lon2=_num(q.get("lon2"), "終点の経度", 0, 360))
        if max(abs(p["lat2"] - p["lat1"]), abs(p["lon2"] - p["lon1"])) < 1.0:
            raise CustomError("始点と終点が近すぎます（1度以上離してください）")
        if max(abs(p["lat2"] - p["lat1"]), abs(p["lon2"] - p["lon1"])) > 60.0:
            raise CustomError("断面が長すぎます（60度以内にしてください）")
    else:
        p.update(lat=_num(q.get("lat"), "緯度", -15, 89), lon=_num(q.get("lon"), "経度", 0, 360))
    return p


def out_path(p):
    key = "_".join(f"{k}={p[k]}" for k in sorted(p))
    h = hashlib.sha1(key.encode()).hexdigest()[:12]
    return CUSTOM_DIR / f"{p['kind']}_{p['model']}_{p['init']}_{p['ft']:03d}_{h}.png"


def load_F(model, init_s, ft, fetch=True):
    """格子データ F を返す。ECMWF/GFS は無ければ取得する。GSM は手元にあるものだけ。"""
    init = nwp_data.parse_init(init_s)
    if model == "gsm":
        import gsm_source
        F = gsm_source.load(init, ft, "main", nwp_data.DEFAULT_DATA_DIR / "gsm")
        if F is None:
            raise CustomError("GSM のデータが手元にありません（GSM は自動取得しません）")
        return F
    d = nwp_data.DEFAULT_DATA_DIR
    path = d / model / init_s / f"f{ft:03d}.grib2"
    if not path.exists():
        if not fetch:
            raise CustomError("データがありません")
        if not disk_guard.make_room(80 * disk_guard.MB):   # 古い取得データを消して余地を作る。空きが少なすぎるなら取得しない
            raise CustomError("ディスクの空きが少ないため、新しいデータを取得できません（空きを増やしてから、もう一度試してください）")
        if nwp_data.fetch_subset(model, init, ft, d) is None:
            raise CustomError(f"{nwp_data.MODELS[model]['label']} {init_s} FT{ft}h のデータを取得できません（初期値・予報時間を確認してください）")
    return nwp_data.load_fields(model, path, None, 6, False)


def render(p):
    """作図して PNG のパスを返す（同じ条件が既にあればそれを返す）。"""
    out = out_path(p)
    if out.exists():
        return out
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    init = nwp_data.parse_init(p["init"])
    label = nwp_data.MODELS[p["model"]]["label"]
    F = load_F(p["model"], p["init"], p["ft"])
    tmp = out.with_suffix(".tmp.png")
    try:
        if p["kind"] == "xsection":
            import xsection
            xsection.render_xsection(F, init, p["ft"], (p["lat1"], p["lon1"]), (p["lat2"], p["lon2"]), str(tmp),
                                     model=label, name=f"断面 ({p['lat1']:g}N,{p['lon1']:g}E)→({p['lat2']:g}N,{p['lon2']:g}E)")
        else:
            import emagram
            try:
                emagram.render_emagram(F, init, p["ft"], p["lat"], p["lon"], str(tmp), model=label)
            except ValueError as e:
                raise CustomError(str(e))
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    disk_guard.prune_group("custom")   # 自由選択の画像が上限を超えたら、古いものから消す
    return out
