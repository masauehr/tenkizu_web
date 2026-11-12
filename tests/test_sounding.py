# 実況エマグラム（ワイオミング大学の高層ゾンデ）のテスト。ネットワークは使わない（実際の応答を tests/data に保存してある）
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import publish  # noqa: E402
import sounding as snd  # noqa: E402
import update_sounding  # noqa: E402

HTML = (Path(__file__).resolve().parent / "data" / "uwyo_synthetic.html").read_text(encoding="utf-8")        # ワイオミング大学の応答と同じ形式の合成データ（実在の観測ではない。ワイオミングのデータは公開しない方針）
JMA_HTML = (Path(__file__).resolve().parent / "data" / "jma_47918_2026111121.html").read_text(encoding="utf-8")    # 気象庁の実際の応答（石垣島 2026-11-11 21時JST＝12UTC）
DT = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def test_parse_wyoming_format():
    d = snd.parse_sounding(HTML)
    assert d["name"] == "TESTVILLE, JAPAN (SYNTHETIC)" and d["lat"] == 30.0 and d["lon"] == 130.0
    assert len(d["p"]) == 26 and d["p"][0] == 1013.0 and d["h"][0] == 10.0 and d["T"][0] == 24.9 and d["Td"][0] == 17.9
    assert np.isfinite(d["T"]).sum() == 24 and np.isfinite(d["Td"]).sum() == 18    # 上層は露点が空欄（NaN）・風だけの行が2本（気温も NaN）
    assert d["sped"][0] == 3.0 and d["drct"][0] == 80.0                            # 風速は m/s
    assert d["p"][-1] == 50.0 and d["T"][-1] == -54.8


@pytest.mark.parametrize("html", ["<html>no table</html>", "<pre>   PRES   HGHT\n 1000.0     50   20.0</pre>"])
def test_parse_rejects_unexpected_format(html):
    with pytest.raises(snd.SoundingError):
        snd.parse_sounding(html)


def test_parse_rejects_when_surface_is_missing():
    rows = "\n".join(f"{p:7.1f}{1000 + i:7.0f}{-20.0:7.1f}" for i, p in enumerate(np.linspace(500, 100, 12)))
    with pytest.raises(snd.SoundingError):
        snd.parse_sounding("<pre>" + rows + "</pre>")                               # 地上付近（800hPa以下）が無い


def test_obs_times():
    now = datetime(2026, 11, 12, 6, 20, tzinfo=timezone.utc)
    ts = snd.obs_times(now, count=4)
    assert [f"{t:%m%d%H}" for t in ts] == ["111200", "111112", "111100", "111012"]
    assert all(t.hour in (0, 12) and t.tzinfo is not None for t in ts)
    # 観測から2時間経っていない時刻は含めない（00Z の観測は 01:30Z 時点ではまだ公開前のことが多い）
    assert snd.obs_times(datetime(2026, 11, 12, 1, 30, tzinfo=timezone.utc), count=1)[0].hour == 12
    assert snd.obs_times(datetime(2026, 11, 12, 2, 0, tzinfo=timezone.utc), count=1)[0].hour == 0


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return NS(status_code=r[0], text=r[1])


def test_fetch_paths(monkeypatch):
    monkeypatch.setattr(snd.time, "sleep", lambda s: None)
    s = FakeSession((200, HTML))
    assert snd.fetch(DT, "47918", s, source="wyoming")["p"][0] == 1013.0
    assert s.calls[0] == {"datetime": "2026-11-11 12:00:00", "id": "47918", "src": "UNKNOWN", "type": "TEXT:LIST"}
    W = {"source": "wyoming"}
    assert snd.fetch(DT, "47936", FakeSession((404, "Unable to retrieve the data for 47936 at 2026-11-11 12:00:00.")), **W) is None   # 欠測・未収録
    assert snd.fetch(DT, "47936", FakeSession((400, "Unable to retrieve the data for 47936")), **W) is None
    assert snd.fetch(DT, "47918", FakeSession((503, "busy"), (200, HTML)), **W)["p"][0] == 1013.0                                   # 一時的な異常は取り直す
    with pytest.raises(snd.SoundingError):
        snd.fetch(DT, "47918", FakeSession((503, "x"), (503, "x"), (503, "x")), **W)
    with pytest.raises(snd.SoundingError):
        snd.fetch(DT, "47918", FakeSession(snd.requests.ConnectionError("x"), snd.requests.ConnectionError("x"), snd.requests.ConnectionError("x")), **W)
    with pytest.raises(ValueError):
        snd.fetch(DT, "47918", FakeSession(), source="noaa")


