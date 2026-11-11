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
        bt.onclick = () => { $("lat1").value = a; $("lon1").value = b; $("lat2").value = c; $("lon2").value = d; };
        box.appendChild(bt);
      }
    } else {
      for (const [n, la, lo] of PRESET_E) {
        const bt = document.createElement("button"); bt.textContent = n;
        bt.onclick = () => { $("lat").value = la; $("lon").value = lo; };
        box.appendChild(bt);
      }
    }
  }
  function applyKind() {
    $("rowX").hidden = kind() !== "xsection";
    $("rowE").hidden = kind() !== "emagram";
    buildPresets();
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
