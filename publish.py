#!/usr/bin/env python
# coding: utf-8
"""公開用ブランチ（既定: gh-pages = GitHub Pages の配信元）へ、画像と画面ファイルを反映する。

目的:
  - 画面（web/）だけを直したときは、画像を作らずに画面ファイルだけを差し替える（--web-only）
  - モデルごとに別々に作図した結果を、他のモデルの画像に触れずに反映する（--model）
  どちらも、公開ブランチの現在の内容を土台にして必要な部分だけを入れ替える。

公開ブランチの構成: index.html, app.js, style.css, board/ ...（= web/）と output/{model}/{初期値}/... と output/manifest.json

使用例:
  python publish.py --web-only                 # 画面ファイルだけを反映（画像・manifestは触らない）
  python publish.py --model gfs                # output/gfs の最新2初期値と画面ファイルを反映し、manifestを再生成
  python publish.py --model gfs --keep 3       # 残す初期値の数を変える（既定は nwp_data.MODELS の publish_keep と publish_hours）
  python publish.py --pull --model gfs         # 公開済みの output/gfs を手元の output/ へ取り込む（作成済み判定のため）
  python publish.py --web-only --dry-run       # 何をするかだけ表示（pushしない）
  python publish.py ? / -? / --?               # ヘルプ

仕組み:
  - 公開ブランチを git worktree で取り出して差分だけ入れ替え、**1コミットの孤立コミット**にして強制pushする
    （履歴を積まないので容量が増え続けない。pushされるのは新しい画像だけで、変わらない画像は再送されない）。
  - 強制pushは --force-with-lease（取得した時点の先頭と一致する場合のみ）。別のジョブと衝突したら取り直して再実行する。
  - コミットの作者は bot（ユーザーのメールアドレスは使わない）。
"""
import argparse
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOT = ["-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]


def git(*args, cwd=ROOT, check=True, capture=True):
    r = subprocess.run(["git", *args], cwd=cwd, check=False, text=True,
                       stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} が失敗: {(r.stderr or '').strip()}")
    return r


def parse_args():
    p = argparse.ArgumentParser(description="公開ブランチへ画像・画面ファイルを反映する",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--model", help="反映するモデル（ecmwf / gfs）。--pull では取り込むモデル")
    p.add_argument("--web-only", action="store_true", help="画面ファイル(web/)だけを反映する（画像は作らない・触らない）")
    p.add_argument("--pull", action="store_true", help="公開済みの output/{model} を手元の output/ へ取り込む")
    p.add_argument("--keep", type=int, default=None, help="公開する初期値の数（モデルごと。既定: nwp_data.MODELS の publish_keep）")
    p.add_argument("--remote", default="origin", help="公開先のリモート名（既定: origin）")
    p.add_argument("--branch", default="gh-pages", help="公開ブランチ（既定: gh-pages）")
    p.add_argument("--custom", nargs="+", metavar="画像名", help="自由選択で作った画像（output/custom/ の PNG）を公開サイトに載せる（直近10件まで）")
    p.add_argument("--custom-remove", nargs="+", metavar="画像名", help="公開サイトに載せた自由選択の画像を外す")
    p.add_argument("--dry-run", action="store_true", help="pushせず、変更の有無だけ表示")
    if any(a in sys.argv[1:] for a in ("?", "-?", "--?")):
        p.print_help()
        sys.exit(0)
    a = p.parse_args()
    if a.model:
        sys.path.insert(0, str(ROOT))
        import nwp_data  # 軽い依存のみ（requests）
        if a.model not in nwp_data.MODELS:
            p.error(f"不明なモデル: {a.model}")
        if nwp_data.MODELS[a.model].get("local_only"):
            # ローカル専用モデル（GSM）は公開しない。誤って指定しても拒否する（二重の防止）
            p.error(f"{a.model} はローカル専用モデルのため、公開ブランチへは反映できません")
    if a.pull and not a.model:
        p.error("--pull には --model が必要です")
    for n in (a.custom or []) + (a.custom_remove or []):
        if not re.fullmatch(r"[A-Za-z0-9_]+\.png", n):
            p.error(f"画像名が不正です: {n}")
    if not a.pull and not a.web_only and not a.model and not a.custom and not a.custom_remove:
        p.error("--model・--web-only・--custom・--custom-remove のどれかを指定してください")
    return a


def fetch_tip(remote, branch):
    """公開ブランチの先頭コミット（無ければ None）を取得する。"""
    r = git("fetch", "--depth", "1", remote, branch, check=False)
    if r.returncode != 0:
        return None
    return git("rev-parse", "FETCH_HEAD").stdout.strip()


def make_worktree(tip):
    """公開ブランチの内容を一時ディレクトリへ取り出す（ブランチが無ければ空）。"""
    wt = Path(tempfile.mkdtemp(prefix="publish_wt_"))  # 空のディレクトリ（git worktree add は空なら受け付ける）
    git("worktree", "add", "--detach", str(wt), tip or "HEAD")
    if tip is None:  # 初回: 空にする（mainの内容を引き継がない）
        git("rm", "-r", "-q", "--ignore-unmatch", ".", cwd=wt)
        for f in list(wt.iterdir()):
            if f.name != ".git":
                shutil.rmtree(f) if f.is_dir() else f.unlink()
    return wt


def remove_worktree(wt, tmp_branch=None):
    git("worktree", "remove", "--force", str(wt), check=False)
    git("worktree", "prune", check=False)
    if tmp_branch:  # 孤立コミット用に作った一時ブランチを残さない
        git("branch", "-D", tmp_branch, check=False)


def unlink_files(d: Path):
    """ディレクトリ配下のファイルだけを削除し、空になったディレクトリを片付ける。"""
    if not d.exists():
        return
    for f in sorted(d.rglob("*"), reverse=True):
        if f.is_file() or f.is_symlink():
            f.unlink()
        elif f.is_dir():
            try:
                f.rmdir()
            except OSError:
                pass


def sync_web(wt: Path):
    """web/ を公開ブランチの直下へ反映（output/ は触らない）。web/ に無くなったファイルは削除する。"""
    src = ROOT / "web"
    want = {p.relative_to(src) for p in src.rglob("*") if p.is_file()}
    for f in list(wt.rglob("*")):
        rel = f.relative_to(wt)
        if rel.parts[0] in (".git", "output") or not f.is_file() or rel.name == ".nojekyll":
            continue
        if rel not in want:
            f.unlink()
    for rel in want:
        dst = wt / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dst)
    (wt / ".nojekyll").write_text("")  # GitHub Pages でJekyll処理を無効化


def newest_inits(model_dir: Path, keep: int, hours=None):
    """新しい順に keep 個。hours（初期時刻UTCの集合）を指定すると、その時刻の初期値だけを数える。"""
    names = [d.name for d in model_dir.iterdir() if d.is_dir() and re.fullmatch(r"\d{10}", d.name)]
    if hours:
        names = [n for n in names if int(n[-2:]) in hours]
    return sorted(names, reverse=True)[:keep]


def sync_model(wt: Path, model: str, keep: int):
    """output/{model} の最新 keep 個の初期値を公開側へ入れ替える（他のモデルには触れない）。"""
    src = ROOT / "output" / model
    if not src.is_dir():
        raise RuntimeError(f"output/{model} がありません（先に作図してください）")
    import nwp_data
    cfg = nwp_data.MODELS.get(model, {})
    inits = newest_inits(src, keep or cfg.get("publish_keep", 2), cfg.get("publish_hours"))
    if not inits:
        raise RuntimeError(f"output/{model} に初期値のディレクトリがありません")
    dst = wt / "output" / model
    unlink_files(dst)
    for init in inits:
        shutil.copytree(src / init, dst / init, ignore=shutil.ignore_patterns(".gitkeep", "*.part"))
    return inits


CUSTOM_MAX = 10   # 公開サイトに載せる自由選択の画像の最大件数


def sync_custom(wt: Path, add, remove):
    """自由選択の画像を公開側の output/custom/ へ載せる・外す。list.json（新しい順・最大10件）を作り直す。
    他のフォルダ（モデルの画像・画面ファイル）には触れない。公開した画像の名前の一覧を返す。"""
    import json
    sys.path.insert(0, str(ROOT))
    import custom
    dst = wt / "output" / "custom"
    dst.mkdir(parents=True, exist_ok=True)
    lp = dst / "list.json"
    try:
        old = json.loads(lp.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        old = []
    local = {Path(x["url"]).name: x for x in custom.list_recent(10_000)}
    new = []
    for name in add:
        e = local.get(name)
        if e is None:
            raise RuntimeError(f"{name} が output/custom/ にありません")
        if "条件は不明" in e["label"]:
            raise RuntimeError(f"{name} は条件が分からない（古い）画像のため公開できません。作図し直してください")
        shutil.copy2(custom.CUSTOM_DIR / name, dst / name)
        new.append({k: v for k, v in e.items() if k != "published"})     # ローカル専用の項目は載せない
    skip = set(add) | set(remove or [])
    items = (new + [x for x in old if Path(x["url"]).name not in skip])[:CUSTOM_MAX]
    want = {Path(x["url"]).name for x in items}
    for f in dst.glob("*.png"):           # 一覧から外れた画像は公開側からも消す
        if f.name not in want:
            f.unlink()
    lp.write_text(json.dumps({"items": items, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                             ensure_ascii=False), encoding="utf-8")
    return [Path(x["url"]).name for x in items]


def record_published(names):
    """公開した画像の名前を、手元の output/custom/published.json に記録する（自由選択の一覧に「公開中」を出すため）。"""
    import json
    d = ROOT / "output" / "custom"
    d.mkdir(parents=True, exist_ok=True)
    (d / "published.json").write_text(json.dumps({"names": names}, ensure_ascii=False), encoding="utf-8")


def regenerate_manifest(wt: Path):
    """公開側の output/ を走査して manifest.json を作り直す（重い依存はここで初めて読み込む）。
    中身が前回と同じ（更新時刻 updated だけが違う）なら、前回のものに戻して無駄なコミットを避ける。"""
    import json
    mf = wt / "output" / "manifest.json"
    old_text = mf.read_text(encoding="utf-8") if mf.exists() else None
    sys.path.insert(0, str(ROOT))
    import make_charts
    make_charts.write_manifest(wt / "output", public_only=True)  # ローカル専用モデル（GSM）は載せない
    if old_text is not None:
        try:
            old, new = json.loads(old_text), json.loads(mf.read_text(encoding="utf-8"))
            old.pop("updated", None)
            new.pop("updated", None)
            if old == new:
                mf.write_text(old_text, encoding="utf-8")
        except ValueError:
            pass


def publish(a):
    custom_mode = bool(a.custom or a.custom_remove)   # 自由選択の画像だけを反映する（画面ファイルやモデルの画像には触れない）
    published = []
    for attempt in range(1, 6):
        tip = fetch_tip(a.remote, a.branch)
        wt = make_worktree(tip)
        tmp_branch = f"publish-tmp-{os.getpid()}-{attempt}"
        try:
            if custom_mode:
                published = sync_custom(wt, a.custom or [], a.custom_remove or [])
                note = f"自由選択の断面図・エマグラム（公開 {len(published)} 件）"
            else:
                sync_web(wt)
                note = "画面ファイルのみ"
            if a.model and not a.web_only and not custom_mode:
                inits = sync_model(wt, a.model, a.keep)
                regenerate_manifest(wt)
                note = f"{a.model} {', '.join(inits)} と画面ファイル"
            git("add", "-A", cwd=wt)
            if tip is not None and git("diff", "--cached", "--quiet", cwd=wt, check=False).returncode == 0:
                print("公開ブランチに変更はありません（何も反映しません）")
                if custom_mode:
                    record_published(published)
                return 0
            n = len(git("diff", "--cached", "--name-only", cwd=wt).stdout.split())
            print(f"反映内容: {note}（変更ファイル {n} 個）", flush=True)
            if a.dry_run:
                print("[dry-run] pushしません")
                return 0
            git("checkout", "-q", "--orphan", tmp_branch, cwd=wt)
            git(*BOT, "commit", "-q", "-m", f"publish: {note}", cwd=wt)
            lease = f"--force-with-lease={a.branch}:{tip or ''}"
            r = git("push", lease, a.remote, f"{tmp_branch}:{a.branch}", cwd=wt, check=False)
            if r.returncode == 0:
                print(f"公開ブランチ {a.branch} へ反映しました", flush=True)
                if custom_mode:
                    record_published(published)
                return 0
            print(f"push が競合/失敗（{attempt}/5）: {(r.stderr or '').strip().splitlines()[-1:]}", flush=True)
        finally:
            remove_worktree(wt, tmp_branch)
        time.sleep(random.uniform(2, 8))  # 別ジョブと同時に公開した場合は少し待って取り直す
    print("5回試しても反映できませんでした")
    return 1


def pull(a):
    """公開済みの output/{model} を手元の output/ へ取り込む（作成済み判定・既存画像の再利用のため）。"""
    tip = fetch_tip(a.remote, a.branch)
    if tip is None:
        print("公開ブランチがまだありません（取り込むものがありません）")
        return 0
    wt = make_worktree(tip)
    try:
        src = wt / "output" / a.model
        if not src.is_dir():
            print(f"公開側に output/{a.model} がありません")
            return 0
        dst = ROOT / "output" / a.model
        shutil.copytree(src, dst, dirs_exist_ok=True)
        n = sum(1 for _ in dst.rglob("*.png"))
        print(f"公開済みの output/{a.model} を取り込みました（画像 {n} 枚）")
    finally:
        remove_worktree(wt)
    return 0


def main():
    a = parse_args()
    sys.exit(pull(a) if a.pull else publish(a))


if __name__ == "__main__":
    main()
