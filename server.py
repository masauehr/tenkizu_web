#!/usr/bin/env python
# coding: utf-8
"""tenkizu_web 用の簡易サーバー（ローカル用。公開は build_site.py で作る静的サイトを使う）（標準ライブラリのみ）。

  /          → web/ の静的ファイル
  /output/   → output/（make_charts.py が作成したPNGとmanifest.json）
  POST /api/update  → 最新データを取得して作図（update_latest.py を裏で実行。ローカルからのみ）
  GET  /api/status  → 更新ジョブの状態とログ末尾
  GET  /api/custom/list → 自由選択で作った画像の「最近の作図」（直近10件・新しい順。画像ファイルから作る）
  POST /api/custom/publish・unpublish → 自由選択で作った画像を公開サイトに載せる／外す（publish.py --custom。ローカルのみ）
  GET  /api/disk    → ローカルのディスク使用量と上限（disk_guard.py。ローカルからのみ）
  GET  /api/custom  → 任意の断面線・地点で断面図／エマグラムを作る（custom.py。ローカルからのみ）
                      ?kind=xsection&model=gfs&init=YYYYMMDDHH&ft=12&lat1=45&lon1=130&lat2=25&lon2=130
                      ?kind=emagram&model=gfs&init=YYYYMMDDHH&ft=12&lat=26.2&lon=127.7

使用例:
  python server.py                 # http://127.0.0.1:8791/（nwp_chart_web のローカルサーバーは 8790 で、同時に動かせる）
  python server.py --port 9000
外部公開はしない設計（127.0.0.1 固定）。LAN内の別端末から見る場合のみ --host 0.0.0.0 を明示する。
"""
import argparse
import json
import re
import subprocess
import sys
import threading
from urllib.parse import parse_qs
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import custom  # noqa: E402
import disk_guard  # noqa: E402  任意の断面線・地点の作図（ローカル専用）
LOG_FILE = ROOT / "logs" / "update_ui.log"
_publish_lock = threading.Lock()   # 公開（git push）は同時に1つだけ
_custom_lock = threading.Lock()   # 作図は重いので同時に1つだけ（取得が入ると1分ほどかかる）

# 画面の「最新を取得」ボタンから起動する更新ジョブの状態（サーバープロセス内で1つだけ）
_job = {"proc": None, "started": None, "rc": None}
_job_lock = threading.Lock()


def start_update(script="update_latest.py", args=("--jobs", "3")):
    """update_latest.py（または sync_public.py）を裏で起動する。実行中なら起動せず False。"""
    with _job_lock:
        p = _job["proc"]
        if p is not None and p.poll() is None:
            return False
        LOG_FILE.parent.mkdir(exist_ok=True)
        f = open(LOG_FILE, "w", encoding="utf-8")  # 毎回上書き（最新の1回分だけ見せる）
        _job["proc"] = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / script), *args],
            cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
        _job["started"] = __import__("time").time()
        _job["rc"] = None
        return True


def job_status():
    with _job_lock:
        p = _job["proc"]
        running = p is not None and p.poll() is None
        if p is not None and not running:
            _job["rc"] = p.returncode
        tail = []
        if LOG_FILE.exists():
            tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        return {"running": running, "started": _job["started"], "rc": _job["rc"], "log": tail}


