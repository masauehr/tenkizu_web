# 公開の手順（GitHub Pages・gh-pages 方式）

仕組みは [nwp_chart_web の PUBLISH.md](https://github.com/masauehr/nwp_chart_web/blob/main/PUBLISH.md) と同じです（CIで作図 → 画像だけを `gh-pages` ブランチへ1コミットで反映 → Pages で配信）。
**2026-11-11 に公開を開始**。定期実行は 12Z初期値が揃う時間帯（UTC 17〜23時の :30、1日7回）、`web/**` の push は自動反映。

## 公開するもの・しないもの
- 公開: ECMWF・GFS（`nwp_data.MODELS` の `publish_hours` / `publish_keep`）。地域は日本周辺・北半球・鉛直断面（固定: 130E・140E）・エマグラム（固定5地点）。
- 公開しない: GSM（`local_only`）、`output/custom/`（自由選択で作った画像。`build_site.py` が除外）。`publish.py` は GSM の指定を拒否する。
- `web/custom.html`（自由選択）は静的サイトでは動かない（`/api/custom` が無い）。ローカルサーバーでだけ使う。

## 公開を始めるとき（ユーザーの判断で行う）
1. GitHub の Settings → Pages で、配信元を `gh-pages` ブランチ（/ root）にする。
2. `.github/workflows/update-model.yml` の `schedule:`（または cron-job.org からの `workflow_dispatch`）を有効にする。時刻は nwp_chart_web の設定（UTC 1/4/6/7/9/10/17/18/20/22/23時の :30）が参考になる。
3. `.github/workflows/deploy-web.yml` の `push:` を有効にすると、`web/**` の修正が画像なしで反映される。
4. 初回は手動実行（Actions → update-model → Run workflow）で動作を確認する。
5. 容量: 1初期値 約20MB（日本周辺5図種＋北半球2＋断面2＋エマグラム5 × 13コマ）。Pages は1GB目安なので、公開個数を増やす前に見積もる。

## ローカルで公開物を確かめる
```bash
/opt/anaconda3/envs/met_env_310/bin/python build_site.py --out /tmp/site_check   # _site 相当を作る
/opt/anaconda3/envs/met_env_310/bin/python publish.py --web-only --dry-run        # pushせず内容だけ表示
```
