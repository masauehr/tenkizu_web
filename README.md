# tenkizu_web

詳しくは [tenkizu-web.md](tenkizu-web.md) を参照。

tenkizu のCLI作図（天気図・断面図・エマグラム）をWebアプリで見られるようにするプロジェクト（公開版は ECMWF・GFS、GSMはローカル専用）。
設計は [DESIGN.md](DESIGN.md)。**Phase1（新規4図種の作図）まで実装済み**。ローカル確認は `/opt/anaconda3/envs/met_env_310/bin/python make_charts.py ecmwf --latest --max-ft 24` → `server.py`（画面の整理は未了）。