@pytest.mark.parametrize("mode", ["emagram", "pt"])
def test_render_modes(tmp_path, mode):
    out = snd.render(snd.parse_sounding(HTML), DT, "47918", mode, tmp_path / f"{mode}.png")
    assert out.exists() and out.stat().st_size > 15_000 and not list(tmp_path.glob("*.tmp.png"))


def test_render_survives_duplicate_pressures_and_missing_dewpoint(tmp_path):
    d = snd.parse_sounding(HTML)
    d["p"] = d["p"].copy()
    d["p"][3] = d["p"][2]                                                           # 同じ気圧が連続する（描画で止まらないこと）
    d["Td"] = np.full_like(d["Td"], np.nan)                                         # 露点がまったく無い
    for mode in snd.MODES:
        assert snd.render(d, DT, "47918", mode, tmp_path / f"{mode}.png").exists()


def test_render_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        snd.render(snd.parse_sounding(HTML), DT, "47918", "hodograph", tmp_path / "x.png")


def test_manifest_and_prune(tmp_path):
    for t in ("2026111100", "2026111112", "2026111200"):
        for st, modes in (("47918", snd.MODES), ("47646", ("emagram",))):
            for m in modes:
                f = tmp_path / t / f"{st}_{m}.png"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"png")
    assert snd.write_manifest(tmp_path) is True
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [x["time"] for x in m["times"]] == ["2026111200", "2026111112", "2026111100"]                   # 新しい順
    assert m["times"][0]["stations"] == {"47646": ["emagram"], "47918": ["emagram", "pt"]}
    assert {s["id"] for s in m["stations"]} == set(snd.STATIONS) and m["source"] == "jma"
    assert "気象庁" in m["legal"] and "加工" in m["legal"]                          # 出典と、加工した旨（公共データ利用規約）
    assert snd.write_manifest(tmp_path) is False                                    # 中身が同じなら更新しない（無駄な公開コミットを避ける）
    assert snd.prune(tmp_path, 2) == ["2026111100"] and not (tmp_path / "2026111100").exists()
    assert snd.write_manifest(tmp_path) is True and len(json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["times"]) == 2


def _touch_render(d, dt, wmo, m, p):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_bytes(b"x")


def test_update_run_skips_existing_and_tolerates_missing(tmp_path):
    t1, t2 = snd.obs_times(datetime(2026, 11, 12, 6, 20, tzinfo=timezone.utc), count=2)
    done = tmp_path / f"{t1:%Y%m%d%H}"
    done.mkdir(parents=True)
    for m in snd.MODES:
        (done / f"47918_{m}.png").write_bytes(b"x")                                 # 作成済み
    fetched = []

    def fake_fetch(dt, wmo, session=None):
        fetched.append((f"{dt:%m%d%H}", wmo))
        if wmo == "47646":
            return None                                                            # 欠測
        if wmo == "47678":
            raise snd.SoundingError("HTTP 503")
        return {"p": [1]}

    res = update_sounding.run([t1, t2], ["47918", "47401", "47646", "47678"], tmp_path, delay=0, fetch=fake_fetch, render=_touch_render)
    assert ("111200", "47918") not in fetched                                      # 作成済みは取りにいかない
    assert ("111112", "47918") in fetched
    assert res["skipped"] == 1 and res["made"] == 3 and res["not_yet"] == []        # t1: 47401。t2: 47918・47401（47646 は欠測・47678 はエラー）
    assert len(res["missing"]) == 2 and len(res["errors"]) == 2
    forced = update_sounding.run([t1], ["47918"], tmp_path, force=True, delay=0, fetch=fake_fetch, render=lambda *a: None)
    assert forced["made"] == 1 and forced["skipped"] == 0                          # --force は作成済みも作り直す


