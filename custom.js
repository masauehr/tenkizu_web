// 断面図・エマグラムの自由選択（ローカルサーバーの /api/custom を呼ぶ）（Vanilla JS）
(() => {
  const $ = (id) => document.getElementById(id);
  const OUT = "output/";
  // 入力欄の候補（公開の固定断面・固定地点と同じ。おおよその位置）
  const PRESET_X = [["南北 130E", 45, 130, 25, 130], ["南北 140E", 45, 140, 25, 140],
                    ["東西 35N", 35, 120, 35, 150], ["東西 30N", 30, 120, 30, 150], ["東シナ海→日本海", 30, 125, 40, 135]];
  const PRESET_E = [["稚内", 45.42, 141.68], ["館野", 36.06, 140.13], ["潮岬", 33.45, 135.76],
                    ["福岡", 33.58, 130.38], ["那覇", 26.21, 127.69], ["石垣島", 24.34, 124.16], ["札幌", 43.06, 141.33]];
  // 気象庁の高層気象観測所（sounding.py の STATIONS と同じ。おおよその位置。地図のクリックで最寄りを選ぶ）
  const SND = [["47401", "稚内", 45.42, 141.68], ["47412", "札幌", 43.06, 141.33], ["47418", "釧路", 42.98, 144.38], ["47582", "秋田", 39.72, 140.10],
               ["47600", "輪島", 37.39, 136.90], ["47646", "館野", 36.06, 140.13], ["47678", "八丈島", 33.10, 139.79], ["47971", "父島", 27.09, 142.19],
               ["47991", "南鳥島", 24.29, 153.98], ["47741", "松江", 35.45, 133.07], ["47778", "潮岬", 33.45, 135.76], ["47807", "福岡", 33.58, 130.38],
               ["47827", "鹿児島", 31.56, 130.55], ["47909", "名瀬", 28.38, 129.50], ["47918", "石垣島", 24.34, 124.16], ["47945", "南大東島", 25.83, 131.23]];
  // 公開サイトでも、Render の断面図API（nwp_xsection_api）が config.js に設定されていれば、断面図・エマグラムを作れる（作図のみ。公開サイトに載せる操作は無い）。
  // 作図は認証なし（回数制限あり）。ローカルサーバーで開いたときは従来どおり（/api/custom）。
  let LOCAL = true;
  const REMOTE = String(window.NWP_XSECTION_API || "").replace(/\/+$/, "");
  let REMOTE_MODE = false;
  let manifest = null;
  const kind = () => document.querySelector("input[name=kind]:checked").value;

  function buildPresets() {
    const box = $("presets");
    box.innerHTML = "";
    if (kind() === "sounding") return;                               // 地点は選択欄と地図のクリックで選ぶ
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
    $("rowOpt").hidden = kind() !== "xsection";                       // 表示要素の選択は断面だけ
    $("rowE").hidden = kind() !== "emagram";
    $("rowS").hidden = kind() !== "sounding";
    $("rowModel").hidden = kind() === "sounding";
    $("rowPtop").hidden = kind() === "sounding";                      // 高度設定は数値モデルの図だけ                      // 実況は数値モデルではないので、モデル・初期値・予報時間は使わない
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
    if (kind() === "sounding") {                                       // 観測所（選択中は赤の大きい丸）
      for (const [id, name, la, lo] of SND) {
        const [x, y] = toXY(lo, la, w, h), on = $("sWmo").value === id;
        c.fillStyle = on ? "#d02020" : "#1a4fd0"; c.beginPath(); c.arc(x, y, on ? 6 : 3.5, 0, 7); c.fill();
        c.fillStyle = on ? "#d02020" : "#334"; c.font = "11px sans-serif"; c.fillText(name, x + 6, y - 4);
      }
    } else if (kind() === "xsection") {
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
    $("mapHint").textContent = kind() === "sounding" ? "地図の観測所（青い点）をクリックすると、最寄りの観測所を選べます。"
      : kind() === "xsection"
      ? "地図を2回クリック＝始点→終点（大きい丸が始点）。青の点線・点は公開サイトの固定断面・固定地点。"
      : "地図をクリックすると、その地点のエマグラムを作れます。";
  }
  cv.addEventListener("click", (ev) => {
    const r = cv.getBoundingClientRect(), e = ext();
    const lon = e.lon0 + (ev.clientX - r.left) / r.width * (e.lon1 - e.lon0);
    const lat = e.lat1 - (ev.clientY - r.top) / r.height * (e.lat1 - e.lat0);
    const f = (x) => (Math.round(x * 10) / 10).toString();
    if (kind() === "sounding") {                                       // 最寄り（緯度経度の単純な距離。経度は緯度で縮める）
      const k = Math.cos(lat * Math.PI / 180);
      const best = SND.reduce((a, s) => ((s[2] - lat) ** 2 + ((s[3] - lon) * k) ** 2 < (a[2] - lat) ** 2 + ((a[3] - lon) * k) ** 2 ? s : a));
      $("sWmo").value = best[0];
    } else if (kind() === "xsection") {
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
  $("sWmo").onchange = drawMap;
  window.addEventListener("resize", drawMap);
  for (const id of ["lat1", "lon1", "lat2", "lon2", "lat", "lon"]) $(id).addEventListener("input", drawMap);
  fetch("coast.json").then((r) => r.json()).then((j) => { coast = j.lines; drawMap(); }).catch(() => drawMap());

  const REMOTE_MODELS = ["ecmwf", "gfs", "aifs"];                        // Render の API が作れるモデル
  async function detectLocal() {
    try { const j = await (await fetch("api/custom/list", { cache: "no-store" })).json(); LOCAL = Array.isArray(j.items); } catch (e) { LOCAL = false; }
    if (!LOCAL && REMOTE) { REMOTE_MODE = true; setupRemote(); }
    else if (!LOCAL) {                                                  // API が未設定の公開サイト: 作者が公開した図を見るだけ
      $("form").hidden = true; $("disk").hidden = true;
      $("intro").textContent = "作者が公開した断面図・エマグラムです（公開サイトでは新しく作ることはできません）。下のボタンで選んでください。";
    }
  }

  async function boot() {
    try { manifest = await (await fetch(OUT + "manifest.json", { cache: "no-store" })).json(); } catch (e) {
      $("msg").textContent = "manifest.json を読めません。先に作図してください"; $("msg").className = "err"; return;
    }
    for (const [id, name] of SND) $("sWmo").add(new Option(`${name}（${id}）`, id));
    $("sWmo").value = "47918";
    const d0 = new Date(Date.now() - 24 * 3600 * 1000);                // 既定: 前日（公開まで半日〜1日かかるため）
    $("sDate").value = `${d0.getUTCFullYear()}-${String(d0.getUTCMonth() + 1).padStart(2, "0")}-${String(d0.getUTCDate()).padStart(2, "0")}`;
    await detectLocal();                                               // ローカルか、公開サイト（Render の API で作る）か
    for (const [k, v] of Object.entries(manifest.models || {})) {
      if (REMOTE_MODE && !REMOTE_MODELS.includes(k)) continue;          // Render の API は GSM を扱えない
      $("model").add(new Option(v.label || k, k));
    }
    $("model").onchange = buildInit;
    buildInit();
    for (const r of document.querySelectorAll("input[name=kind]")) r.onchange = applyKind;
    applyKind();
  }

  // ディスク使用量（ローカルサーバーの /api/disk）。上限を超えた項目は赤で出す
  const fmtB = (b) => (b >= 1024 ** 3 ? (b / 1024 ** 3).toFixed(2) + "GB" : Math.round(b / 1024 ** 2) + "MB");
  async function showDisk() {
    try {
      const j = await (await fetch("api/disk", { cache: "no-store" })).json();
      const parts = j.items.filter((i) => i.key === "grib" || i.key === "custom").map((i) =>
        `<span class="${i.level === "over" ? "warn" : ""}">${i.label} ${fmtB(i.size)} / 上限 ${fmtB(i.limit)}</span>`);
      parts.push(`<span class="${j.free_level === "ok" ? "" : "warn"}">ディスクの空き ${fmtB(j.free)}${j.free_level === "min" ? "（少ないため新しい取得を止めています）" : ""}</span>`);
      $("disk").innerHTML = "ディスク: " + parts.join(" ／ ") + "（上限を超えると古いものから自動で削除します）";
    } catch (e) { $("disk").textContent = ""; }
  }
  showDisk();

  // ---------- 作図結果の履歴（直近10件）。サーバーが output/custom/ の画像ファイルから作る一覧を読む
  // （ブラウザの保存には頼らない。別のブラウザでも、サイトデータを消しても同じ。通常版の「別の図を並べる」の右の図でも選べる）
  const MAX_HIST = 10;
  async function getHist() {
    const url = LOCAL ? "api/custom/list" : "output/custom/list.json";
    try { const j = await (await fetch(url, { cache: "no-store" })).json(); return Array.isArray(j.items) ? j.items : []; }
    catch (e) { return []; }
  }
  // 登録した日時（公開した時刻 pub）。無い項目（ローカルで作っただけの図）は作図した時刻を「作図」として出す。ブラウザの時刻帯で表示する
  const pad2 = (n) => String(n).padStart(2, "0");
  const fmtTs = (sec) => { const d = new Date(sec * 1000); return (d.getMonth() + 1) + "/" + d.getDate() + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes()); };
  const stampText = (c) => (c.pub ? "　登録 " + fmtTs(c.pub) + (c.by ? "（" + c.by + "）" : "") : (c.created || c.ts) ? "　作図 " + fmtTs(c.created || c.ts) : "");
  const histText = (c) => `${c.label}（${c.model} ${c.initText}${c.kind === "sounding" ? "" : ` ＋${c.ft}h`}）` + stampText(c) + (c.published ? "【公開中】" : "");
  // 結果の画像と「平面図と並べて見る」のリンクを出す
  function showResult(c) {
    if (REMOTE_MODE) return showRemoteResult(c, true);               // 一覧（公開サイトの list.json）の図はすべて公開済み
    if (!LOCAL) { $("result").innerHTML = `<img alt="${esc(histText(c))}" src="${esc(c.url)}">`; return; }   // API 未設定の公開サイト: 公開された図を見るだけ
    const base = c.url.split("/").pop();
    const link = c.mk ? `./#model=${c.mk}&init=${c.ik}&region=japan&chart=fax57&ft=${c.ft}&fig=1&region2=custom&chart2=${encodeURIComponent(base)}`
                      : `./#region=japan&chart=fax57&fig=1&region2=custom&chart2=${encodeURIComponent(base)}`;
    $("result").innerHTML = `<p><a href="${link}" target="_blank" rel="noopener">平面図（FAX57）と並べて見る（通常版を開く）</a>` +
      `<span class="note">　通常版の「別の図を並べる」で、右の図を選び直せます</span></p>` +
      `<p id="pubRow"><button id="btnPub">${c.published ? "公開から外す" : "公開サイトに載せる"}</button> ` +
      `<span class="note" id="pubMsg">${c.published ? "公開中です（公開サイトの通常版で、誰でも右の図に並べて見られます）"
        : "公開サイトに載せると、誰でも公開版の通常版の右の図に並べて見られます（直近10件まで。古いものから外れます）"}</span></p>` +
      `<img alt="${histText(c)}" src="${c.url}">`;
    wirePub(c);
  }

  // 公開サイトに載せる／外す。誤操作を防ぐため、2回押して確定する（1回目で表示が変わり、5秒以内にもう一度押す）
  function wirePub(c) {
    const b = $("btnPub"), msg = $("pubMsg"), label = b.textContent;
    b.onclick = async () => {
      if (!b.dataset.armed) {
        b.dataset.armed = "1";
        b.textContent = c.published ? "本当に外す（もう一度押す）" : "本当に公開する（もう一度押す）";
        setTimeout(() => { if (b.dataset.armed) { delete b.dataset.armed; b.textContent = label; } }, 5000);
        return;
      }
      b.disabled = true;
      msg.textContent = "公開サイトへ反映しています…（数十秒かかります）";
      try {
        const r = await fetch("api/custom/" + (c.published ? "unpublish" : "publish"), {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ names: [c.url.split("/").pop()] }) });
        const j = await r.json();
        if (!r.ok) { msg.textContent = j.error || "反映できません"; b.disabled = false; delete b.dataset.armed; b.textContent = label; return; }
        await renderHist();
        showResult((j.items || []).find((x) => x.url === c.url) || { ...c, published: !c.published });
        $("pubMsg").textContent = (c.published ? "公開から外しました" : "公開しました（公開サイトへの反映に1〜2分かかります）") + "。";
      } catch (e) { msg.textContent = "反映できません（ローカルサーバーを確認してください）"; b.disabled = false; delete b.dataset.armed; b.textContent = label; }
    };
  }
  async function renderHist() {
    const h = await getHist(), box = $("hist");
    box.innerHTML = "";
    if (!h.length) return;
    const t = document.createElement("div"); t.className = "note";
    t.textContent = LOCAL ? `最近の作図（直近${MAX_HIST}件・保存した画像ファイルから表示。通常版の「別の図を並べる」の右の図でも選べます）` : "公開されている断面図・エマグラム";
    box.appendChild(t);
    for (const c of h) {
      const b = document.createElement("button");
      b.textContent = histText(c);
      b.title = "この結果を表示する";
      b.onclick = () => showResult(c);
      box.appendChild(b);
    }
  }

  $("go").onclick = async () => {
    if (REMOTE_MODE) return goRemote();
    const q = kind() === "sounding"
      ? new URLSearchParams({ kind: "sounding", wmo: $("sWmo").value, time: $("sDate").value.replace(/-/g, "") + $("sHour").value, mode: $("sMode").value })
      : new URLSearchParams({ kind: kind(), model: $("model").value, init: $("init").value, ft: $("ft").value, ptop: $("ptop").value });   // ptop: 高度設定（表示する高度の範囲の上端[hPa]）
    if (kind() !== "sounding") for (const id of kind() === "xsection" ? ["lat1", "lon1", "lat2", "lon2"] : ["lat", "lon"]) q.set(id, $(id).value);
    if (kind() === "emagram" && $("pt").value) q.set("pt", "1");       // 温位エマグラム（ローカルと Render の API の両方）
    if (kind() === "xsection") {                                       // 断面の表示要素（色塗り・等値線・風矢羽）
      q.set("shade", document.querySelector("input[name=shade]:checked").value);
      q.set("contours", [...document.querySelectorAll("input[name=cont]:checked")].map((e) => e.value).join(","));
      q.set("barbs", document.querySelector("input[name=barbs]:checked").value);
    }
    $("go").disabled = true; $("msg").className = ""; $("msg").textContent = kind() === "sounding" ? "取得・作図中…（数秒）" : "作図中…（データ取得が入ると1分ほどかかります）";
    try {
      const r = await fetch("api/custom?" + q);
      const j = await r.json();
      if (!r.ok) { $("msg").textContent = j.error || "作図できません"; $("msg").className = "err"; }
      else {
        $("msg").textContent = "完了";
        await renderHist();                        // サーバーの一覧（画像ファイル）を読み直す。いま作った画像が先頭に来る
        const cur = (await getHist()).find((x) => x.url === j.url);
        if (cur) showResult(cur); else $("result").innerHTML = `<img alt="" src="${j.url}">`;
      }
    } catch (e) { $("msg").textContent = "サーバーに接続できません（ローカルサーバーで開いてください）"; $("msg").className = "err"; }
    $("go").disabled = false;
    showDisk();
  };

  // ---------- 公開サイトから Render の断面図APIで作る（REMOTE_MODE。nwp_chart_web と同じ仕組み。作図のみ）
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const KEY_STORE = "nwpXsectionKey";                  // nwp_chart_web と同じ API・同じ合言葉（同じオリジンなので保存も共通）
  const getKey = () => { try { return localStorage.getItem(KEY_STORE) || ""; } catch (e) { return ""; } };
  const setKey = (v) => { try { v ? localStorage.setItem(KEY_STORE, v) : localStorage.removeItem(KEY_STORE); } catch (e) { /* 保存できなくても使える */ } };
  const SITE = "tenkizu_web";                          // API に渡す公開先（API 側で nwp_chart_web 以外はこの名前だけ許す）
  function setupRemote() {
    $("disk").hidden = true;
    $("rowKey").hidden = false;
    $("pass").value = getKey();
    $("pass").onchange = () => setKey($("pass").value.trim());
    document.querySelector("input[name=kind][value=sounding]").closest("label").hidden = true;      // 実況エマグラムはローカルのみ
    $("intro").innerHTML = "断面図・エマグラムを、サーバー（Render）でその場で作れます。<b>作図に合言葉は要りません</b>（1時間に8回まで）。" +
      "初回や久しぶりのときはサーバーの起動に<b>1分ほど</b>、データの取得を含めて<b>1〜2分</b>かかります。" +
      "作った図を<b>公開サイトに載せる</b>ときと、公開済みの図を<b>外す</b>ときだけ、渡された合言葉が要ります。下の一覧は、公開されている図です。";
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function remoteJson(path, opt) {
    const r = await fetch(REMOTE + path, opt);
    let j = {};
    try { j = await r.json(); } catch (e) { /* JSON でない応答 */ }
    return { r, j };
  }
  async function goRemote() {
    const k = kind();                                                           // xsection（断面図）／ emagram（エマグラム）
    const q = new URLSearchParams({ model: $("model").value, init: $("init").value, ft: $("ft").value, ptop: $("ptop").value });   // ptop: 高度設定
    for (const id of k === "xsection" ? ["lat1", "lon1", "lat2", "lon2"] : ["lat", "lon"]) q.set(id, $(id).value);
    if (k === "emagram" && $("pt").value) q.set("pt", "1");                       // 温位エマグラム
    if (k === "xsection") {                                                     // 断面の表示要素（色塗り・等値線・風矢羽）
      q.set("shade", document.querySelector("input[name=shade]:checked").value);
      q.set("contours", [...document.querySelectorAll("input[name=cont]:checked")].map((e) => e.value).join(","));
      q.set("barbs", document.querySelector("input[name=barbs]:checked").value);
    }
    $("go").disabled = true;
    const fail = (t) => { $("msg").textContent = t; $("msg").className = "err"; $("go").disabled = false; };
    const say = (t) => { $("msg").className = ""; $("msg").textContent = t; };
    const t0 = Date.now(), secs = () => Math.round((Date.now() - t0) / 1000);
    // サーバーがまだ起動していない（無料枠は止まっていると起動に1分ほどかかる）間は、接続できない・502/503/504（本文が JSON でない）が返る。失敗にせず待つ
    const starting = (res) => !res.r.ok && !res.j.error && [502, 503, 504].includes(res.r.status);
    const call = async (path, opt) => {
      for (;;) {
        let res = null;
        try { res = await remoteJson(path, opt); } catch (e) { /* 接続できない＝起動中 */ }
        if (res && !starting(res)) return res;
        if (secs() > 240) throw new Error("timeout");
        say(`サーバーを起動しています…${secs()}秒（止まっていると1分ほどかかります。そのままお待ちください）`);
        await sleep(4000);
      }
    };
    say("サーバーに接続しています…（久しぶりのときは起動に1分ほどかかります）");
    try {
      let { r, j } = await call("/api/" + k + "?" + q);
      if (!r.ok && r.status !== 202) return fail(j.error || `作図できません（HTTP ${r.status}）`);
      let restarts = 0, jid = j.job;       // ジョブIDは別に持つ（状態確認の応答を j に代入すると、IDが消える）
      while (j.state === "queued" || j.state === "running") {
        say((j.state === "queued" && j.position ? `順番待ちです（前に${j.position}件）` : "作図中です（データ取得が入ると1〜2分かかります）") + `…${secs()}秒`);
        await sleep(3000);
        if (secs() > 12 * 60) return fail("時間がかかりすぎています。しばらくしてからもう一度試してください");
        ({ r, j } = await call("/api/job/" + encodeURIComponent(jid)));
        if (r.status === 404) {                                                // サーバーが再起動して記録が消えた。同じ条件の再送は安全
          if (++restarts > 3) return fail("サーバーが何度も再起動しています。しばらくしてからもう一度試してください");
          say(`サーバーが再起動したようです。作図をやり直しています…（${restarts}回目）`);
          ({ r, j } = await call("/api/" + k + "?" + q));
          if (!r.ok && r.status !== 202) return fail(j.error || `作図できません（HTTP ${r.status}）`);
          jid = j.job;
          continue;
        }
        if (!r.ok) return fail(j.error || `状態を確認できません（HTTP ${r.status}）`);
      }
      if (j.state === "error") return fail(j.error || "作図できません");
      say("完了");
      showRemoteResult({ ...j, url: REMOTE + j.url }, false);
    } catch (e) { return fail("サーバーに接続できません（しばらくしてからもう一度試してください）"); }
    $("go").disabled = false;
  }

  // 結果の画像と、公開サイトに載せる／外すボタン。published: 公開サイトに載っているか
  function showRemoteResult(c, published) {
    const name = c.name || c.url.split("/").pop();
    const src = c.url;                                                         // 作ったばかりの図は API の絶対URL・一覧の図は相対URL
    $("result").innerHTML =
      `<p id="pubRow"><button id="btnPub">${published ? "公開から外す" : "公開サイトに載せる"}</button> ` +
      `<span class="note" id="pubMsg">${published ? "公開中です（誰でも見られます）。外すには合言葉が要ります。"
        : "公開サイトに載せると誰でも見られます（直近20件まで）。合言葉が要ります。作図結果は、しばらくすると API 側から消えます。"}</span></p>` +
      `<img alt="${esc(c.label || histText(c))}" src="${esc(src)}">` +
      (c.legal ? `<p class="note">${esc(c.legal)}</p>` : "");
    wireRemotePub(name, published);
  }

  function wireRemotePub(name, published) {
    const b = $("btnPub"), msg = $("pubMsg"), label = b.textContent;
    b.onclick = async () => {
      const key = ($("pass").value || "").trim();
      if (!key) { msg.textContent = "合言葉を入力してください（上の「合言葉」欄）"; return; }
      if (!b.dataset.armed) {                                                  // 誤操作を防ぐため2回押して確定する（5秒以内）
        b.dataset.armed = "1";
        b.textContent = published ? "本当に外す（もう一度押す）" : "本当に公開する（もう一度押す）";
        setTimeout(() => { if (b.dataset.armed) { delete b.dataset.armed; b.textContent = label; } }, 5000);
        return;
      }
      b.disabled = true; msg.textContent = "公開サイトへ反映しています…（数十秒かかります）";
      const undo = (t) => { msg.textContent = t; b.disabled = false; delete b.dataset.armed; b.textContent = label; };
      try {
        const { r, j } = await remoteJson(published ? "/api/unpublish" : "/api/publish", {
          method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + key }, body: JSON.stringify({ name, site: SITE }) });
        if (!r.ok) return undo(j.error || `反映できません（HTTP ${r.status}）`);
        setKey(key);                                                           // 合っていた合言葉は、この端末に覚えておく
        b.hidden = true;
        msg.textContent = (published ? "公開から外しました" : "公開しました") + "。公開サイトへの反映に1〜2分かかります（上の一覧は、そのあと再読み込みすると変わります）。";
      } catch (e) { undo("反映できません（しばらくしてからもう一度試してください）"); }
    };
  }

  boot().then(renderHist);              // 公開サイトかどうかが分かってから、一覧を出す
})();
