# cron-job.org 設定ガイド — 公開データの定期起動（tenkizu_web）

作成: 2026-11-25（**設定完了**。テスト実行で 204 を確認・Actions に workflow_dispatch の起動を確認済み。本番の定期起動は 2026-11-25 UTC 17:30 から）

## なぜ使うか
`update-model.yml` の `schedule`（UTC 17〜23時の :30）は GitHub に間引かれ、2026-11-24 は1日1回（UTC 3時台）しか動かず、12Z 分の公開が約12時間遅れた。
nwp_chart_web・taiwan_radar_frames と同じく、**cron-job.org から GitHub API で `workflow_dispatch` を呼んで起動**する。`schedule` は予備として残す。
`update-model` は「新しい初期値が揃っていなければ何も作らず終わる」ので、多めに起動しても安全。同じモデルは `concurrency` で同時に1つだけ処理される。
仕組みと用語の詳細は nwp_chart_web の [CRON_JOB_ORG.md](../nwp_chart_web/CRON_JOB_ORG.md) の 0 章を参照。

## 設定内容
| 項目 | 設定 |
|------|------|
| URL | `https://api.github.com/repos/masauehr/tenkizu_web/actions/workflows/update-model.yml/dispatches` |
| 方式・本文 | POST、`{"ref":"main","inputs":{"model":"all"}}` |
| ヘッダー | `Authorization: Bearer <トークン>`、`Accept: application/vnd.github+json`、`X-GitHub-Api-Version: 2022-11-28`、`Content-Type: application/json` |
| 実行時刻（UTC） | 17〜23時の各時の **:30 と :55**（計14回）。GFS 12Z は 16:15〜18:00、ECMWF 12Z は 19:40〜23:25 に揃う目安 |
| **cron-job.org の入力（JST）** | 画面は JST なので UTC に9を足す: **2 / 3 / 4 / 5 / 6 / 7 / 8 時**（UTC の 17〜23 時）。分は 30 と 55。**UTC と JST を取り違えない** |
| 正常な応答 | `204 No Content` |

## 手順
1. **トークン**: 既存の `nwp-chart-web-dispatch`（Fine-grained・Actions: Read and write）の対象リポジトリに `tenkizu_web` を追加する（GitHub → Settings → Developer settings → Fine-grained tokens）。トークンの値は変わらず、期限（2027-02-08 01:45:40 UTC）も同じ。別トークンにしたいときは新規発行（権限は `tenkizu_web` の Actions だけ）。**トークンの値はチャット・ファイル・リポジトリに書かない。**
2. **ジョブ**: cron-job.org で nwp_chart_web 用のジョブを複製し、URL の `nwp_chart_web` を `tenkizu_web` に、時刻を上の表に直す。
3. **テスト実行**: 応答が `204` になり、Actions に `update-model` が `workflow_dispatch` で現れることを確認する。
4. **期限通知**: トークンを共有する場合は nwp_chart_web 側の期限通知（Slack DM）がそのまま効く。別トークンにした場合は期限を `PUBLISH.md` に追記する。

## 確認
- 失敗が続くとジョブが自動で無効化されることがある。ときどき cron-job.org の履歴を見る。
- `gh run list --workflow update-model` で、各時の :30/:55 付近に `workflow_dispatch` の実行が並んでいれば正常。
