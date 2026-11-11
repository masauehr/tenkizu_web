#!/usr/bin/env python
# coding: utf-8
"""公開用の静的サイト（_site/）を組み立てる。GitHub Pages などへそのまま置ける。

  _site/index.html, board/, app.js ...   ← web/
  _site/output/{model}/{初期値}/...png   ← output/（manifest.json 含む）

使用例: python build_site.py            # → _site/
        python build_site.py --out dist
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description="公開用の静的サイトを組み立てる")
    p.add_argument("--out", default="_site")
    if any(a in sys.argv[1:] for a in ("?", "-?", "--?")):
        p.print_help()
        return
    a = p.parse_args()
    out = ROOT / a.out
    if not (ROOT / "output" / "manifest.json").exists():
        print("output/manifest.json がありません。先に make_charts.py / update_latest.py を実行してください")
        sys.exit(1)
    # 既存のコピー先は上書き（ディレクトリごとの削除はしない。古い画像は残るので、CIのように空の状態から使う）
    shutil.copytree(ROOT / "web", out, dirs_exist_ok=True)
    # ローカル専用モデル（GSM）と、自由選択で作った画像（output/custom）は公開用に含めない
    sys.path.insert(0, str(ROOT))
    import nwp_data
    local_only = {m for m, v in nwp_data.MODELS.items() if v.get("local_only")}
    base_ignore = shutil.ignore_patterns(".gitkeep", ".update.lock", "*.part")

    def ignore(d, names):
        skip = set(base_ignore(d, names))
        if Path(d).resolve() == (ROOT / "output").resolve():
            skip |= {n for n in names if n in local_only or n == "custom"}   # custom=ローカルの自由選択で作った画像（公開しない）
        return skip

    shutil.copytree(ROOT / "output", out / "output", dirs_exist_ok=True, ignore=ignore)
    # manifest も公開用に作り直す（GSMを載せない）
    import make_charts
    make_charts.write_manifest(out / "output", public_only=True)
    (out / ".nojekyll").write_text("")  # GitHub Pages でJekyll処理を無効化
    n = sum(1 for _ in (out / "output").rglob("*.png"))
    size = sum(f.stat().st_size for f in (out / "output").rglob("*") if f.is_file()) / 1e6
    print(f"{out} を作成: 画像 {n} 枚、output {size:.0f}MB")


if __name__ == "__main__":
    main()
