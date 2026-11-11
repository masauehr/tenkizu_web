#!/usr/bin/env python
# coding: utf-8
"""ローカル専用: 任意の断面線・任意の地点で、断面図／エマグラムをその場で作る。

公開サイトは固定（xsection.SECTIONS・emagram.STATIONS）。こちらは server.py の /api/custom から呼ばれ、
output/custom/ にPNGを作って返す（同じ条件なら作り直さない）。
画像ごとに、条件（種類・モデル・初期値・予報時間・地点など）を書いた同名の .json を隣に置く。
「最近の作図」の一覧は、この画像ファイル（と .json）から作る（ブラウザの保存には頼らない。別のブラウザでも同じ一覧になる）。
データ: ECMWF/GFS は data/<model>/<初期値>/fNNN.grib2（無ければその場で取得して data/ に残す。1ステップ約20〜40MB）、
GSM は手元にあるものだけ（自動取得しない。600MB と大きいため）。
"""
import hashlib
import json
import os
import re
import time
from datetime import timedelta
from pathlib import Path

import disk_guard
import nwp_data

ROOT = Path(__file__).resolve().parent
CUSTOM_DIR = ROOT / "output" / "custom"
KINDS = ("xsection", "emagram")
HIST_MAX = 10          # 「最近の作図」に出す件数


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


def describe(p):
    """画面に出す名前（地点・断面線）。"""
    if p["kind"] == "xsection":
        return f"断面 ({p['lat1']:g}N,{p['lon1']:g}E)→({p['lat2']:g}N,{p['lon2']:g}E)"
    return f"エマグラム ({p['lat']:g}N,{p['lon']:g}E)"


def init_text(ik):
    return f"{ik[:4]}-{ik[4:6]}-{ik[6:8]} {ik[8:]}Z"


def meta_path(png):
    return png.with_suffix(".json")


def write_meta(out, p):
    """画像の隣に、条件を書いた .json を置く（一覧と、通常版の見出しに使う）。"""
    meta = dict(kind=p["kind"], mk=p["model"], ik=p["init"], ft=p["ft"], label=describe(p),
                model=nwp_data.MODELS[p["model"]]["label"], initText=init_text(p["init"]),
                params={k: v for k, v in p.items() if k not in ("kind", "model", "init", "ft")}, created=time.time())
    meta_path(out).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


_LEGACY = re.compile(r"(xsection|emagram)_([a-z0-9]+)_(\d{10})_(\d{3})_[0-9a-f]{12}\.png")


def list_recent(limit=HIST_MAX):
    """output/custom/ の画像を、新しく作った（または使った）順に limit 件返す。
    .json があればその条件を、無ければ（以前に作った画像）ファイル名から分かる範囲を使う。"""
    items = []
    try:   # 公開サイトに載せた画像（publish.py が published.json に記録する）
        published = set(json.loads((CUSTOM_DIR / "published.json").read_text(encoding="utf-8")).get("names", []))
    except (OSError, ValueError):
        published = set()
    try:
        files = [f for f in CUSTOM_DIR.glob("*.png") if not f.name.endswith(".tmp.png")]
    except OSError:
        return []
    for f in files:
        try:
            ts = f.stat().st_mtime
        except OSError:
            continue
        meta = None
        try:
            meta = json.loads(meta_path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if meta is None:
            m = _LEGACY.fullmatch(f.name)
            if not m:
                continue
            kind, mk, ik, ft = m.group(1), m.group(2), m.group(3), int(m.group(4))
            meta = dict(kind=kind, mk=mk, ik=ik, ft=ft,
                        label=("断面" if kind == "xsection" else "エマグラム") + "（条件は不明）",
                        model=nwp_data.MODELS.get(mk, {}).get("label", mk), initText=init_text(ik))
        meta = dict(meta)
        meta.update(url="output/custom/" + f.name, ts=ts, published=f.name in published)
        meta.pop("params", None)
        items.append(meta)
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:limit]


def render(p):
    """作図して PNG のパスを返す（同じ条件が既にあればそれを返す）。"""
    out = out_path(p)
    if out.exists():
        now = time.time()                       # 使った画像を「最近」にする（一覧の先頭へ。ディスクの整理で消されにくくもなる）
        os.utime(out, (now, now))
        if not meta_path(out).exists():
            write_meta(out, p)
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
                                     model=label, name=describe(p))
        else:
            import emagram
            try:
                emagram.render_emagram(F, init, p["ft"], p["lat"], p["lon"], str(tmp), model=label)
            except ValueError as e:
                raise CustomError(str(e))
        tmp.replace(out)
        write_meta(out, p)
    finally:
        tmp.unlink(missing_ok=True)
    disk_guard.prune_group("custom")   # 自由選択の画像が上限を超えたら、古いものから消す
    return out