class Handler(SimpleHTTPRequestHandler):
    """/output/ を output/ ディレクトリへ振り分け、manifest.json はキャッシュさせない。"""

    def translate_path(self, path):
        p = path.split("?", 1)[0].split("#", 1)[0]
        if p.startswith("/output/"):
            rel = Path(p[len("/output/"):])
            target = (ROOT / "output" / rel).resolve()
            # ディレクトリトラバーサル対策: output/ の外へ出さない
            if (ROOT / "output").resolve() not in target.parents and target != (ROOT / "output").resolve():
                return str(ROOT / "output" / "__forbidden__")
            return str(target)
        return super().translate_path(path)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local_only(self):
        """更新APIは自分のPC（ループバック）からだけ許可。LAN公開時に他端末から叩かれないようにする。"""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self._json(403, {"error": "ローカルからのみ実行できます"})
            return False
        return True

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            if self._local_only():
                self._json(200, job_status())
            return
        if path == "/api/custom":
            self._custom()
            return
        if path == "/api/custom/list":
            if self._local_only():
                self._json(200, {"items": custom.list_recent()})
            return
        if path == "/api/disk":
            if self._local_only():
                rep = disk_guard.report()
                self._json(200, {"free": rep["free"], "free_level": rep["free_level"],
                                 "items": [{"key": k, "label": v["label"], "size": v["size"], "limit": v["limit"],
                                            "level": v["level"], "prune": v["prune"]} for k, v in rep["items"].items()]})
            return
        super().do_GET()

    def _custom(self):
        """/api/custom?kind=xsection|emagram&model=..&init=YYYYMMDDHH&ft=..&lat1=..&lon1=..&lat2=..&lon2=..（emagram は lat,lon）"""
        if not self._local_only():
            return
        q = {k: v[0] for k, v in parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "").items()}
        try:
            p = custom.validate(q.get("kind"), q.get("model"), q.get("init"), q.get("ft"), q)
            with _custom_lock:
                out = custom.render(p)
        except custom.CustomError as e:
            self._json(400, {"error": str(e)})
            return
        except Exception as e:   # 作図の想定外エラーは内容を返す（ローカル専用なので詳細を出してよい）
            self._json(500, {"error": f"作図に失敗しました: {e!r}"})
            return
        self._json(200, {"url": "output/custom/" + out.name})

    def _body(self):
        """POSTの本文（JSON）。なければ空dict"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if 0 < n < 10000 else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/update", "/api/custom/publish", "/api/custom/unpublish"):
            self._json(404, {"error": "not found"})
            return
        if not self._local_only():
            return
        # 他サイトのページからの送信（CSRF）対策: Origin があれば同一ホストのものだけ許可
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != self.headers.get("Host"):
            self._json(403, {"error": "不正なOriginです"})
            return
        if path.startswith("/api/custom/"):
            self._custom_publish(path.endswith("/unpublish"))
            return
        started = start_update()
        self._json(200, {"started": started, **job_status()})

    def _custom_publish(self, remove):
        """自由選択で作った画像を、公開サイト（gh-pages）に載せる／外す。publish.py --custom / --custom-remove を実行する。
        ローカルからのみ・同一Originのみ（do_POST で確認済み）。公開は画面で2回押して確定した操作だけ。"""
        names = self._body().get("names")
        if (not isinstance(names, list) or not names or len(names) > 10
                or not all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_]+\.png", n) for n in names)):
            self._json(400, {"error": "画像の指定が不正です"})
            return
        try:
            with _publish_lock:
                r = subprocess.run([sys.executable, str(ROOT / "publish.py"), "--custom-remove" if remove else "--custom", *names],
                                   cwd=ROOT, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            self._json(500, {"error": "公開の処理が時間内に終わりませんでした"})
            return
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        if r.returncode != 0:
            self._json(500, {"error": "公開サイトに反映できませんでした: " + " / ".join(tail)})
            return
        self._json(200, {"ok": True, "log": tail, "items": custom.list_recent()})

    def end_headers(self):
        p = self.path.split("?", 1)[0]
        if p.startswith("/api/") or p.endswith((".json", ".html", ".js", ".css", "/")):   # APIの状態は常に最新を返す
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "max-age=600")  # PNGは初期値ごとに別ファイル名のため短期キャッシュ可
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # アクセスログは出さない


def main():
    p = argparse.ArgumentParser(description="数値モデル天気図ビューアのサーバー")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8791)
    if any(a in sys.argv[1:] for a in ("?", "-?", "--?")):
        p.print_help()
        return
    a = p.parse_args()
    handler = partial(Handler, directory=str(ROOT / "web"))
    srv = ThreadingHTTPServer((a.host, a.port), handler)
    print(f"http://{a.host}:{a.port}/ で公開中（Ctrl+C で停止）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
