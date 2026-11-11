#!/usr/bin/env python
# coding: utf-8
"""公開サイト（gh-pages）で作られた画像を、手元の output/ へ取り込んで manifest.json を更新する。

目的: GitHub Actions が作った新しい初期値は公開側にしか無く、ローカル版（server.py）に出ない。
      これを取り込んで、ローカルでも同じ初期値を見られるようにする。
動作: 公開ブランチの先頭コミットが前回と同じなら何もしない（重い取り込みを避ける）。
      違えば ECMWF / GFS を順に取り込み、manifest.json を作り直す。
      GSM（ローカル専用）には触れない。

使用例:
  python sync_public.py            # 変更があれば取り込む（launchd・画面のボタンから呼ぶ）
  python sync_public.py --force    # 変更の有無にかかわらず取り込む
  python sync_public.py --prune-only --dry-run   # 古い初期値の整理だけ（消す対象を表示するだけ）

整理: 手元の output/{モデル}/ のうち、初期時刻が --keep-days（既定3日）より古いディレクトリを削除する（ECMWF/GFS のみ。
      GSM は update_latest.py の --keep-days が担当）。公開側（gh-pages）や元データには影響しない。
"""
import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import make_charts
import nwp_data
import publish

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "logs" / "sync_state.json"
MODELS = [m for m, c in nwp_data.MODELS.items() if not c.get("local_only")]


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def prune(keep_days, dry_run=False):
    """初期時刻が keep_days 日より古い手元の初期値ディレクトリを削除する（公開対象モデルのみ）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    n = 0
    for m in MODELS:
        d = ROOT / "output" / m
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if not (sub.is_dir() and len(sub.name) == 10 and sub.name.isdigit()):
                continue
            if nwp_data.parse_init(sub.name) < cutoff:
                n += 1
                log(f"古い初期値を削除{' [dry-run]' if dry_run else ''}: {m}/{sub.name}")
                if not dry_run:
                    shutil.rmtree(sub)
    return n


def main():
    ap = argparse.ArgumentParser(description="公開済みの画像を手元へ取り込む")
    ap.add_argument("--force", action="store_true", help="変更の有無にかかわらず取り込む")
    ap.add_argument("--keep-days", type=float, default=3, help="手元に残す日数（初期時刻基準。既定3。0で整理しない）")
    ap.add_argument("--prune-only", action="store_true", help="取り込まず、古い初期値の整理だけ行う")
    ap.add_argument("--dry-run", action="store_true", help="整理の対象を表示するだけ（削除しない）")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="gh-pages")
    a = ap.parse_args()
    if a.prune_only:
        prune(a.keep_days, a.dry_run)
        make_charts.write_manifest()
        return 0
    tip = None
    for k in range(4):  # スリープ復帰直後はネットワークが未接続のことがあるため、20秒おきに最大4回試す
        tip = publish.fetch_tip(a.remote, a.branch)
        if tip is not None:
            break
        if k < 3:
            time.sleep(20)
    if tip is None:
        log("公開ブランチを取得できません（ネットワークを確認）")
        return 1
    last = json.loads(STATE.read_text(encoding="utf-8")).get("tip") if STATE.exists() else None
    if tip == last and not a.force:
        log("公開側に変更はありません")
        if a.keep_days > 0 and prune(a.keep_days):
            make_charts.write_manifest()          # 古い初期値を消したら一覧も更新
        return 0
    for m in MODELS:
        publish.pull(SimpleNamespace(remote=a.remote, branch=a.branch, model=m))
    if a.keep_days > 0:
        prune(a.keep_days)
    make_charts.write_manifest()
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"tip": tip, "synced": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    log("取り込み完了（manifest.json を更新しました）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
