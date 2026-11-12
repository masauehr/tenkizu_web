// 実況エマグラム（高層ゾンデ・ワイオミング大学）の表示ページ（Vanilla JS）
// データは output/sounding/manifest.json（update_sounding.py が作り、GitHub Actions が公開する）。ローカルサーバーでは任意の地点・時刻も作れる（api/sounding）
(() => {
  const $ = (id) => document.getElementById(id);
  const OUT = "output/sounding/";
  const MODE_LABEL = { emagram: "エマグラム", pt: "温位エマグラム" };
  const S = { st: null, t: null, m: "emagram" };
  let M = null;                                                   // manifest

  const pad2 = (n) => String(n).padStart(2, "0");
  // 観測時刻 "YYYYMMDDHH"（UTC）→ 表示。日本時間も添える
  function fmtTime(t) {
    const d = new Date(Date.UTC(+t.slice(0, 4), +t.slice(4, 6) - 1, +t.slice(6, 8), +t.slice(8, 10)));
    const j = new Date(d.getTime() + 9 * 3600 * 1000);
    return `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)} ${t.slice(8, 10)}UTC（日本時間 ${pad2(j.getUTCMonth() + 1)}/${pad2(j.getUTCDate())} ${pad2(j.getUTCHours())}時）`;
  }
  const stInfo = (id) => (M.stations || []).find((s) => s.id === id) || { id, name: id, region: "" };
  const timesOf = (st) => (M.times || []).filter((x) => x.stations[st]).map((x) => x.time);   // 新しい順

  function readHash() {
    const q = new URLSearchParams(location.hash.slice(1));
    return { st: q.get("st"), t: q.get("t"), m: q.get("m") };
  }
  function writeHash() { history.replaceState(null, "", `#st=${S.st}&t=${S.t}&m=${S.m}`); }

  function buildStations() {
    const sel = $("stSel");
    sel.innerHTML = "";
    const have = new Set((M.times || []).flatMap((x) => Object.keys(x.stations)));
    const groups = [];
    for (const s of M.stations || []) {
      if (!have.has(s.id)) continue;                               // 一度もデータが無い地点は出さない
      let g = groups.find((x) => x.region === s.region);
      if (!g) { g = { region: s.region, list: [] }; groups.push(g); }
      g.list.push(s);
    }
    for (const g of groups) {
      const og = document.createElement("optgroup");
      og.label = g.region;
      for (const s of g.list) og.appendChild(new Option(`${s.name}（${s.id}）`, s.id));
      sel.appendChild(og);
    }
  }
  function buildTimes() {
    const sel = $("tSel");
    sel.innerHTML = "";
    for (const t of timesOf(S.st)) sel.add(new Option(fmtTime(t), t));
  }

  function show() {
    const ts = timesOf(S.st);
    if (!ts.length) { $("view").hidden = true; msg("この地点のデータはまだありません"); return; }
    if (!ts.includes(S.t)) S.t = ts[0];
    $("stSel").value = S.st; $("tSel").value = S.t;
    for (const b of document.querySelectorAll("#modeBtns button")) b.classList.toggle("on", b.dataset.m === S.m);
    const i = ts.indexOf(S.t);
    $("btnOld").disabled = i >= ts.length - 1; $("btnNew").disabled = i <= 0;
    const modes = M.times.find((x) => x.time === S.t).stations[S.st];
    if (!modes.includes(S.m)) S.m = modes[0];
    const si = stInfo(S.st);
    const img = $("img");
    msg("");
    img.onerror = () => { msg("画像が見つかりません（公開の更新の途中かもしれません。少し待って再読み込みしてください）", true); };
    img.src = `${OUT}${S.t}/${S.st}_${S.m}.png`;
    $("view").hidden = false;
    $("caption").textContent = `${si.name}（${S.st}）｜${MODE_LABEL[S.m]}｜観測 ${fmtTime(S.t)}`;
    writeHash();
  }
  function msg(t, err) { const m = $("msg"); m.textContent = t; m.className = err ? "err" : ""; }

  function step(dir) {            // dir=+1: 新しい方へ、-1: 古い方へ
    const ts = timesOf(S.st), i = ts.indexOf(S.t) - dir;
    if (i >= 0 && i < ts.length) { S.t = ts[i]; $("tSel").value = S.t; show(); }
  }

  async function boot() {
    try { M = await (await fetch(OUT + "manifest.json", { cache: "no-store" })).json(); }
    catch (e) { msg("実況エマグラムはまだ作られていません（公開の更新を待ってください）", true); return; }
    if (!(M.times || []).length) { msg("実況エマグラムはまだ作られていません", true); return; }
    $("legal").textContent = M.legal || "";
    $("updated").textContent = M.updated ? `更新 ${M.updated.replace("T", " ").replace("Z", " UTC")}` : "";
    buildStations();
    const h = readHash();
    S.st = [...$("stSel").options].some((o) => o.value === h.st) ? h.st : ([...$("stSel").options].find((o) => o.value === "47918") ? "47918" : $("stSel").options[0].value);
    S.t = h.t; S.m = ["emagram", "pt"].includes(h.m) ? h.m : "emagram";
    buildTimes();
    show();
    $("stSel").onchange = () => { S.st = $("stSel").value; buildTimes(); show(); };
    $("tSel").onchange = () => { S.t = $("tSel").value; show(); };
    $("btnOld").onclick = () => step(-1); $("btnNew").onclick = () => step(+1);
    for (const b of document.querySelectorAll("#modeBtns button")) b.onclick = () => { S.m = b.dataset.m; show(); };
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
      if (e.key === "ArrowLeft") step(-1); else if (e.key === "ArrowRight") step(+1);
    });
  }

  // ---------- ローカルサーバーだけ: 任意の地点・時刻（api/sounding）
  async function detectLocal() {
    try {
      const j = await (await fetch("api/status", { cache: "no-store" })).json();
      if (typeof j.running !== "boolean") return;
    } catch (e) { return; }
    $("localBox").hidden = false; $("lnkCustom").hidden = false;
    const dl = $("lStations");
    for (const s of (M && M.stations) || []) dl.appendChild(Object.assign(document.createElement("option"), { value: s.id, label: s.name }));
    const d = new Date(Date.now() - 24 * 3600 * 1000);
    $("lDate").value = `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
    $("lGo").onclick = async () => {
      const wmo = $("lWmo").value.trim(), date = $("lDate").value, hour = $("lHour").value, mode = $("lMode").value;
      if (!date) { $("lMsg").textContent = "日付を入れてください"; return; }
      const q = new URLSearchParams({ wmo, time: date.replace(/-/g, "") + hour, mode });
      $("lGo").disabled = true; $("lMsg").textContent = "取得・作図中…（数秒）";
      try {
        const r = await fetch("api/sounding?" + q), j = await r.json();
        if (!r.ok) { $("lMsg").textContent = j.error || "作図できません"; }
        else {
          $("lMsg").textContent = "完了（この図は公開されません）";
          msg("");
          $("img").onerror = () => msg("画像を読めません", true);
          $("img").src = j.url + "?t=" + Date.now();
          $("view").hidden = false;
          $("caption").textContent = `ローカルで作った図｜${wmo}｜${MODE_LABEL[mode]}｜観測 ${fmtTime(date.replace(/-/g, "") + hour)}`;
        }
      } catch (e) { $("lMsg").textContent = "サーバーに接続できません"; }
      $("lGo").disabled = false;
    };
  }

  boot().then(detectLocal);
})();
