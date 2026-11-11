#!/usr/bin/env python
# coding: utf-8
"""ECMWF / GFS 天気図の一括作成CLI（取得 → 作図 → 一時データ削除 → manifest更新）。

使用例:
  python make_charts.py ecmwf --latest                      # 最新の揃った初期値を取得して作図（FT0〜72h、6時間毎）
  python make_charts.py gfs 2026110312 --max-ft 24          # 初期値指定
  python make_charts.py ecmwf --latest --interval 3 --jobs 3
  python make_charts.py gfs --latest --charts surf z500 --regions japan
  python make_charts.py --manifest-only                     # output/ を走査して manifest.json だけ更新
  python make_charts.py ? / -? / --?                        # ヘルプ

取得したGRIB2（必要な変数だけ、1ステップ ECMWF約20MB・GFS約40MB）は作図後に削除する（--keep-data で残す）。
サーバーに残るのは output/{model}/{初期値}/{地域}/{図種}_{FT}.png と manifest.json だけ。
"""
import argparse
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import nwp_data
import nwp_charts
import gsm_source

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"


def parse_args():
    p = argparse.ArgumentParser(description="ECMWF / GFS / GSM から天気図PNGを作成する",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("model", nargs="?", choices=list(nwp_data.MODELS), help="ecmwf / gfs / gsm（gsm はローカル専用）")
    p.add_argument("init_time", nargs="?", help="初期時刻 YYYYMMDDHH（UTC）。--latest 指定時は省略可")
    p.add_argument("--latest", action="store_true", help="必要な予報時間が全部揃った最新の初期値を自動検索する")
    p.add_argument("--max-ft", type=int, default=72, help="作成する最大予報時間[h]（既定: 72）")
    p.add_argument("--min-ft", type=int, default=0, help="作成する最小予報時間[h]（既定: 0）")
    p.add_argument("--interval", type=int, default=None,
                   help="FT間隔[h]（既定: モデルごと。GSMは3＝日本域0.125°が3時間毎、他は6）")
    p.add_argument("--charts", nargs="+", default=list(nwp_charts.CHARTS), choices=list(nwp_charts.CHARTS))
    p.add_argument("--regions", nargs="+", default=None, choices=list(nwp_charts.REGIONS),
                   help="地域（既定: そのモデルで作れる全地域）")
    p.add_argument("--data-dir", default=str(nwp_data.DEFAULT_DATA_DIR), help="取得GRIB2の置き場（既定: data/、GSMは data/gsm/。既存の gsm_chart_web/data/gsm も指定可）")
    p.add_argument("--jobs", type=int, default=2, help="作図の並列数（既定: 2）")
    p.add_argument("--dl-jobs", type=int, default=4, help="ダウンロードの並列数（既定: 4）")
    p.add_argument("--keep-data", action="store_true", help="作図後も取得GRIB2を残す（GSMは常に残す）")
    p.add_argument("--no-download", action="store_true", help="ダウンロードせず手元のファイルだけ使う")
    p.add_argument("--manifest-only", action="store_true", help="manifest.json の更新だけ行う")
    if any(a in sys.argv[1:] for a in ("?", "-?", "--?")):
        p.print_help()
        sys.exit(0)
    a = p.parse_args()
    if not a.manifest_only and not a.model:
        p.error("model（ecmwf / gfs / gsm）を指定してください")
    if a.interval is None and a.model:
        a.interval = nwp_data.default_interval(a.model)
    if a.regions is None and a.model:
        a.regions = nwp_charts.regions_for(a.model)
    if a.model and any(r not in nwp_charts.regions_for(a.model) for r in (a.regions or [])):
        p.error(f"{a.model} で作れない地域が含まれています（作れる地域: {' '.join(nwp_charts.regions_for(a.model))}）")
    return a


def _render_ft(args):
    """1つのFT分（全図種・全地域）を描画する。サブプロセスで実行される。"""
    model, init_s, ft, charts, regions, data_dir, interval, keep = args
    init = nwp_data.parse_init(init_s)
    d = Path(data_dir)
    cache = {}  # データ系統（GSMの main/rjp）ごとに1回だけ読む

    if model == "gsm":
        def fields(region):
            src = nwp_charts.REGIONS[region].get("src", "main")
            if src not in cache:
                cache[src] = gsm_source.load(init, ft, src, d)
            return cache[src]
        path = None
    else:
        path = d / model / init_s / f"f{ft:03d}.grib2"
        if not path.exists():
            return ft, 0, "データなし"
        tp_prev, zero = None, False
        if model in nwp_data.ECMWF_LIKE:
            if ft - interval >= 1:
                tp_prev = d / model / init_s / f"tp_f{ft - interval:03d}.grib2"
            elif ft == interval:
                zero = True
        F0 = nwp_data.load_fields(model, path, tp_prev if tp_prev and tp_prev.exists() else None, interval, zero)

        def fields(region):
            return F0

    n = 0
    for region in regions:
        F = fields(region)
        if F is None:
            continue  # この予報時間に、その地域のデータ系統が無い（例: GSMの全球は6時間毎）
        out = OUTPUT_DIR / model / init_s / region
        out.mkdir(parents=True, exist_ok=True)
        for chart in charts:
            if region not in nwp_charts.CHARTS[chart].get("regions", (region,)):
                continue  # 図種ごとに作る地域を限っている（容量のため。jet200・t300はアジア域のみ）
            if nwp_charts.CHARTS[chart].get("cloud") and F.get("no_cloud"):
                continue  # 雲量が無いデータ（JRA-55）では天気図は作らない
            if any(lv not in F["p"] for lv in nwp_charts.CHARTS[chart].get("levels", [])):
                continue  # このモデルに無い気圧面（ECMWF/AIFS/GSM全球の975hPa）はスキップ
            try:
                nwp_charts.render_chart(chart, region, F, init, ft, out / f"{chart}_{ft:03d}.png",
                                        model=nwp_data.MODELS[model]["label"])
                n += 1
            except Exception as e:  # 1枚の失敗で全体を止めない
                print(f"  失敗: {model} {init_s} FT{ft} {region}/{chart}: {e!r}", flush=True)
    if not keep and path is not None:  # GSM は Rjp を複数のFTで共有するため削除しない
        path.unlink(missing_ok=True)  # 作図が終わったら一時データを捨てる（ファイル単位で削除）
        if model in nwp_data.ECMWF_LIKE:
            (d / model / init_s / f"tp_f{ft - interval:03d}.grib2").unlink(missing_ok=True)
    return ft, n, "ok" if n else "データなし"


def write_manifest(output_dir=None, public_only=False):
    """output/ を走査して output/manifest.json を作る（Webアプリはこれを読む）。
    output_dir を渡すと、そのディレクトリ（公開用サイトの output/ など）を対象にする。
    public_only=True なら local_only のモデル（GSM）は、画像が出力先にあっても manifest に載せない（公開用）。"""
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    models = {}
    for model, info in nwp_data.MODELS.items():
        if public_only and info.get("local_only"):
            continue
        md = out_dir / model
        inits = []
        if md.is_dir():
            for d in sorted(md.iterdir(), reverse=True):
                if not (d.is_dir() and re.fullmatch(r"\d{10}", d.name)):
                    continue
                regions = {}
                for rd in sorted(d.iterdir()):
                    if not rd.is_dir() or rd.name not in nwp_charts.REGIONS:
                        continue
                    charts = {}
                    for f in rd.glob("*_*.png"):
                        m = re.fullmatch(r"(\w+?)_(\d{3})\.png", f.name)
                        if m and m.group(1) in nwp_charts.CHARTS:
                            charts.setdefault(m.group(1), []).append(int(m.group(2)))
                    if charts:
                        regions[rd.name] = {c: sorted(charts[c]) for c in nwp_charts.CHARTS if c in charts}   # 図種は高層→地上（CHARTSの順）
                if regions:
                    inits.append({"init": d.name, "regions": regions})
        if inits:
            now = datetime.now(timezone.utc)
            legal = nwp_data.legal_text(model, now.year, now.strftime("%Y-%m-%d"))
            models[model] = {"label": info["label"], "credit": legal["short"], "notice": legal["notice"],
                             "license_url": legal["license_url"], "inits": inits}
    manifest = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": ("本サイトの図は、上記の元データを加工して本アプリが作図したもので、各提供元（ECMWF・NOAA/NCEP・気象庁）の公式な図ではありません。"
                       "予報は不確実性を含みます。防災・安全に関わる判断には、気象庁など公的機関の公式情報を使ってください。"
                       "図の内容や利用による損害について、本サイトの作成者は責任を負いません。"),
        "charts": {k: v["label"] for k, v in nwp_charts.CHARTS.items()},
        "regions": {k: v["label"] for k, v in nwp_charts.REGIONS.items()
                    if not (public_only and "models" in v and not any(m in v["models"] for m in nwp_data.public_models()))},
        "models": models,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = ", ".join("%s %d件" % (m, len(v["inits"])) for m, v in models.items()) or "画像なし"
    print("manifest.json 更新: " + summary, flush=True)


