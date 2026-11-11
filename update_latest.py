#!/usr/bin/env python
# coding: utf-8
"""各モデルの「必要な予報時間が全部揃った最新の初期値」を取得して作図する（画面ボタン・CI用）。

動作（モデルごと）:
  1. 最新から順に、RISH等ではなく各配信元で max_ft まで揃っている初期値を探す（公開途中は飛ばす）
  2. すでに作成済み（その初期値のFTが全部 output/ にある）なら何もしない
  3. make_charts.py で 取得 → 作図 → 一時データ削除 → manifest更新
  各モデルは別々のプロセスで**並行**して処理する（あるモデルの取得が遅くても他のモデルは待たない）。
  各モデルの中でも、全ステップの取得完了を待たず、取得できたステップから順に作図する（make_charts.py）。

使用例:
  python update_latest.py --dry-run            # 何をするかだけ表示
  python update_latest.py                      # 全モデル（ECMWF・GFS・GSM）を並行して更新（FT0〜72h、6時間毎）
  python update_latest.py --models gfs --jobs 3
  python update_latest.py --sequential         # モデルを1つずつ順に処理（CPUやネットワークを絞りたいとき）
  python update_latest.py --keep 2             # 各モデル最新2初期値だけ残す（古いPNGをファイル単位で削除。既定は削除しない）
  python update_latest.py ? / -? / --?         # ヘルプ

二重起動は output/.update.lock で防ぐ。
"""
import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import nwp_data

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
LOCK = OUTPUT_DIR / ".update.lock"
LOCK_STALE_SEC = 3 * 3600


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def parse_args():
    p = argparse.ArgumentParser(description="最新の初期値を取得して作図する",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--models", nargs="+", default=nwp_data.auto_models(), choices=list(nwp_data.MODELS))   
    p.add_argument("--max-ft", type=int, default=72)
    p.add_argument("--interval", type=int, default=None, help="FT間隔[h]（既定: モデルごと。GSMは3、他は6）")
    p.add_argument("--jobs", type=int, default=2, help="作図の並列数")
    p.add_argument("--dl-jobs", type=int, default=4, help="ダウンロードの並列数")
    p.add_argument("--days-back", type=int, default=3)
    p.add_argument("--keep", type=int, default=0, help="各モデルで残す初期値の数。0(既定)なら削除しない")
    p.add_argument("--init-hours", type=int, nargs="+", default=None, help="対象にする初期時刻（UTC）。例: 0 6 12 18。既定はモデルごとの公開時刻（nwp_data.MODELS の publish_hours）")
    p.add_argument("--keep-days", type=float, default=0, help="初期値がこの日数より古い PNG と GSM の GRIB2 を削除する。0(既定)なら削除しない")
    p.add_argument("--data-dir", default=str(nwp_data.DEFAULT_DATA_DIR))
    p.add_argument("--sequential", action="store_true", help="モデルを並行せず1つずつ処理する")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="作成済みでも作り直す")
    if any(a in sys.argv[1:] for a in ("?", "-?", "--?")):
        p.print_help()
        sys.exit(0)
    return p.parse_args()


def acquire_lock():
    OUTPUT_DIR.mkdir(exist_ok=True)
    if LOCK.exists():
        age = time.time() - LOCK.stat().st_mtime
        if age < LOCK_STALE_SEC:
            return False
        log(f"古いロック（{age / 3600:.1f}時間前）を無視して続行")
    LOCK.write_text(str(os.getpid()))
    return True


def is_built(model, init_s, max_ft, interval):  # interval はモデルごとの値を渡す
    """その初期値の全FTが、いずれかの地域に作成済みか。"""
    d = OUTPUT_DIR / model / init_s
    if not d.is_dir():
        return False
    have = set()
    for f in d.glob("*/*_*.png"):
        m = re.fullmatch(r"\w+?_(\d{3})\.png", f.name)
        if m:
            have.add(int(m.group(1)))
    return all(ft in have for ft in range(0, max_ft + 1, interval))


def is_kept(model, init_s):
    """手動で取得した初期値（将来用）（output/<モデル>/<初期値>/.keep がある）は、自動削除しない。"""
    return (OUTPUT_DIR / model / init_s / ".keep").exists()


def prune(model, keep):
    """モデルごとに最新 keep 個の初期値以外のPNGを削除（ファイル単位。ディレクトリごとは消さない）。"""
    md = OUTPUT_DIR / model
    if not md.is_dir():
        return
    inits = sorted([d.name for d in md.iterdir() if d.is_dir() and re.fullmatch(r"\d{10}", d.name) and not is_kept(model, d.name)],
                   reverse=True)
    n = 0
    for init_s in inits[keep:]:
        d = md / init_s
        for f in list(d.glob("*/*.png")):
            f.unlink()
            n += 1
        for sub in list(d.iterdir()):
            if sub.is_dir():
                try:
                    sub.rmdir()
                except OSError:
                    pass
        try:
            d.rmdir()
        except OSError:
            pass
    if n:
        log(f"{model}: 古い初期値 {len(inits[keep:])} 件を整理（PNG {n} 枚）")


