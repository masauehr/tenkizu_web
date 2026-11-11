# tenkizu_web 設計メモ（2026-11-11 開始・Phase1・Phase2（固定断面）まで実装）

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