def build(a):
    """1モデル・1初期値を作る。"""
    model = a.model
    data_dir = Path(a.data_dir)
    if model == "gsm" and a.data_dir == str(nwp_data.DEFAULT_DATA_DIR):
        data_dir = nwp_data.DEFAULT_DATA_DIR / "gsm"  # GSMは元のファイル名のまま data/gsm/ に置く（削除しない）
    if a.latest or not a.init_time:
        init = nwp_data.find_latest_complete(model, a.max_ft, a.interval)
        if init is None:
            print(f"{model}: FT{a.max_ft}hまで揃った初期値が見つかりません")
            return 1
        print(f"{model}: 最新初期値 {init:%Y-%m-%d %H}UTC", flush=True)
    else:
        init = nwp_data.parse_init(a.init_time)
    init_s = f"{init:%Y%m%d%H}"
    ft_list = list(range(a.min_ft, a.max_ft + 1, a.interval))

    def make_task(ft):
        return (model, init_s, ft, a.charts, a.regions, str(data_dir), a.interval, a.keep_data)

    def dl(ft):
        """1ステップ分を取得する。ECMWF系は降水の差分用に1つ前のステップの tp も取る。"""
        if model == "gsm":
            srcs = {nwp_charts.REGIONS[r].get("src", "main") for r in a.regions}
            if a.no_download:
                return ft, True
            return ft, gsm_source.fetch(init, ft, data_dir, "main" in srcs, "rjp" in srcs)
        if a.no_download:
            return ft, (data_dir / model / init_s / f"f{ft:03d}.grib2").exists()
        ok = nwp_data.fetch_subset(model, init, ft, data_dir) is not None
        if ok and model in nwp_data.ECMWF_LIKE and ft - a.interval >= 1:
            nwp_data.fetch_tp_only(init, ft - a.interval, data_dir, model)
        return ft, ok

    # 取得と作図は「全ステップの取得が終わるまで待たず」重ねて進める:
    # 1ステップ取得できたらすぐ作図に回す（取得は別スレッド、作図は別プロセス）
    print(f"{model}: 取得と作図を並行して実行（FT {len(ft_list)} 個、必要な変数だけ取得）", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=a.dl_jobs) as dl_ex:
        dl_futs = [dl_ex.submit(dl, ft) for ft in ft_list]
        if a.jobs > 1:
            with ProcessPoolExecutor(max_workers=a.jobs) as rn_ex:
                rn_futs = []
                for f in as_completed(dl_futs):
                    ft, ok = f.result()
                    if ok:
                        rn_futs.append(rn_ex.submit(_render_ft, make_task(ft)))
                        print(f"  {model} FT{ft}h 取得完了 → 作図開始", flush=True)
                    else:
                        print(f"  {model} FT{ft}h 取得失敗（スキップ）", flush=True)
                results = [f.result() for f in rn_futs]
        else:
            for f in as_completed(dl_futs):
                ft, ok = f.result()
                if ok:
                    results.append(_render_ft(make_task(ft)))
    total = sum(r[1] for r in results)
    for ft, n, msg in results:
        if msg != "ok":
            print(f"  FT{ft}: {msg}")
    print(f"完了({model}): {total} 枚 → {OUTPUT_DIR / model / init_s}", flush=True)
    return 0 if total else 1


def main():
    a = parse_args()
    if a.manifest_only:
        write_manifest()
        return
    rc = build(a)
    write_manifest()
    sys.exit(rc)


if __name__ == "__main__":
    main()
