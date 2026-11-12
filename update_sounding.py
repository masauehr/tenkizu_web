#!/usr/bin/env python
# coding: utf-8
"""実況エマグラム（気象庁の高層気象観測データ）を、直近の観測時刻ぶんまとめて作る（公開サイト用・GitHub Actions から）。

  python update_sounding.py                  # 直近6観測時刻（約3日分）× 全地点。作成済みの図は取りにいかない
  python update_sounding.py --times 2        # 直近2観測時刻だけ
  python update_sounding.py --stations 47918,47646 --force   # 地点を絞る・作成済みも作り直す

出力: output/sounding/<観測時刻 YYYYMMDDHH>/<WMO番号>_emagram.png と _pt.png、output/sounding/manifest.json。
取得元は気象庁「過去の気象データ検索（高層）」（sounding.py の jma）。観測から公開までは半日〜1日ほどかかるので、
**まだ公開されていない時刻は、先頭の2地点だけ試して見分け、残りの地点は取りにいかない**（無駄な要求を避ける）。
取得できない（欠測・公開前）時刻・地点は飛ばし、次回の実行で取り直す（失敗にしない）。
配信元への負荷を抑えるため、要求の間隔を空け、作成済みは取りにいかない。公開は publish.py --sounding。
"""
import argparse
import sys
import time
from pathlib import Path

import requests

import sounding as snd

ROOT = Path(__file__).resolve().parent


def run(times, stations, root=snd.SOUNDING_DIR, force=False, delay=2.0, session=None, fetch=None, render=None):
    """作図の本体。fetch・render はテストで差し替える。返り値: dict(made, skipped, missing, errors, not_yet)。
    not_yet: まだ公開されていないとみなした観測時刻（先頭の2地点のどちらも無かった時刻）。"""
    fetch = fetch or snd.fetch
    render = render or snd.render
    root = Path(root)
    res = {"made": 0, "skipped": 0, "missing": [], "errors": [], "not_yet": []}
    ERR = object()                                             # 取得でエラーになった印（None＝データ無し と区別する）
    for dt in times:
        tdir = root / f"{dt:%Y%m%d%H}"
        targets = {w: {m: tdir / f"{w}_{m}.png" for m in snd.MODES} for w in stations}
        todo = [w for w in stations if force or not all(p.exists() for p in targets[w].values())]
        res["skipped"] += len(stations) - len(todo)
        if not todo:
            continue

        def get(wmo):
            tag = f"{dt:%Y-%m-%d %H}Z {wmo}"
            try:
                d = fetch(dt, wmo, session)
            except snd.SoundingError as e:
                res["errors"].append(f"{tag}: {e}")
                print(f"  取得エラー {tag}: {e}", flush=True)
                d = ERR
            time.sleep(delay)                                   # 配信元に負荷をかけない
            return d

        got = {}
        for w in todo[:2]:                                      # 公開前の時刻を、少ない要求で見分ける
            got[w] = get(w)
            if got[w] is not None and got[w] is not ERR:
                break
        if len(got) == min(2, len(todo)) and all(v is None for v in got.values()):
            res["not_yet"].append(f"{dt:%Y-%m-%d %H}Z")
            print(f"  まだ公開されていない {dt:%Y-%m-%d %H}Z（先頭の{len(got)}地点が無い）", flush=True)
            continue
        for w in todo:
            d = got.pop(w) if w in got else get(w)
            tag = f"{dt:%Y-%m-%d %H}Z {w}"
            if d is ERR:
                continue
            if d is None:
                res["missing"].append(tag)
                print(f"  なし {tag}（欠測・未収録）", flush=True)
                continue
            try:
                for m, p in targets[w].items():
                    render(d, dt, w, m, p)
            except Exception as e:                              # 1枚の描画の失敗で全体を止めない
                res["errors"].append(f"{tag}: 描画に失敗 {e!r}")
                print(f"  描画エラー {tag}: {e!r}", flush=True)
                continue
            res["made"] += 1
            print(f"  作成 {tag}", flush=True)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="実況エマグラム（気象庁の高層気象観測データ）を作る", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--times", type=int, default=6, help="直近の観測時刻（00/12UTC）の数（既定 6＝約3日分。公開もこの数だけ残す）")
    ap.add_argument("--stations", default="", help="WMO番号をカンマ区切り（既定: 全地点）")
    ap.add_argument("--force", action="store_true", help="作成済みの図も作り直す")
    ap.add_argument("--delay", type=float, default=2.0, help="配信元への要求の間隔[秒]（既定 2）")
    ap.add_argument("--min-age", type=float, default=3.0, help="観測から何時間経った時刻から対象にするか（既定 3。公開前の時刻は自動で見分けて飛ばす）")
    ap.add_argument("--out", default=str(snd.SOUNDING_DIR), help="出力先（既定 output/sounding）")
    a = ap.parse_args(argv)

    stations = [s.strip() for s in a.stations.split(",") if s.strip()] or list(snd.STATIONS)
    bad = [s for s in stations if s not in snd.STATIONS]
    if bad:
        ap.error(f"未登録の地点: {', '.join(bad)}（{', '.join(snd.STATIONS)}）")
    if a.times < 1:
        ap.error("--times は 1 以上")
    times = snd.obs_times(count=a.times, min_age_hours=a.min_age)
    print(f"対象: {len(stations)} 地点 × 観測 {len(times)} 時刻（{times[-1]:%m/%d %HZ}〜{times[0]:%m/%d %HZ}）", flush=True)

    with requests.Session() as session:
        res = run(times, stations, a.out, a.force, a.delay, session)
    gone = snd.prune(a.out, a.times)
    changed = snd.write_manifest(a.out)
    print(f"作成 {res['made']} ／ 作成済みで省略 {res['skipped']} ／ 欠測 {len(res['missing'])} ／ 公開前の時刻 {len(res['not_yet'])} ／ エラー {len(res['errors'])} ／ "
          f"古い時刻を削除 {len(gone)} ／ manifest {'更新' if changed else '変更なし'}", flush=True)
    if res["errors"] and res["made"] == 0 and res["skipped"] == 0:
        print("すべて失敗しました（気象庁のサイトの障害・形式の変更の可能性）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