def prune_days(models, keep_days, data_dir, dry_run=False):
    """初期値が keep_days 日より古い PNG（出力）と GSM の GRIB2（取得データ）を削除する。

    初期値はディレクトリ名・ファイル名の時刻（UTC）で判定する。この関数が消すのは
    output/<モデル>/<初期値>/ 以下の PNG と、data/gsm/ の GSM ファイルだけ。
    """
    border = datetime.utcnow() - timedelta(days=keep_days)
    def old(init_s):
        return datetime.strptime(init_s, "%Y%m%d%H") < border
    for model in models:
        md = OUTPUT_DIR / model
        if md.is_dir():
            for d in sorted(md.iterdir()):
                if d.is_dir() and re.fullmatch(r"\d{10}", d.name) and old(d.name) and not is_kept(model, d.name):
                    pngs = list(d.glob("*/*.png"))
                    log(f"{model}: {d.name} を削除（PNG {len(pngs)} 枚）{' [dry-run]' if dry_run else ''}")
                    if not dry_run:
                        for f in pngs:
                            f.unlink()
                        for sub in list(d.iterdir()):
                            if sub.is_dir():
                                try:
                                    sub.rmdir()
                                except OSError:
                                    pass
                        try:
                            d.rmdir()
                        except OSError:
                            pass
    if "gsm" in models:
        gd = Path(data_dir) / "gsm"
        files = [f for f in gd.glob("Z__C_RJTD_*_GSM_GPV_*.bin")] if gd.is_dir() else []
        n = 0
        for f in files:
            m = re.match(r"Z__C_RJTD_(\d{10})\d{4}_GSM_GPV_", f.name)  # 初期時刻 YYYYMMDDHH + 分秒
            if m and old(m.group(1)) and not is_kept("gsm", m.group(1)):   # 手動取得(.keep)は残す
                n += 1
                if not dry_run:
                    f.unlink()
        if n:
            log(f"gsm: 古い GRIB2 {n} ファイルを削除{' [dry-run]' if dry_run else ''}")


def main():
    a = parse_args()
    if not a.dry_run and not acquire_lock():
        log("別の更新が実行中のため終了します")
        return
    rc_all = 0
    try:
        todo = []  # (model, init_s)
        for model in a.models:
            iv = a.interval or nwp_data.default_interval(model)  # GSMは3時間毎（日本域0.125°）、他は6
            init = nwp_data.find_latest_complete(
                model, a.max_ft, iv, a.days_back, a.init_hours or nwp_data.MODELS[model].get("publish_hours"))
            if init is None:
                log(f"{model}: FT{a.max_ft}hまで揃った初期値が見つかりません")
                continue
            init_s = f"{init:%Y%m%d%H}"
            if not a.force and is_built(model, init_s, a.max_ft, iv):
                log(f"{model}: {init_s} は作成済み")
                continue
            log(f"{model}: {init_s} を取得・作図します{' [dry-run]' if a.dry_run else ''}")
            todo.append((model, init_s, iv))
        if todo and not a.dry_run:
            def cmd_for(model, init_s, iv):
                return [sys.executable, "-u", str(ROOT / "make_charts.py"), model, init_s,
                        "--max-ft", str(a.max_ft), "--interval", str(iv), "--jobs", str(a.jobs),
                        "--dl-jobs", str(a.dl_jobs), "--data-dir", a.data_dir]
            if a.sequential:
                procs = []
                for model, init_s, iv in todo:
                    r = subprocess.run(cmd_for(model, init_s, iv), cwd=ROOT)
                    procs.append((model, r.returncode))
            else:
                running = [(m, subprocess.Popen(cmd_for(m, i, v), cwd=ROOT)) for m, i, v in todo]  # モデルごとに別プロセスで並行
                procs = [(m, pr.wait()) for m, pr in running]
            for model, rc in procs:
                log(f"{model}: make_charts 終了コード {rc}")
                rc_all = rc_all or rc
        if a.keep > 0 and not a.dry_run:
            for model in a.models:
                prune(model, a.keep)
        if a.keep_days > 0:
            prune_days(a.models, a.keep_days, a.data_dir, a.dry_run)
        if not a.dry_run:  # 常にmanifestを最新化
            subprocess.run([sys.executable, str(ROOT / "make_charts.py"), "--manifest-only"], cwd=ROOT)
    finally:
        if not a.dry_run:
            LOCK.unlink(missing_ok=True)
    sys.exit(rc_all)


if __name__ == "__main__":
    main()
