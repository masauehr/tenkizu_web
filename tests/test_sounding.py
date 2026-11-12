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

HTML = (Path(__file__).resolve().parent / "data" / "uwyo_47918_2026111112.html").read_text(encoding="utf-8")
DT = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def test_parse_real_response():
    d = snd.parse_sounding(HTML)
    assert d["name"] == "ISHIGAKIJIMA, JAPAN" and d["lat"] == 24.33 and d["lon"] == 124.16
    assert len(d["p"]) == 104 and d["p"][0] == 1021.0 and d["h"][0] == 7.0 and d["T"][0] == 24.0 and d["Td"][0] == 15.0
    assert np.isfinite(d["T"]).all() and np.isfinite(d["Td"]).sum() == 45          # 上層は露点が空欄（NaN）
    assert d["sped"][0] == 4.6 and d["drct"][0] == 60.0                            # 風速は m/s
    assert d["p"][-1] == 16.3 and d["T"][-1] == -53.1


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
    assert snd.fetch(DT, "47918", s)["p"][0] == 1021.0
    assert s.calls[0] == {"datetime": "2026-11-11 12:00:00", "id": "47918", "src": "UNKNOWN", "type": "TEXT:LIST"}
    assert snd.fetch(DT, "47936", FakeSession((404, "Unable to retrieve the data for 47936 at 2026-11-11 12:00:00."))) is None   # 欠測・未収録
    assert snd.fetch(DT, "47936", FakeSession((400, "Unable to retrieve the data for 47936"))) is None
    assert snd.fetch(DT, "47918", FakeSession((503, "busy"), (200, HTML)))["p"][0] == 1021.0                                   # 一時的な異常は取り直す
    with pytest.raises(snd.SoundingError):
        snd.fetch(DT, "47918", FakeSession((503, "x"), (503, "x"), (503, "x")))
    with pytest.raises(snd.SoundingError):
        snd.fetch(DT, "47918", FakeSession(snd.requests.ConnectionError("x"), snd.requests.ConnectionError("x"), snd.requests.ConnectionError("x")))


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
    assert {s["id"] for s in m["stations"]} == set(snd.STATIONS) and "Wyoming" in m["legal"]
    assert snd.write_manifest(tmp_path) is False                                    # 中身が同じなら更新しない（無駄な公開コミットを避ける）
    assert snd.prune(tmp_path, 2) == ["2026111100"] and not (tmp_path / "2026111100").exists()
    assert snd.write_manifest(tmp_path) is True and len(json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["times"]) == 2


def test_update_run_skips_existing_and_tolerates_missing(tmp_path):
    t1, t2 = snd.obs_times(datetime(2026, 11, 12, 6, 20, tzinfo=timezone.utc), count=2)
    done = tmp_path / f"{t1:%Y%m%d%H}"
    done.mkdir(parents=True)
    for m in snd.MODES:
        (done / f"47918_{m}.png").write_bytes(b"x")                                 # 作成済み
    fetched, rendered = [], []

    def fake_fetch(dt, wmo, session=None):
        fetched.append((f"{dt:%m%d%H}", wmo))
        if wmo == "47646":
            return None                                                            # 欠測
        if wmo == "47678":
            raise snd.SoundingError("HTTP 503")
        return {"p": [1]}

    res = update_sounding.run([t1, t2], ["47918", "47646", "47678", "47401"], tmp_path, delay=0, fetch=fake_fetch,
                              render=lambda d, dt, wmo, m, p: rendered.append((wmo, m)) or Path(p).parent.mkdir(parents=True, exist_ok=True) or Path(p).write_bytes(b"x"))
    assert ("111200", "47918") not in fetched                                      # 作成済みは取りにいかない
    assert ("111112", "47918") in fetched
    assert res["skipped"] == 1 and res["made"] == 3                                # 47401（2時刻）と 47918（作成済みでない方の1時刻）
    assert len(res["missing"]) == 2 and len(res["errors"]) == 2
    forced = update_sounding.run([t1], ["47918"], tmp_path, force=True, delay=0, fetch=fake_fetch, render=lambda *a: None)
    assert forced["made"] == 1 and forced["skipped"] == 0                          # --force は作成済みも作り直す


def test_update_run_continues_after_render_error(tmp_path):
    t = snd.obs_times(datetime(2026, 11, 12, 6, 20, tzinfo=timezone.utc), count=1)
    def bad_render(d, dt, wmo, m, p):
        raise RuntimeError("boom")
    res = update_sounding.run(t, ["47918", "47646"], tmp_path, delay=0, fetch=lambda *a: {"p": [1]}, render=bad_render)
    assert res["made"] == 0 and len(res["errors"]) == 2                            # 1枚の描画の失敗で全体を止めない


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
    monkeypatch.setattr(snd, "fetch", lambda dt, wmo, session=None: calls.append(wmo) or snd.parse_sounding(HTML))
    out = snd.render_custom("47918", DT, "emagram", tmp_path)
    assert out.name == "2026111112_47918_emagram.png" and out.exists()
    assert snd.render_custom("47918", DT, "emagram", tmp_path) == out and calls == ["47918"]       # 2回目は作り直さない
    monkeypatch.setattr(snd, "fetch", lambda *a, **k: None)
    with pytest.raises(snd.SoundingError, match="観測データがありません"):
        snd.render_custom("47936", DT, "pt", tmp_path)


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


def test_stations_are_japanese_upper_air_sites_with_valid_ids():
    assert len(snd.STATIONS) == 11 and all(len(k) == 5 and k.isdigit() for k in snd.STATIONS)
    assert "47936" not in snd.STATIONS and "46692" not in snd.STATIONS            # 那覇・台北はワイオミング大学に無い（取れない地点を毎回取りにいかない）
