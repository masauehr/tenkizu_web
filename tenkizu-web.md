# tenkizu_web 設計メモ（2026-11-11 開始・Phase1〜3 実装済み・2026-11-11 公開開始）

tenkizu（CLIで天気図・断面図・エマグラムを描画）の成果を、Webアプリで見られるようにする新プロジェクト。
仕組みは [nwp_chart_web](../nwp_chart_web/DESIGN.md) と同様（GitHub Actionsで作図 → 静的PNGをGitHub Pagesで配信）。

## 決定事項（ユーザー確認済み 2026-11-11）

| 項目 | 決定 |
|---|---|
| 公開版のモデル | **ECMWF・GFS**（AIFSは公開しない） |
| ローカル専用モデル | **GSM**（併用）。公開しない（`local_only`方式を踏襲） |
| 着手順 | **天気図（tenkizu系）→ 断面図・エマグラム** |
| 断面図・エマグラム | **ローカルは地点・断面線を自由選択、公開は固定（事前作成PNG）** |
| 部品の共有 | **nwp_chart_web と共通部品化しない**（コピーして調整。自己完結を優先） |
| 図種の範囲 | **新規分のみ**（nwp_chart_webと重複する図は作らない。既存図はリンクで案内） |
| 公開の固定断面線 | **南北断面 130E・140E**（ほか東西断面は Phase2 で検討） |
| 公開の固定地点 | **主要ゾンデ地点**（Phase3 で地点一覧を確定） |
| リポジトリ | **tenkizu_web（GitHub public）**。データ・画像は公開の仕組み確立まで含めない |

## nwp_chart_web との重複整理

nwp_chart_web に**すでにある**図種: 地上（気圧・風・降水／天気・SSI）、500hPa高度・渦度、ジェット100/200/300、各層（300/500/700/850/925/975）気温・湿数・風、850hPa EPT・SSI。

tenkizu にあって **nwp_chart_web に無い**（本プロジェクトの新規価値）:

| 図種 | 元スクリプト | 備考 |
|---|---|---|
| 850hPa Qベクトル発散 | GSM_QVector850hPa.py | |
| 不安定域分布 | GSM_Instability.py | |
| FAX57相当 / FAX78相当 | *_fax57 / *_fax78 / ECM_Fax57,78 | 気象庁FAX天気図風 |
| 地上 FAX（気圧・10m風・2m気温） | GSM_faxSrfPre.py | |
| 北半球極座標図 | GSM_PolarView.py | ジェット蛇行確認用 |
| **鉛直断面図** | GSM_CrossSection.py | ポテンシャル温位・EPT・風 |
| **エマグラム** | GRIB2_Emagram.py | 温位エマグラム含む |

## 残りの要判断（Phase1以降）

- 公開サイトの容量見積もり（Pages 1GB。別リポジトリなので nwp_chart_web とは別計上）
- 東西断面線・ゾンデ地点の具体的な一覧
- ローカル自由選択UI（地図上で地点クリック／断面線の2点指定 → server.py のAPIで作図）

## 想定構成

```
tenkizu_web/
├── charts.py        # 図種定義（CHARTS）。tenkizuスクリプトから描画部を移植
├── xsection.py      # 断面図
├── emagram.py       # エマグラム
├── data.py          # ECMWF/GFS 部分取得（nwp_chart_webのコピー調整）、GSMはローカル
├── update_latest.py / publish.py / build_site.py   # nwp_chart_web 同様
├── server.py        # ローカル用（自由選択API・local_only）
└── web/             # Vanilla JS / HTML / CSS
```

## フェーズ案

- Phase0: 設計確定（上の要判断を決める）
- Phase1: 新規図種（Qベクトル・不安定域・FAX）をECMWF/GFSで作図、ローカル表示
- Phase2: 断面図（固定断面線＋ローカル自由選択）
- Phase3: エマグラム（固定地点＋ローカル自由選択）
- Phase4: GitHub Actions＋Pages公開（公開前に確認を取る）

## 進捗

- **Phase1（作図部）完了 2026-11-11**: nwp_chart_web の作図の枠組み（`nwp_data.py` 部分取得・`nwp_charts.py` 描画部品・`make_charts.py`・`server.py`・`web/`）をコピーし、AIFS・JRA-55 を外した。
  新規図種 4 つを実装し、ECMWF・GFS の実データ（2026-11-10 18UTC 初期値 FT12）で作図確認済み。

| 図種 | キー | 内容 | 1枚の容量（実測） |
|---|---|---|---|
| Qベクトル | `qvec850` | 850hPa Q発散（シェード）・Q矢印・気温・高度 | 約130KB |
| 不安定域 | `instab` | SEPT300 − maxEPT(850hPa以下)・300hPa気温 | 約95KB |
| FAX57相当 | `fax57` | 500hPa気温・700hPa湿数 | 約210KB |
| FAX78相当 | `fax78` | 850hPa気温・風・W/C・700hPa発散 | 約155KB |

