// 断面図・エマグラムの自由選択（ローカルサーバーの /api/custom を呼ぶ）（Vanilla JS）
(() => {
  const $ = (id) => document.getElementById(id);
  const OUT = "output/";
  // 入力欄の候補（公開の固定断面・固定地点と同じ。おおよその位置）
  const PRESET_X = [["南北 130E", 45, 130, 25, 130], ["南北 140E", 45, 140, 25, 140],
                    ["東西 35N", 35, 120, 35, 150], ["東西 30N", 30, 120, 30, 150], ["東シナ海→日本海", 30, 125, 40, 135]];
  const PRESET_E = [["稚内", 45.42, 141.68], ["館野", 36.06, 140.13], ["潮岬", 33.45, 135.76],
                    ["福岡", 33.58, 130.38], ["那覇", 26.21, 127.69], ["石垣島", 24.34, 124.16], ["札幌", 43.06, 141.33]];
  let manifest = null;
  const kind = () => document.querySelector("input[name=kind]:checked").value;

  function buildPresets() {
    const box = $("presets");
    box.innerHTML = "";
    if (kind() === "xsection") {
      for (const [n, a, b, c, d] of PRESET_X) {
        const bt = document.createElement("button"); bt.textContent = n;
        bt.onclick = () => { $("lat1").value = a; $("lon1").value = b; $("lat2").value = c; $("lon2").value = d; drawMap(); };
        box.appendChild(bt);
      }
    } else {
      for (const [n, la, lo] of PRESET_E) {
        const bt = document.createElement("button"); bt.textContent = n;
        bt.onclick = () => { $("lat").value = la; $("lon").value = lo; drawMap(); };
        box.appendChild(bt);
      }
    }
  }
  function applyKind() {
    $("rowX").hidden = kind() !== "xsection";
    $("rowE").hidden = kind() !== "emagram";
    buildPresets(); clickStep = 0; mapHint(); drawMap();
  }
  function buildInit() {
    const sel = $("init");
    sel.innerHTML = "";
    const m = manifest && manifest.models[$("model").value];
    for (const i of ((m && m.inits) || [])) {
      const s = i.init;
      sel.add(new Option(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)} ${s.slice(8)}Z`, s));
    }
  }
  // ---------- 地図（Canvas・外部のタイルは使わない）。クリックで断面の始点・終点／エマグラムの地点を指定する
  const EXTS = { ea: { lon0: 100, lon1: 180, lat0: 0, lat1: 70 }, nh: { lon0: 0, lon1: 360, lat0: -15, lat1: 90 } };
  let coast = null, clickStep = 0;                                    // clickStep: 次にクリックするのが始点(0)か終点(1)か
  const cv = $("map");
  const ext = () => EXTS[$("ext").value];
  const val = (id) => parseFloat($(id).value);
  function sizeCanvas() {
    const e = ext(), w = cv.clientWidth || 800, dpr = window.devicePixelRatio || 1;
    const h = Math.round(w * (e.lat1 - e.lat0) / (e.lon1 - e.lon0) * ($("ext").value === "nh" ? 0.5 : 1));   // 北半球は縦を圧縮して表示
    cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + "px";
    cv.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w, h };
  }
  const toXY = (lon, lat, w, h) => { const e = ext(); return [((((lon - e.lon0) % 360) + 360) % 360) / (e.lon1 - e.lon0) * w, (e.lat1 - lat) / (e.lat1 - e.lat0) * h]; };
  function drawMap() {
    const { w, h } = sizeCanvas(), c = cv.getContext("2d"), e = ext();
    c.clearRect(0, 0, w, h);
    c.strokeStyle = "rgba(0,0,0,.18)"; c.lineWidth = 1; c.fillStyle = "#667"; c.font = "10px sans-serif";
    const gstep = $("ext").value === "nh" ? 30 : 10;
    for (let lo = Math.ceil(e.lon0 / gstep) * gstep; lo <= e.lon1; lo += gstep) { const [x] = toXY(lo, 0, w, h); c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke(); c.fillText(lo + "E", x + 2, h - 3); }
    for (let la = Math.ceil(e.lat0 / 10) * 10; la <= e.lat1; la += 10) { const [, y] = toXY(0, la, w, h); c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); c.fillText(la + "N", 2, y - 2); }
    if (coast) {
      c.strokeStyle = "#333"; c.lineWidth = 0.8;
      for (const l of coast) {
        c.beginPath();
        let px = null;
        for (let i = 0; i < l.length; i += 2) {
          const [x, y] = toXY(l[i], l[i + 1], w, h);
          if (px === null || Math.abs(x - px) > w / 2) c.moveTo(x, y); else c.lineTo(x, y);   // 端をまたぐ線は切る
          px = x;
        }
        c.stroke();
      }
    }
    // 公開サイトの固定断面・固定地点（参考）
    c.strokeStyle = "rgba(26,79,208,.35)"; c.setLineDash([4, 3]);
    for (const [, a, b, cc, d] of PRESET_X.slice(0, 2)) { const p = toXY(b, a, w, h), q = toXY(d, cc, w, h); c.beginPath(); c.moveTo(...p); c.lineTo(...q); c.stroke(); }
    c.setLineDash([]); c.fillStyle = "rgba(26,79,208,.45)";
    for (const [, la, lo] of PRESET_E.slice(0, 5)) { const [x, y] = toXY(lo, la, w, h); c.fillRect(x - 2, y - 2, 4, 4); }
    // 現在の指定
    c.strokeStyle = "#d02020"; c.fillStyle = "#d02020"; c.lineWidth = 2;
    if (kind() === "xsection") {
      const v = ["lat1", "lon1", "lat2", "lon2"].map(val);
      if (v.every(isFinite)) {
        const p = toXY(v[1], v[0], w, h), q = toXY(v[3], v[2], w, h);
        c.beginPath(); c.moveTo(...p); c.lineTo(...q); c.stroke();
        c.beginPath(); c.arc(p[0], p[1], 5, 0, 7); c.fill();                       // 始点は大きい丸
        c.beginPath(); c.arc(q[0], q[1], 3, 0, 7); c.fill();
      }
    } else {
      const la = val("lat"), lo = val("lon");
      if (isFinite(la) && isFinite(lo)) { const [x, y] = toXY(lo, la, w, h); c.beginPath(); c.arc(x, y, 5, 0, 7); c.fill(); }
    }
  }
  function mapHint() {
    $("mapHint").textContent = kind() === "xsection"
      ? "地図を2回クリック＝始点→終点（大きい丸が始点）。青の点線・点は公開サイトの固定断面・固定地点。"
      : "地図をクリックすると、その地点のエマグラムを作れます。";
  }
  cv.addEventListener("click", (ev) => {
    const r = cv.getBoundingClientRect(), e = ext();
    const lon = e.lon0 + (ev.clientX - r.left) / r.width * (e.lon1 - e.lon0);
    const lat = e.lat1 - (ev.clientY - r.top) / r.height * (e.lat1 - e.lat0);
    const f = (x) => (Math.round(x * 10) / 10).toString();
    if (kind() === "xsection") {
      if (clickStep === 0) { $("lat1").value = f(lat); $("lon1").value = f(lon); $("lat2").value = f(lat); $("lon2").value = f(lon); }
      else { $("lat2").value = f(lat); $("lon2").value = f(lon); }
      clickStep = 1 - clickStep;
    } else { $("lat").value = f(lat); $("lon").value = f(lon); }
    drawMap();
  });
  cv.addEventListener("mousemove", (ev) => {
    const r = cv.getBoundingClientRect(), e = ext();
    $("mapInfo").textContent = `${(e.lat1 - (ev.clientY - r.top) / r.height * (e.lat1 - e.lat0)).toFixed(1)}N ${(e.lon0 + (ev.clientX - r.left) / r.width * (e.lon1 - e.lon0)).toFixed(1)}E`;
  });
  $("ext").onchange = drawMap;
  window.addEventListener("resize", drawMap);
  for (const id of ["lat1", "lon1", "lat2", "lon2", "lat", "lon"]) $(id).addEventListener("input", drawMap);
  fetch("coast.json").then((r) => r.json()).then((j) => { coast = j.lines; drawMap(); }).catch(() => drawMap());

  async function boot() {
    try { manifest = await (await fetch(OUT + "manifest.json", { cache: "no-store" })).json(); } catch (e) {
      $("msg").textContent = "manifest.json を読めません。先に作図してください"; $("msg").className = "err"; return;
    }
    for (const [k, v] of Object.entries(manifest.models || {})) $("model").add(new Option(v.label || k, k));
    $("model").onchange = buildInit;
    buildInit();
    for (const r of document.querySelectorAll("input[name=kind]")) r.onchange = applyKind;
    applyKind();
  }

  $("go").onclick = async () => {
    const q = new URLSearchParams({ kind: kind(), model: $("model").value, init: $("init").value, ft: $("ft").value });
    for (const id of kind() === "xsection" ? ["lat1", "lon1", "lat2", "lon2"] : ["lat", "lon"]) q.set(id, $(id).value);
    $("go").disabled = true; $("msg").className = ""; $("msg").textContent = "作図中…（データ取得が入ると1分ほどかかります）";
    try {
      const r = await fetch("api/custom?" + q);
      const j = await r.json();
      if (!r.ok) { $("msg").textContent = j.error || "作図できません"; $("msg").className = "err"; }
      else { $("msg").textContent = "完了"; $("result").innerHTML = `<img alt="" src="${j.url}">`; }
    } catch (e) { $("msg").textContent = "サーバーに接続できません（ローカルサーバーで開いてください）"; $("msg").className = "err"; }
    $("go").disabled = false;
  };

  boot();
})();