def test_update_run_skips_unpublished_time_with_two_probes(tmp_path):
    """まだ公開されていない時刻は、先頭の2地点だけ試して見分け、残りの地点は取りにいかない（無駄な要求を避ける）。"""
    ts = snd.obs_times(datetime(2026, 11, 12, 6, 20, tzinfo=timezone.utc), count=2)    # 11/12 00Z（未公開）と 11/11 12Z（公開済み）
    calls = []

    def fake_fetch(dt, wmo, session=None):
        calls.append((f"{dt:%m%d%H}", wmo))
        return None if dt.day == 12 else {"p": [1]}

    res = update_sounding.run(ts, list(snd.STATIONS), tmp_path, delay=0, fetch=fake_fetch, render=_touch_render)
    unpublished = [c for c in calls if c[0] == "111200"]
    assert len(unpublished) == 2 and res["not_yet"] == ["2026-11-12 00Z"]          # 未公開の時刻は2回だけ（16地点を取りにいかない）
    assert res["made"] == len(snd.STATIONS) and len([c for c in calls if c[0] == "111112"]) == len(snd.STATIONS)   # 公開済みの時刻は全地点（先頭の1地点は再利用）
    assert res["missing"] == [] and res["errors"] == []


def test_update_run_continues_when_only_the_first_probe_is_missing(tmp_path):
    """先頭の地点だけ欠測でも、その時刻を「公開前」とは決めつけない（2地点目があれば全地点を処理）。"""
    t = snd.obs_times(datetime(2026, 11, 12, 6, 20, tzinfo=timezone.utc), count=1)
    first = list(snd.STATIONS)[0]
    res = update_sounding.run(t, list(snd.STATIONS), tmp_path, delay=0, fetch=lambda dt, w, s=None: None if w == first else {"p": [1]}, render=_touch_render)
    assert res["not_yet"] == [] and res["made"] == len(snd.STATIONS) - 1 and len(res["missing"]) == 1


def test_update_run_continues_after_render_error(tmp_path):
    t = snd.obs_times(datetime(2026, 11, 12, 6, 20, tzinfo=timezone.utc), count=1)
    def bad_render(d, dt, wmo, m, p):
        raise RuntimeError("boom")
    res = update_sounding.run(t, ["47918", "47646"], tmp_path, delay=0, fetch=lambda *a: {"p": [1]}, render=bad_render)
    assert res["made"] == 0 and len(res["errors"]) == 2                            # 1枚の描画の失敗で全体を止めない


def test_validate_request_by_source():
    now = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="気象庁"):
        snd.validate_request("12345", "2026111112", "pt", now)                      # 気象庁は観測所の一覧にある地点だけ
    assert snd.validate_request("12345", "2026111112", "pt", now, source="wyoming")[0] == "12345"       # ワイオミングは国外も（5桁なら）
    with pytest.raises(ValueError):
        snd.validate_request("47918", "2026111112", "pt", now, source="noaa")


def test_validate_request():
    now = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
    wmo, dt, mode = snd.validate_request("47918", "2026111112", "pt", now)
    assert (wmo, dt, mode) == ("47918", DT, "pt")
    for args in (("4791", "2026111112", "pt"), ("abcde", "2026111112", "pt"), ("47918", "20261111", "pt"), ("47918", "2026133112", "pt"),
                 ("47918", "2026111103", "pt"), ("47918", "2026111300", "pt"), ("47918", "2026111112", "x"), (None, "2026111112", "pt")):
        with pytest.raises(ValueError):
            snd.validate_request(*args, now)


