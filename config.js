// 公開サイトから断面図・エマグラムを作るための設定（空のままなら、公開サイトは閲覧専用。ローカルでは使わない）。
// 断面図API（nwp_xsection_api を Render に置いたもの。nwp_chart_web と同じ）のURLを入れる。
// 秘密ではない。作図は認証なし（1時間に8回まで）。このサイトからは作図だけで、公開サイトに載せる操作はできない（ローカルで行う）。
window.NWP_XSECTION_API = "https://nwp-xsection-api.onrender.com";
