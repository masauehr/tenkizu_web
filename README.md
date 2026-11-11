# tenkizu_web

詳しくは [tenkizu-web.md](tenkizu-web.md) を参照。

tenkizu のCLI作図（天気図・断面図・エマグラム）をWebアプリで見られるようにするプロジェクト（公開版は ECMWF・GFS、GSMはローカル専用）。
設計は [DESIGN.md](DESIGN.md)。**新規図種（Qベクトル・不安定域・FAX57/78・地上FAX・北半球極座標・固定鉛直断面）の作図まで実装済み**。ローカル確認は `/opt/anaconda3/envs/met_env_310/bin/python make_charts.py ecmwf --latest --max-ft 24` → `server.py`（画面の整理は未了）。
