# CLAUDE.md — tenkizu_web

## 概要
tenkizu の CLI 作図（天気図・断面図・エマグラム）を Web で見られるようにする。公開サイト https://masauehr.github.io/tenkizu_web/ 。マニュアルは [tenkizu-web.md](tenkizu-web.md)、設計・経緯は [DESIGN.md](DESIGN.md)、運用は [PUBLISH.md](PUBLISH.md)。
**nwp_chart_web に無い図だけ**を作る（重複する図は作らない）。nwp_chart_web とは**共通部品化しない**（コピーして調整。各リポジトリは単独で動く）。

## 実行環境
- 画面を直して確認するとき: **古いサーバーが残っていると、新しいサーバーはポート使用中で起動に失敗し、新機能が効かないように見える**。`lsof -nP -iTCP:8791 -sTCP:LISTEN` で待ち受けているプロセスを確認してから起動する。URL の `#` 以降だけを変えても再読み込みされない。
- Python: `/opt/anaconda3/envs/met_env_310/bin/python`（pygrib・cartopy 0.25・metpy・scipy・pytest）。`python3` は別の環境になるので使わない。
- **CI は cartopy 0.26・pip の pygrib**。手元と挙動が違う（過去に北半球図の範囲・ECMWF の経度で崩れた）。図の見た目を変えたら `uv venv --python 3.11` ＋ `requirements.txt` の環境でも確認する。
- テスト: `python -m pytest tests`。ブラウザの確認は Chrome（画面の修正は公開サイトにも自動反映されるので push 前に手元で確認）。

## 規約
- コメント・UI は日本語。インデント: Python 4、JS/HTML/CSS 2。f-string 内で同じ種類の引用符を入れ子にしない（Python 3.10）。
- 図種は `nwp_charts.py` の `CHARTS`、地域は `REGIONS`。断面は `xsection.SECTIONS`、エマグラム地点は `emagram.STATIONS`。追加したらマニュアル（下記）も更新する。
- `data/`（取得 GRIB2）・`output/`・`_site/`・`logs/` は git 除外。

## 守ること
- **GSM は公開しない**（`nwp_data.MODELS["gsm"]` の `local_only`。`publish.py`・`build_site.py`・`sync_public.py` で除外している。外さない）。`output/custom/`（自由選択の画像）も公開しない。
- 経度は 0〜360 に正規化して扱う（`nwp_data._read`）。ECMWF のオープンデータは −180〜180 度で来ることがある。
- 北半球図（`render_polar`）の範囲は投影座標で指定する（cartopy 0.26 対応）。
- 画用紙版の保存キーは `tenkizuBoard.v1`。**同じドメイン（masauehr.github.io）の nwp_chart_web と localStorage を共有する**ため、キー名を共通にしない。
- ローカルのディスク: 自由選択で取得した GRIB2（`data/ecmwf`・`data/gfs`）と `output/custom` は `disk_guard.py` が上限を超えた分を古い順に削除する。**GSM のデータ（`data/gsm`）と作図済み画像は消さない**（警告のみ）。消す対象を増やすときは `groups()` の `prune` を確認する。
- 自由選択の「最近の作図」は、`output/custom/` の画像と同名の `.json`（条件）から `/api/custom/list` が作る。ブラウザの localStorage に履歴を持たせない（別のブラウザで見えなくなる）。画像を消す処理は `.json` も一緒に消すこと。
- 通常版の「別の図を並べる」（`web/app.js`。右に別の地域の図／自由選択の直近10件。公開サイトは `output/custom/list.json` の公開分。2モデル比較とは同時に使えない）。nwp_chart_web にも同じ機能があるが、**別々にコピーしてある**（直すときは両方を見る）。
- **公開版では新しい断面図・エマグラムは作れない**（Pages は静的。作図は Python が要る）。代わりに、ローカルで作った図を `publish.py --custom` で gh-pages の `output/custom/`（直近10件）に載せ、公開版でも右の図に並べて見られる。公開は画面で2回押して確定した画像だけ（自動では載せない）。公開した名前は手元の `output/custom/published.json` に記録する。`sync_web`・`sync_model` は `output/custom/` に触れないこと。
- ローカルサーバーの既定ポートは **8791**（nwp_chart_web は 8790。同時に動かせるよう分けてある）。
- 公開サイトの容量は 1GB が目安（1初期値 約20MB）。公開個数・図種・地域を増やす前に見積もる。
- 図の見た目を変えたら、公開側の既存画像は作り直さないと変わらない（Actions の update-model を `force=true` で手動実行）。
- 公開ブランチ（gh-pages）への反映・Pages の設定・定期実行の時刻変更は、ユーザーの確認を取ってから行う。

## ドキュメント更新ルール
- 機能追加・変更をしたら、**マニュアル [tenkizu-web.md](tenkizu-web.md) の該当箇所と改修履歴**を更新する。
- マニュアルは `~/projects/pc_docs/manuals/automation/tenkizu-web.md` にも**同じ内容でコピー**する（両方を更新）。`pc_docs/README.md` の表の説明も、内容が変わったら直す。
- 設計判断・不具合の原因と対処は [DESIGN.md](DESIGN.md) に記録する。運用手順の変更は [PUBLISH.md](PUBLISH.md)。

## 定期処理（登録済み）
- GitHub Actions `update-model`: UTC 17〜23時の :30（12Z初期値の時間帯）。`deploy-web`: `web/**` の push で画面だけ反映。
- launchd: `com.masahiro.tenkizu_web_sync`（30分おき）、`com.masahiro.tenkizu_web_gsm`（1時間おきに確認・00/12Z）、`com.masahiro.tenkizu_web_disk`（6時間おき・ディスク制限と通知。2026-11-11 登録済み。ログ `logs/disk_guard.log`）。止める: `launchctl bootout gui/$(id -u)/<Label>`。