- **追加実装 2026-11-11**: 地上FAX（`surffax`）・北半球極座標図（`polar300`/`polar500`、専用の `render_polar`、気温W/C・風速シェード）・**鉛直断面図**（`xsection.py`、固定断面 `xs130`/`xs140`＝南北 45N→25N、地域名 `xsec`）。
  データ読み込み範囲を北半球全体（経度0〜360・緯度−15〜90）へ広げた（極座標図のため。`nwp_data.CROP`）。
- 断面図の作り方: MetPy の cross_section を使わず、格子データを経路上へ双一次補間（`compute_section`）。鉛直は log(気圧) 補間で等値線を滑らかに、発散は約1°平滑化、風は経路方向に平滑化。地面（地表気圧）より下は灰色。上端は取得している最上層 200hPa（tenkizu は 100hPa まで）。
- 容量見積もり（旧・4図種のみ）: 4図種×日本域×13FT（6時間毎・72h）≈ 7.5MB/初期値。公開5初期値で約40MB と小さく、アジア域の追加も余裕。
- 平滑化回数は tenkizu（0.5°向け）を格子間隔で換算（`_passes`）。
- 地域は当面 `japan` のみ。
- **未実装（次）**: 画面（`web/` は nwp_chart_web のまま。AIFS・JRA-55・過去分取得の記述が残る）の整理、`update_latest.py`、`publish.py`／CI（Phase4）、北半球極座標図・地上FAX。

## 進捗（2026-11-11 続き）

- **エマグラム（Phase3）**: `emagram.py`。最寄り格子点のプロファイルを log(気圧) で補間し、持ち上げ気塊・LCL・EL・CAPE/CIN・ホドグラフを描く。CAPE=0（安定）のときは塗らない。固定5地点（稚内・館野・潮岬・福岡・那覇＝`emagram.STATIONS`、地域名 `emagram`、図種 `em_<観測所番号>`）。取得している気圧面は 975〜200hPa の7層と少ないため補間で補っている（粗さは残る）。
- **ローカルの自由選択**: `custom.py` と `server.py` の `GET /api/custom`（ローカルのみ・入力検証あり・同じ条件は作り直さない・直列実行）、画面 `web/custom.html`（断面の始点終点・エマグラムの緯度経度・プリセットボタン）。データが無い初期値・予報時間は ECMWF/GFS ならその場で取得（約1分、`data/` に残る）、GSM は手元分のみ。結果は `output/custom/`（公開しない）。
- **画面の整理**: AIFS・JRA-55・過去分取得・公開分の同期ボタンと関連コードを `web/` から除去。更新APIがあるとき（ローカル）だけ「自由選択」リンクを出す。
- **公開用の準備**: `update_latest.py`・`build_site.py`・`publish.py`・`.github/workflows/`（update-model・deploy-web）を移植。**自動実行は無効**（手動実行のみ）。手順は [PUBLISH.md](PUBLISH.md)。
- テスト: `tests/test_charts.py`（6件、`python -m pytest tests`）。
- **残り**: 公開の開始（ユーザー判断）、断面図の地図クリックでの2点指定、取得気圧面の追加（400/250/100hPa 等）の検討、ローカルの定期更新（launchd）。

## 進捗（2026-11-11 公開開始）

- **公開開始**: https://masauehr.github.io/tenkizu_web/ （GitHub Pages・gh-pages ブランチ）。定期実行は **12Z初期値が揃う時間帯**（UTC 17〜23時の :30、1日7回。GFS 12Z は 16:15〜18:00、ECMWF 12Z は 19:40〜23:25 に揃う実測）。00Z・18Z の時間帯は未設定（ECMWF 18Z は 0:30〜3:35 UTC に揃うため、定期実行ではなく手動実行のときだけ載る）。`web/**` の push は画像なしで自動反映（deploy-web）。11/11 は手動実行、自動実行は 11/12 02:30 JST から。
- **自由選択の地図クリック**: `web/custom.html`＋`custom.js`。Canvas で自前描画（`make_coast.py` が作る `web/coast.json`・Natural Earth 50m・181KB。外部の地図タイル・APIキーは使わない）。断面は2回クリック＝始点→終点、エマグラムは1回クリック。範囲は東アジア／北半球。公開の固定断面・地点を参考表示。
- **気圧面の追加**: `nwp_data.PLEVELS` に 1000/600/400/250/150/100hPa を追加（GSM も同様）。1ステップの取得量は ECMWF 約20→36MB、GFS 約38→71MB に増えた（PNG の容量は変わらない）。断面図は上端 100hPa、エマグラムは上端 100hPa（露点・相対湿度は 300hPa 以下だけ描く。それより上は湿度が信頼できないため）。
- **日本語フォント**: CI（ubuntu）には日本語フォントが無く、図の中の日本語が四角になった。`fonts-noto-cjk` をCIで入れ、描画のフォント候補に `Noto Sans CJK JP` を追加。作り直しは Actions の `update-model` を `force=true` で手動実行する。
- **ローカル定期更新（launchd）**: `launchd/com.masahiro.tenkizu_web_sync.plist`（30分おき・公開分を手元へ取り込み `sync_public.py`）と `com.masahiro.tenkizu_web_gsm.plist`（GSM を 00/12Z だけ1時間おきに確認・3日保管）。登録は PUBLISH.md の手順。