def test_render_custom_uses_cache_and_reports_missing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(snd, "fetch", lambda dt, wmo, session=None, source="jma": calls.append((wmo, source)) or snd.parse_jma(JMA_HTML))
    out = snd.render_custom("47918", DT, "emagram", tmp_path)
    assert out.name == "2026111112_47918_emagram.png" and out.exists()
    assert snd.render_custom("47918", DT, "emagram", tmp_path) == out and calls == [("47918", "jma")]      # 2回目は作り直さない
    monkeypatch.setattr(snd, "fetch", lambda dt, wmo, session=None, source="jma": snd.parse_sounding(HTML))
    uw = snd.render_custom("47918", DT, "emagram", tmp_path, source="wyoming")
    assert uw.name == "2026111112_47918_emagram_uwyo.png" and uw != out            # ワイオミング大学の図は別の名前（混ざらない）
    monkeypatch.setattr(snd, "fetch", lambda *a, **k: None)
    with pytest.raises(snd.SoundingError, match="観測データがありません.*半日〜1日"):
        snd.render_custom("47646", DT, "pt", tmp_path)
    with pytest.raises(snd.SoundingError, match="観測データがありません.*ワイオミング"):
        snd.render_custom("47936", DT, "pt", tmp_path, source="wyoming")


def test_publish_sync_sounding_touches_only_output_sounding(tmp_path, monkeypatch):
    src = tmp_path / "local" / "output" / "sounding"
    (src / "2026111112").mkdir(parents=True)
    (src / "2026111112" / "47918_emagram.png").write_bytes(b"png")
    (src / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(publish, "ROOT", tmp_path / "local")
    wt = tmp_path / "wt"
    (wt / "output" / "ecmwf" / "2026111118").mkdir(parents=True)
    keep = wt / "output" / "ecmwf" / "2026111118" / "x.png"
    keep.write_bytes(b"model")
    old = wt / "output" / "sounding" / "2026110100"
    old.mkdir(parents=True)
    (old / "47918_emagram.png").write_bytes(b"old")
    (wt / "index.html").write_text("web", encoding="utf-8")
    assert publish.sync_sounding(wt) == ["2026111112"]
    assert (wt / "output" / "sounding" / "2026111112" / "47918_emagram.png").exists() and (wt / "output" / "sounding" / "manifest.json").exists()
    assert not old.exists()                                                         # 公開側の古い時刻は消える
    assert keep.read_bytes() == b"model" and (wt / "index.html").read_text(encoding="utf-8") == "web"   # モデルの画像・画面ファイルには触れない
    monkeypatch.setattr(publish, "ROOT", tmp_path / "nowhere")
    with pytest.raises(RuntimeError):
        publish.sync_sounding(tmp_path / "wt2")


def test_should_sync_web_rules():
    """画面ファイルは --web-only（と、公開ブランチが無い初回）だけ。モデルの公開・実況の公開は画面に触れない。"""
    assert publish.should_sync_web(NS(web_only=True, model=None, sounding=False), "abc")
    assert publish.should_sync_web(NS(web_only=False, model=None, sounding=False), "abc")
    assert not publish.should_sync_web(NS(web_only=False, model="gfs", sounding=False), "abc")
    assert not publish.should_sync_web(NS(web_only=False, model=None, sounding=True), "abc")
    assert publish.should_sync_web(NS(web_only=False, model="gfs", sounding=False), None)
    assert publish.should_sync_web(NS(web_only=False, model=None, sounding=True), None)


def test_publish_args(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["publish.py", "--sounding"])
    assert publish.parse_args().sounding is True
    monkeypatch.setattr(sys, "argv", ["publish.py", "--pull", "--sounding"])
    a = publish.parse_args()
    assert a.pull and a.sounding
    monkeypatch.setattr(sys, "argv", ["publish.py", "--pull"])
    with pytest.raises(SystemExit):
        publish.parse_args()                                                       # --pull だけはだめ（モデルか --sounding が要る）


def test_stations_are_jma_upper_air_sites():
    assert len(snd.STATIONS) == 16 and all(len(k) == 5 and k.isdigit() for k in snd.STATIONS)
    assert snd.STATIONS["47418"][0] == "釧路" and snd.STATIONS["47412"][0] == "札幌"       # 釧路は 47418（47417 ではない。確認済み）
    assert "47936" not in snd.STATIONS                                              # 那覇は観測を終了した地点


# ------------------------------------------------------------------ 気象庁（既定の取得元）
def test_parse_jma_real_response():
    d = snd.parse_jma(JMA_HTML)
    assert d["name"] == "石垣島"
    assert d["p"][0] == 1020.9 and d["h"][0] == 8.0 and d["T"][0] == 24.1 and d["sped"][0] == 4.6 and d["drct"][0] == 60.0     # 地上
    lv = list(d["p"])
    assert lv[1:5] == [1000.0, 925.0, 900.0, 850.0] and d["T"][lv.index(500.0)] == -5.2 and d["h"][lv.index(500.0)] == 5909.0   # 指定気圧面
    assert d["sped"][lv.index(300.0)] == 21.0 and d["drct"][lv.index(300.0)] == 287.0
    # 相対湿度から露点を計算（地上: 24.1℃・59% → 約15.6℃）。250hPa より上は湿度が無い（///）ので NaN
    assert d["Td"][0] == pytest.approx(15.6, abs=0.2) and np.isnan(d["Td"][lv.index(250.0)]) and np.isfinite(d["Td"][lv.index(300.0)])
    assert np.isnan(d["T"][lv.index(5.0)])                                          # 5hPa は全部 ///


def test_parse_jma_rejects_unexpected_format():
    for html in ("<html>no table</html>", "<table><tr><td>気圧</td><td>高度</td></tr></table>"):
        with pytest.raises(snd.SoundingError):
            snd.parse_jma(html)


def test_fetch_jma_paths_and_japan_time(monkeypatch):
    monkeypatch.setattr(snd.time, "sleep", lambda s: None)
    s = FakeSession((200, JMA_HTML))
    assert snd.fetch(DT, "47918", s)["name"] == "石垣島"                              # 既定は気象庁
    # 気象庁の「時」は日本時間: 12UTC＝21時、00UTC＝9時、18UTC＝翌日3時
    assert s.calls[0] == {"year": 2026, "month": 11, "day": 11, "hour": 21, "atm": "", "point": "47918", "view": ""}
    s0 = FakeSession((200, "表示できるデータがありません"))
    assert snd.fetch(datetime(2026, 11, 12, 0, tzinfo=timezone.utc), "47918", s0) is None
    assert s0.calls[0]["day"] == 12 and s0.calls[0]["hour"] == 9
    s18 = FakeSession((200, "表示できるデータがありません"))
    snd.fetch(datetime(2026, 11, 11, 18, tzinfo=timezone.utc), "47918", s18)
    assert s18.calls[0]["day"] == 12 and s18.calls[0]["hour"] == 3
    assert snd.fetch(DT, "47918", FakeSession((503, "busy"), (200, JMA_HTML)))["name"] == "石垣島"       # 一時的な異常は取り直す
    with pytest.raises(snd.SoundingError, match="地点番号"):
        snd.fetch(DT, "47417", FakeSession((200, "指定気圧面の観測データ ページを表示することが出来ませんでした。")))       # 地点番号が違うとき（公開前とは区別する）
    with pytest.raises(snd.SoundingError):
        snd.fetch(DT, "47918", FakeSession((503, "x"), (503, "x"), (503, "x")))


@pytest.mark.parametrize("mode", ["emagram", "pt"])
def test_render_jma_shows_citation_and_processing(tmp_path, mode):
    out = snd.render(snd.parse_jma(JMA_HTML), DT, "47918", mode, tmp_path / f"{mode}.png", source="jma")
    assert out.exists() and out.stat().st_size > 15_000
    assert "加工" in snd.FOOTER["jma"] and "気象庁" in snd.FOOTER["jma"] and "Wyoming" in snd.FOOTER["wyoming"]


def test_jma_table_with_all_missing_values_is_a_missing_observation_not_an_error():
    """表はあるのに観測値がすべて ///（八丈島・南大東島で実際にあった）は、取得の失敗ではなく欠測（None）。"""
    rows = "".join(f"<tr><td>{p}</td><td>0</td><td>///</td><td>///</td><td>///</td><td>///</td></tr>" for p in (1000, 925, 900, 850, 800, 700, 600, 500, 400, 300, 250, 200))
    html = "<table><tr><td>地上</td></tr><tr><td>///</td><td>0</td><td>///</td><td>///</td><td>///</td><td>///</td></tr>" + rows + "</table>"
    with pytest.raises(snd.SoundingMissing):
        snd.parse_jma(html)
    assert snd.fetch(DT, "47678", FakeSession((200, html))) is None
    assert issubclass(snd.SoundingMissing, snd.SoundingError)
