# 公開の手順（GitHub Pages・gh-pages 方式）

仕組みは [nwp_chart_web の PUBLISH.md](https://github.com/masauehr/nwp_chart_web/blob/main/PUBLISH.md) と同じです（CIで作図 → 画像だけを `gh-pages` ブランチへ1コミットで反映 → Pages で配信）。
**2026-11-11 に公開を開始**。定期実行は 12Z初期値が揃う時間帯（UTC 17〜23時の :30、1日7回）、`web/**` の push は自動反映。

## 公開するもの・しないもの
- 公開: ECMWF・GFS（`nwp_data.MODELS` の `publish_hours` / `publish_keep`）。地域は日本周辺・北半球・鉛直断面（固定: 130E・140E）・エマグラム（固定5地点）。
- 公開しない: GSM（`local_only`）、`output/custom/`（自由選択で作った画像。`build_site.py` が除外）。`publish.py` は GSM の指定を拒否する。
- `web/custom.html`（自由選択）は静的サイトでは動かない（`/api/custom` が無い）。ローカルサーバーでだけ使う。

## 公開を始めたときの手順（記録。2026-11-11 実施済み）
1. GitHub の Settings → Pages で、配信元を `gh-pages` ブランチ（/ root）にする。
2. `.github/workflows/update-model.yml` の `schedule:`（または cron-job.org からの `workflow_dispatch`）を有効にする。時刻は nwp_chart_web の設定（UTC 1/4/6/7/9/10/17/18/20/22/23時の :30）が参考になる。
3. `.github/workflows/deploy-web.yml` の `push:` を有効にすると、`web/**` の修正が画像なしで反映される。
4. 初回は手動実行（Actions → update-model → Run workflow）で動作を確認する。
5. 容量: 1初期値 約20MB（日本周辺5図種＋北半球2＋断面2＋エマグラム5 × 13コマ）。Pages は1GB目安なので、公開個数を増やす前に見積もる。

## 自由選択の図を公開サイトに載せる（任意）
ローカルで作った断面図・エマグラムを、直近10件まで公開サイトに載せられる（自由選択のページの「公開サイトに載せる」。2回押して確定）。公開版では通常版の右の図に並べて見られる。
```bash
python publish.py --custom <画像名.png ...> [--dry-run]   # 載せる（output/custom/ の画像。条件ファイル .json が要る）
python publish.py --custom-remove <画像名.png ...>         # 外す
```
- 反映先は gh-pages の `output/custom/`（`list.json` と画像）だけ。モデルの画像・画面ファイルには触れない。`list.json` は新しい順・最大10件。
- 公開した名前は手元の `output/custom/published.json` に記録される（自由選択のページの【公開中】の表示に使う）。

## ローカルで公開物を確かめる
```bash
/opt/anaconda3/envs/met_env_310/bin/python build_site.py --out /tmp/site_check   # _site 相当を作る
/opt/anaconda3/envs/met_env_310/bin/python publish.py --web-only --dry-run        # pushせず内容だけ表示
```

## 作り直し（図の見た目を直したとき）
Actions → update-model → Run workflow で `force` にチェック。作成済みの初期値も作り直して公開する（約30〜60分）。

## ローカルの定期更新（launchd）
```bash
cp launchd/com.masahiro.tenkizu_web_sync.plist launchd/com.masahiro.tenkizu_web_gsm.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.masahiro.tenkizu_web_sync.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.masahiro.tenkizu_web_gsm.plist
# 止める: launchctl bootout gui/$(id -u)/com.masahiro.tenkizu_web_sync
```
- sync: 公開側（ECMWF・GFS）の新しい画像を30分おきに `output/` へ取り込む（変更が無ければ数秒）。ログ `logs/sync.log`。
- gsm: GSM（ローカル専用）を 00/12Z だけ取得・作図（初回は約600MB・十数分）。3日より古いものは削除。ログ `logs/gsm_auto.log`。
- nwp_chart_web の GSM 取得と別々に動く（GSM のデータは重複して保管される。必要なら片方を止める）。

## ディスク使用量のチェック（launchd・2026-11-11 登録済み）
自由選択で取得したデータが増え続けないよう、6時間おきに上限超過分を古い順に削除し、超過・空き不足を通知する。
```bash
cp launchd/com.masahiro.tenkizu_web_disk.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.masahiro.tenkizu_web_disk.plist
# 止める: launchctl bootout gui/$(id -u)/com.masahiro.tenkizu_web_disk
```
ログは `logs/disk_guard.log`。上限・通知の詳細はマニュアル（tenkizu-web.md）の「ディスク使用量の制限と通知」。登録していない環境でも、自由選択の取得の直前と作図後の整理は動く（定期チェックと通知だけが止まる）。
