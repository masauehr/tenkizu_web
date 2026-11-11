#!/usr/bin/env python
# coding: utf-8
"""ローカルのディスク使用量を制限し、多くなったら通知する。

対象と方針（上限は disk_limits.json で変えられる。無ければ既定値）:
  grib    data/ecmwf・data/gfs の GRIB2（自由選択で取得したもの。自動では消えない）
          → 上限を超えたら、**古いファイルから**削除する（新しいものは残す）
  custom  output/custom/ の画像（自由選択で作ったもの。作り直せる）
          → 上限を超えたら、古いものから削除する
  gsm     data/gsm の GSM の取得データ（launchd が3日で自動削除。作図に必要なので上限は**警告だけ**）
  output  output/ の作図済み画像（ecmwf・gfs・gsm。古い初期値は自動で整理される。警告だけ）
  空き    ボリュームの空き容量（少なくなったら警告。さらに少ないと自由選択の取得を止める）

通知は macOS の通知センター（osascript）。同じ内容は 24 時間に1回まで。
削除するのは上の「grib」と「custom」のファイル単位だけ（ディレクトリごとは消さない。空になった初期値のフォルダだけ消す）。

使用例:
  python disk_guard.py                # 使用量と上限を表示するだけ
  python disk_guard.py --prune        # 上限を超えている分を古い順に削除
  python disk_guard.py --prune --notify   # 削除・警告を通知する（launchd から）
  python disk_guard.py --test-notify  # 通知が出るかの確認だけ
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIMITS_FILE = ROOT / "disk_limits.json"
STATE_FILE = ROOT / "logs" / "disk_guard_state.json"
GB = 1024 ** 3
MB = 1024 ** 2
NOTIFY_INTERVAL = 24 * 3600

DEFAULTS = {
    "grib_gb": 3.0,        # data/ecmwf・data/gfs の GRIB2（自由選択の取得分）
    "custom_mb": 500.0,    # output/custom の画像
    "gsm_gb": 4.0,         # data/gsm（警告のみ）
    "output_gb": 2.0,      # output/ の作図済み画像（警告のみ）
    "free_warn_gb": 20.0,  # ボリュームの空きがこれより少ないと警告
    "free_min_gb": 5.0,    # これより少ないと、自由選択の新しい取得を止める
}


def limits():
    """上限（disk_limits.json があれば上書き）。"""
    v = dict(DEFAULTS)
    try:
        v.update({k: float(x) for k, x in json.loads(LIMITS_FILE.read_text(encoding="utf-8")).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return v


def _files(patterns):
    """(更新時刻, サイズ, パス) の一覧。patterns は ROOT からの glob。"""
    out = []
    for pat in patterns:
        for p in ROOT.glob(pat):
            try:
                if p.is_file():
                    st = p.stat()
                    out.append((st.st_mtime, st.st_size, p))
            except OSError:
                continue
    return out


def groups():
    lim = limits()
    return {
        "grib": dict(label="取得データ（ECMWF・GFS の GRIB2）", patterns=["data/ecmwf/**/*", "data/gfs/**/*"],
                     limit=lim["grib_gb"] * GB, prune=True),
        "custom": dict(label="自由選択の画像", patterns=["output/custom/*.png"], limit=lim["custom_mb"] * MB, prune=True),
        "gsm": dict(label="GSM の取得データ", patterns=["data/gsm/*"], limit=lim["gsm_gb"] * GB, prune=False),
        "output": dict(label="作図済みの画像（ECMWF・GFS・GSM）",
                       patterns=["output/ecmwf/**/*.png", "output/gfs/**/*.png", "output/gsm/**/*.png"],
                       limit=lim["output_gb"] * GB, prune=False),
    }


def report():
    """使用量・上限・状態の一覧。level: ok / over（上限超過）。"""
    items = {}
    for key, g in groups().items():
        fs = _files(g["patterns"])
        size = sum(s for _, s, _ in fs)
        items[key] = dict(label=g["label"], size=size, limit=g["limit"], files=len(fs), prune=g["prune"],
                          level="over" if size > g["limit"] else "ok")
    lim = limits()
    free = shutil.disk_usage(ROOT).free
    level = "min" if free < lim["free_min_gb"] * GB else ("warn" if free < lim["free_warn_gb"] * GB else "ok")
    return dict(items=items, free=free, free_warn=lim["free_warn_gb"] * GB, free_min=lim["free_min_gb"] * GB, free_level=level)


def prune_group(key, limit_bytes=None):
    """key（grib / custom）が上限を超えていれば、古いファイルから消して上限以下にする。(削除数, 解放バイト) を返す。"""
    g = groups()[key]
    if not g["prune"]:
        return 0, 0
    lim = g["limit"] if limit_bytes is None else limit_bytes
    fs = sorted(_files(g["patterns"]))          # 古い順
    total = sum(s for _, s, _ in fs)
    n = freed = 0
    for _, size, p in fs:
        if total <= lim:
            break
        try:
            p.unlink()
        except OSError:
            continue
        total -= size
        freed += size
        n += 1
        parent = p.parent                          # 空になった初期値のフォルダだけ消す（中身があれば消えない）
        if parent != ROOT / "data" and parent.parent.name in ("ecmwf", "gfs"):
            try:
                parent.rmdir()
            except OSError:
                pass
    return n, freed


def make_room(extra_bytes):
    """自由選択で GRIB2 を取得する前に呼ぶ。extra_bytes ぶんの余地ができるよう、古い取得データを消す。

    空きが free_min を下回るときは、取得してはいけないので False を返す（呼び出し側がエラーにする）。"""
    g = groups()["grib"]
    prune_group("grib", max(0.0, g["limit"] - extra_bytes))
    return shutil.disk_usage(ROOT).free - extra_bytes >= limits()["free_min_gb"] * GB


def fmt(b):
    return f"{b / GB:.2f}GB" if b >= GB else f"{b / MB:.0f}MB"


def messages(rep):
    """通知すべき内容（kind, 本文）の一覧。"""
    out = []
    for key, it in rep["items"].items():
        if it["level"] == "over":
            how = "古いものから自動で削除します" if it["prune"] else "削除は自動では行いません（手動で整理してください）"
            out.append((key, f"{it['label']}が上限を超えています（{fmt(it['size'])} / 上限 {fmt(it['limit'])}）。{how}"))
    if rep["free_level"] != "ok":
        out.append(("free", f"ディスクの空きが少なくなっています（{fmt(rep['free'])}）。"
                    + ("自由選択の新しい取得を止めています。" if rep["free_level"] == "min" else "")))
    return out


def notify(title, body):
    """macOS の通知センターに出す。"""
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def should_notify(kind, state, now=None):
    """同じ種類の通知は24時間に1回まで。"""
    now = time.time() if now is None else now
    return now - float(state.get(kind, 0)) >= NOTIFY_INTERVAL


def run(do_prune=False, do_notify=False):
    """削除（do_prune）と通知（do_notify）を行い、使用量の一覧を返す。"""
    pruned = []
    if do_prune:
        for key in ("grib", "custom"):
            n, freed = prune_group(key)
            if n:
                pruned.append((key, n, freed))
    rep = report()
    msgs = messages(rep)
    for key, n, freed in pruned:
        msgs.insert(0, ("pruned_" + key, f"{rep['items'][key]['label']}を古い順に {n} 件（{fmt(freed)}）削除しました。"))
    if do_notify and msgs:
        st = _state()
        sent = False
        for kind, body in msgs:
            if should_notify(kind, st):
                notify("tenkizu_web: ディスク", body)
                st[kind] = time.time()
                sent = True
        if sent:
            STATE_FILE.parent.mkdir(exist_ok=True)
            STATE_FILE.write_text(json.dumps(st), encoding="utf-8")
    return rep, msgs


def main():
    ap = argparse.ArgumentParser(description="ローカルのディスク使用量の確認・制限・通知", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prune", action="store_true", help="上限を超えている分を古い順に削除する")
    ap.add_argument("--notify", action="store_true", help="削除・警告を通知する（同じ内容は24時間に1回）")
    ap.add_argument("--test-notify", action="store_true", help="通知が出るかの確認だけ行う")
    a = ap.parse_args()
    if a.test_notify:
        notify("tenkizu_web: ディスク", "通知のテストです。")
        return 0
    rep, msgs = run(a.prune, a.notify)
    print("使用量（上限）:")
    for it in rep["items"].values():
        mark = "超過" if it["level"] == "over" else "OK"
        print(f"  [{mark:2}] {it['label']}: {fmt(it['size'])}（上限 {fmt(it['limit'])}・{it['files']}ファイル・{'自動削除' if it['prune'] else '警告のみ'}）")
    print(f"  [{ {'ok': 'OK', 'warn': '警告', 'min': '危険'}[rep['free_level']] :2}] ディスクの空き: {fmt(rep['free'])}（警告 {fmt(rep['free_warn'])}・取得停止 {fmt(rep['free_min'])}）")
    for _, body in msgs:
        print("  ・" + body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
