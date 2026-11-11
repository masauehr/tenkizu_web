# tenkizu_web

詳しくは [tenkizu-web.md](tenkizu-web.md)（マニュアル）を参照。

tenkizu の CLI 作図（天気図・断面図・エマグラム）を、ブラウザで見られるようにしたもの。nwp_chart_web に無い図（北半球極座標・FAX57/78・Qベクトル・不安定域・地上FAX・鉛直断面・エマグラム）だけを載せる。

- **公開サイト**: https://masauehr.github.io/tenkizu_web/ （ECMWF・GFS。12Z初期値の時間帯に自動更新）
- **姉妹サイト**: [数値モデル天気図](https://masauehr.github.io/nwp_chart_web/)
- GSM（気象庁）はローカル専用。公開しない。

## ローカルで動かす

    cd ~/projects/tenkizu_web
    /opt/anaconda3/envs/met_env_310/bin/python update_latest.py   # 最新の初期値を取得して作図
    /opt/anaconda3/envs/met_env_310/bin/python server.py          # http://127.0.0.1:8790/

`python3` ではなく conda の `met_env_310` の python を絶対パスで指定する（`python3` は別の環境になることがある）。
任意の断面線・地点の作図は、画面の「自由選択」（ローカルのみ）。

## 文書
- [tenkizu-web.md](tenkizu-web.md): マニュアル（図の読み方・画面の使い方・更新・困ったとき・改修履歴）
- [DESIGN.md](DESIGN.md): 設計判断と経緯
- [PUBLISH.md](PUBLISH.md): 公開と運用の手順（GitHub Pages・Actions・launchd）
