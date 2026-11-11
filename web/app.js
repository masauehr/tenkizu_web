// tenkizu_web（ECMWF/GFS/GSM の天気図・断面図・エマグラム）（Vanilla JS）
// 画像規則: output/{model}/{init}/{region}/{chart}_{ft:03d}.png、一覧は output/manifest.json
// パスは相対指定（GitHub Pages のようにサブパス配下へ置いても動く）。更新API(api/)があるのはローカルサーバーだけ。
//
// 2モデル比較モード: 同じ図を、2つのモデルで左右に並べる。
//   左のモデルが基準（初期値 S.init と予報時間 S.ft を持つ）。右のモデルに同じ初期値が無ければ、
//   予報有効時刻が一致する別の初期値の画像を探して出す（画用紙版と同じ考え方）。
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const OUT = "output/";
  const API = "api/";
  const HOUR = 3600e3;
  const S = { manifest: null, local: false, model: null, model2: null, cmp: false, fig: false, region2: null, chart2: null, init: null, region: null, chart: null, ft: 0, timer: null };
  const cache = new Map(); // 先読み画像（再生を滑らかにする）

  const imgUrl = (model, init, region, chart, ft) =>
    `${OUT}${model}/${init}/${region}/${chart}_${String(ft).padStart(3, "0")}.png`;

  const pad = (n) => String(n).padStart(2, "0");

  // "2026110318" → Date(UTC)
  function parseInit(s) {
    return new Date(Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10)));
  }
  const initMs = (s) => parseInit(s).getTime();
  function fmt(d, offsetH, tz) {
    const t = new Date(d.getTime() + offsetH * HOUR);
    const w = "日月火水木金土"[t.getUTCDay()];
    return `${t.getUTCMonth() + 1}/${t.getUTCDate()}(${w}) ${pad(t.getUTCHours())}時${tz}`;
  }

  const modelKeys = () => Object.keys(S.manifest.models || {});
  const modelInits = (model) => ((S.manifest.models[model] || {}).inits) || [];   // 新しい順
  const inits = () => modelInits(S.model);
  const cur = () => inits().find((i) => i.init === S.init);
  // manifest の更新時刻をUTCだけで短く表す（例: 11/6 03:06UTC）
  const updatedUtc = (iso) => {
    const t = new Date(iso);
    return `${t.getUTCMonth() + 1}/${t.getUTCDate()} ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}UTC`;
  };
  const labelOf = (m) => (S.manifest.models[m] || {}).label || m;
  // 比較に使うモデル（比較オフなら左だけ）
  const shownModels = () => (S.cmp && S.model2 ? [S.model, S.model2] : [S.model]);

  // そのモデルの、現在の初期値での {region: {chart: [ft]}}。現在の初期値が無ければ最新の初期値の内容で代用（図・範囲の有無の判定用）
  const regionMap = (model) => (modelInits(model).find((i) => i.init === S.init) || modelInits(model)[0] || {}).regions || {};
  const listAt = (model, init, region, chart) => {
    const e = modelInits(model).find((i) => i.init === init);
    return ((((e || {}).regions || {})[region] || {})[chart]) || [];
  };
  const regionsAvail = () => [...new Set(shownModels().flatMap((m) => Object.keys(regionMap(m))))].sort((x, y) => regionRank(x) - regionRank(y));
  // 図種の並びは 高層→地上。公開側の manifest は次の更新まで古い順のままなので、画面側にも順を持つ（nwp_charts.CHARTS と同じ順）。
  // 未知の図種は末尾（manifest の順）
  const CHART_ORDER = ["polar300", "polar500", "fax57", "fax78", "qvec850", "surffax", "instab", "xs130", "xs140", "em_47401", "em_47646", "em_47778", "em_47807", "em_47936"];
  // 地域ボタンの並びと表示名（日本周辺は平面図）。北半球が先頭＝初期表示（北半球300hPa）
  const REGION_ORDER = ["polar", "japan", "okinawa", "asia", "xsec", "emagram"];
  const REGION_LABEL = { japan: "平面図", custom: "自由選択（断面図・エマグラム）" };
  const regionLabel = (r) => (r === "custom" && !S.local ? "保存された断面図・エマグラム（公開分）" : REGION_LABEL[r] || S.manifest.regions[r] || r);
  // 地域を切り替えたときに最初に出す図（無ければ並びの先頭）。エマグラムは那覇
  const REGION_FIRST_CHART = { polar: "polar300", xsec: "xs130", emagram: "em_47936" };
  const firstChart = (region, charts) => (charts.includes(REGION_FIRST_CHART[region]) ? REGION_FIRST_CHART[region] : charts[0]);
  const regionRank = (r) => { const i = REGION_ORDER.indexOf(r); return i >= 0 ? i : 100; };
  const chartRank = (c) => {
    const i = CHART_ORDER.indexOf(c);
    return i >= 0 ? i : 100 + Object.keys(S.manifest.charts).indexOf(c);
  };
  const byChart = (list) => list.sort((a, b) => chartRank(a) - chartRank(b));
  const chartsAvail = (region) => byChart([...new Set(shownModels().flatMap((m) => Object.keys(regionMap(m)[region] || {})))]);

  // ---------- 別の図を並べる: 右に、同じモデル・初期値・予報時間の別の地域・図（平面図と断面図など）、
  // またはローカルの自由選択で作った結果（直近10件）を出す。一覧はサーバー（/api/custom/list）が output/custom/ の画像ファイルから作る
  // （ブラウザの保存には頼らない。別のブラウザでも、サイトデータを消しても同じ一覧になる）
  let customCache = [];                                // サーバーが返した一覧（新しい順・最大10件）
  const customList = () => customCache;
  async function loadCustom() {                        // ローカルサーバーから取得できたか（＝ローカルか）を返す
    try {
      const j = await (await fetch(API + "custom/list", { cache: "no-store" })).json();
      if (Array.isArray(j.items)) { customCache = j.items; return true; }
    } catch (e) { /* 公開サイトにはこの API が無い */ }
    try {                                              // 公開サイト: 作者が公開した直近10件の一覧（静的ファイル）
      const j = await (await fetch(OUT + "custom/list.json", { cache: "no-store" })).json();
      customCache = Array.isArray(j.items) ? j.items : [];
    } catch (e) { customCache = []; }
    return false;
  }
  const customBase = (c) => c.url.split("/").pop();    // 画像のファイル名（選択欄の値・URLの chart2 に使う）
  const customFind = (chart) => { const l = customList(); return chart === "last" ? (l[0] || null) : (l.find((c) => customBase(c) === chart) || null); };
  // 自由選択は、ローカルサーバーで開いているとき（作図の有無にかかわらず）か、作図済みの結果があるときに選べる
  const regions2Avail = () => [...regionsAvail(), ...(S.local || customList().length ? ["custom"] : [])];
  const charts2Avail = (region) => (region === "custom" ? (customList().length ? customList().map(customBase) : ["last"]) : chartsAvail(region));
  const customText = (c) => `${c.label}（${c.model} ${c.initText} ＋${c.ft}h）`;
  const chartLabel2 = (region, chart) => {
    if (region === "custom") { const c = customFind(chart); return c ? customText(c) : "自由選択（まだ作図していません）"; }
    return S.manifest.charts[chart] || chart;
  };
  function defaultFig() {      // 左が断面でなければ断面（130E）、断面なら平面図の先頭を右に出す
    const regs = regionsAvail();
    S.region2 = regs.includes("xsec") && S.region !== "xsec" ? "xsec" : (regs.find((r) => r !== S.region) || S.region);
    S.chart2 = firstChart(S.region2, charts2Avail(S.region2));
  }
  function validFig() {
    if (!regions2Avail().includes(S.region2)) { defaultFig(); return; }
    if (S.region2 === "custom" && S.chart2 === "last" && customList().length) S.chart2 = customBase(customList()[0]);   // 「最新」は先頭の画像に置き換える
    const cs = charts2Avail(S.region2);
    if (!cs.includes(S.chart2)) S.chart2 = firstChart(S.region2, cs);
  }

  // 予報時間 ft がその図に無いときは、直前の予報時間、無ければ最小のものを使う
  const pickFt = (list, ft) => {
    if (list.includes(ft)) return ft;
    const before = list.filter((f) => f <= ft);
    return before.length ? Math.max(...before) : Math.min(...list);
  };

  // model の図を、全体（左のモデルの初期値 S.init）の予報時間 gft のときに、どの初期値・FTの画像で出すか決める。
  //  - そのモデルに S.init があれば、その初期値（刻みが合わなければ直前のFT）
  //  - 無ければ、予報有効時刻が一致する別の初期値の画像を探す
  function resolve(model, region, chart, gft) {
    if (gft === undefined) gft = S.ft;
    if (modelInits(model).some((i) => i.init === S.init)) {
      const list = listAt(model, S.init, region, chart);
      return list.length ? { init: S.init, ft: pickFt(list, gft), exact: list.includes(gft) } : null;
    }
    const valid = initMs(S.init) + gft * HOUR;
    for (const e of modelInits(model)) {
      const ft = (valid - initMs(e.init)) / HOUR;
      if (ft >= 0 && listAt(model, e.init, region, chart).includes(ft)) return { init: e.init, ft, exact: true, other: true };
    }
    return null;
  }

  // 全体の予報時間の候補。S.init が無いモデルは、その初期値の予報時刻を全体のFTへ換算して加える（負は除く）
  function modelFts(model) {
    if (modelInits(model).some((i) => i.init === S.init)) return listAt(model, S.init, S.region, S.chart);
    const out = [];
    for (const e of modelInits(model)) {
      const shift = (initMs(e.init) - initMs(S.init)) / HOUR;
      for (const f of listAt(model, e.init, S.region, S.chart)) if (f + shift >= 0) out.push(f + shift);
    }
    return out;
  }
  const fts = () => [...new Set(shownModels().flatMap(modelFts))].sort((a, b) => a - b);

  // ---------- URLハッシュ（リロード・共有で状態を保つ）
  function saveHash() {
    history.replaceState(null, "", `#model=${S.model}&init=${S.init}&region=${S.region}&chart=${S.chart}&ft=${S.ft}` +
      (S.cmp ? `&cmp=1&model2=${S.model2}` : "") + (S.fig ? `&fig=1&region2=${S.region2}&chart2=${S.chart2}` : ""));
  }
  function loadHash() {
    return Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
  }

  // ---------- 初期化
  async function boot() {
    let m;
    try {
      const r = await fetch(OUT + "manifest.json", { cache: "no-store" });
      m = await r.json();
    } catch (e) {
      $("views").innerHTML = '<div style="padding:40px">manifest.json を読めません。先に <code>python update_latest.py</code> を実行してください。</div>';
      return;
    }
    S.manifest = m;
    if (!modelKeys().length) {
      $("views").innerHTML = '<div style="padding:40px">天気図がまだありません。<code>python update_latest.py</code> を実行してください。</div>';
      return;
    }
    $("updated").textContent = "更新 " + updatedUtc(m.updated);

    S.local = await loadCustom();                     // ローカルサーバーなら自由選択の一覧が返る（公開サイトでは取得できない）
    const h = loadHash();
    const models = modelKeys();
    S.model = models.includes(h.model) ? h.model : models[0];
    S.init = inits().some((i) => i.init === h.init) ? h.init : inits()[0].init;
    S.cmp = h.cmp === "1" && models.length > 1;
    S.fig = h.fig === "1" && !S.cmp;
    S.region2 = h.region2 || null; S.chart2 = h.chart2 || null;
    S.model2 = models.includes(h.model2) && h.model2 !== S.model ? h.model2 : (models.find((k) => k !== S.model) || null);
    const regs = regionsAvail();
    S.region = regs.includes(h.region) ? h.region : regs[0];
    const charts = chartsAvail(S.region);
    S.chart = charts.includes(h.chart) ? h.chart : firstChart(S.region, charts);   // 既定は北半球300hPa
    S.ft = +h.ft || 0;

    buildModelSel();
    buildInitSel();
    buildRegionBtns();
    buildTabs();
    applyCmpUi();
    update();
    buildCredit();
  }

  function fillModelOptions(sel, value) {
    sel.innerHTML = "";
    for (const k of modelKeys()) {
      const o = document.createElement("option");
      o.value = k; o.textContent = labelOf(k);
      sel.appendChild(o);
    }
    sel.value = value;
  }

  function buildModelSel() {
    const sel = $("modelSel");
    fillModelOptions(sel, S.model);
    sel.onchange = () => {
      S.model = sel.value;
      if (S.cmp && S.model2 === S.model) S.model2 = modelKeys().find((k) => k !== S.model);   // 左右が同じにならないように
      if (!inits().some((i) => i.init === S.init)) S.init = inits()[0].init; // そのモデルに同じ初期値が無ければ最新へ
      applyCmpUi(); keepValid(); buildInitSel(); buildRegionBtns(); buildTabs(); update(); buildCredit();
    };
    const sel2 = $("model2Sel");
    fillModelOptions(sel2, S.model2 || S.model);
    sel2.onchange = () => {
      S.model2 = sel2.value;
      if (S.model2 === S.model) { S.model = modelKeys().find((k) => k !== S.model2); S.init = inits()[0].init; $("modelSel").value = S.model; buildInitSel(); }
      keepValid(); buildRegionBtns(); buildTabs(); update(); buildCredit();
    };
  }

  // 比較モードの表示・非表示（2つ目のモデル欄、右の画像、幅）
  function applyCmpUi() {
    const multi = modelKeys().length > 1;
    const dual = S.cmp || S.fig;                 // 左右に2枚並べる状態（2モデル比較 または 別の図を並べる）
    $("cmpChk").disabled = !multi;
    $("cmpChk").checked = S.cmp;
    $("figChk").checked = S.fig;
    $("cmpBox").hidden = !S.cmp;
    $("figBox").hidden = !S.fig;
    $("view2").hidden = !dual;
    $("cap1").hidden = !dual;
    document.body.classList.toggle("cmp", dual);
    $("modelLabel").textContent = S.cmp ? "左のモデル" : "モデル";
    if (S.cmp && S.model2) { $("model2Sel").value = S.model2; $("modelSel").value = S.model; }
    if (S.fig) buildFigSelects();
  }

  // 右の図（地域・図）の選択欄
  function buildFigSelects() {
    validFig();
    const rs = $("region2Sel"), cs = $("chart2Sel");
    rs.innerHTML = ""; cs.innerHTML = "";
    for (const r of regions2Avail()) rs.add(new Option(regionLabel(r), r));
    for (const c of charts2Avail(S.region2)) cs.add(new Option(chartLabel2(S.region2, c), c));
    rs.value = S.region2; cs.value = S.chart2;
  }
  $("region2Sel").onchange = (e) => { S.region2 = e.target.value; S.chart2 = firstChart(S.region2, charts2Avail(S.region2)); buildFigSelects(); update(); };
  $("chart2Sel").onchange = (e) => { S.chart2 = e.target.value; update(); };

  // 出典・ライセンス・免責・改変の表示（配信元の利用条件に基づく。文言は manifest.json から読む）
  const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const linkify = (t) => esc(t).replace(/(https?:\/\/[^\s）)]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  function buildCredit() {
    // 画面上部に常時出す短い表示（比較中は両モデル分）。ライセンス名はリンクにする
    $("credit").innerHTML = shownModels().map((m) => {
      const md = S.manifest.models[m];
      return esc(md.credit).replace("CC BY 4.0", `<a href="${md.license_url}" target="_blank" rel="noopener noreferrer">CC BY 4.0</a>`);
    }).join("<br>");
    // フッターに全モデルの詳細（どのモデルを選んでいても全て表示）
    let html = "";
    for (const k of modelKeys()) {
      const m = S.manifest.models[k];
      html += `<h3>${esc(m.label)}</h3>` + m.notice.map((t) => `<p>${linkify(t)}</p>`).join("");
    }
    if (S.manifest.disclaimer) html += `<h3>本サイトについて</h3><p>${esc(S.manifest.disclaimer)}</p>`;
    $("legal").innerHTML = html;
  }

  function buildInitSel() {
    const sel = $("initSel");
    sel.innerHTML = "";
    for (const i of inits()) {
      const o = document.createElement("option");
      const d = parseInit(i.init);
      o.value = i.init;
      o.textContent = fmt(d, 0, "UTC");
      sel.appendChild(o);
    }
    sel.value = S.init;
    sel.onchange = () => { S.init = sel.value; keepValid(); buildRegionBtns(); buildTabs(); update(); };
  }

  function buildRegionBtns() {
    const box = $("regionBtns");
    box.innerHTML = "";
    for (const r of regionsAvail()) {
      const b = document.createElement("button");
      b.textContent = regionLabel(r);
      b.className = r === S.region ? "on" : "";
      b.onclick = () => { S.region = r; keepValid(); buildRegionBtns(); buildTabs(); update(); };
      box.appendChild(b);
    }
  }

  function buildTabs() {
    const box = $("chartTabs");
    box.innerHTML = "";
    for (const c of chartsAvail(S.region)) {
      const b = document.createElement("button");
      b.textContent = S.manifest.charts[c] || c;
      b.className = c === S.chart ? "on" : "";
      b.onclick = () => { S.chart = c; update(); buildTabs(); };
      box.appendChild(b);
    }
  }

  // 初期値・地域・モデルを切り替えたあと、地域・図種・FTが存在しなければ近いものへ寄せる
  function keepValid() {
    const regs = regionsAvail();
    if (!regs.includes(S.region)) S.region = regs[0];
    const charts = chartsAvail(S.region);
    if (!charts.includes(S.chart)) S.chart = firstChart(S.region, charts);
    if (S.fig) validFig();
    const f = fts();
    if (f.length && !f.includes(S.ft)) S.ft = f.reduce((a, b) => (Math.abs(b - S.ft) < Math.abs(a - S.ft) ? b : a));
  }

  // ---------- 表示更新
  // 1つの画像枠（n=1:左 / n=2:右）を、model の図で更新する。解決できなければ「画像なし」。戻り値は解決結果。
  function renderView(n, model, region, chart) {
    const img = $(n === 1 ? "chartImg" : "chartImg2");
    const nodata = $(n === 1 ? "noData" : "noData2");
    img.onerror = null;                           // 自由選択の画像用に付けた処理を残さない
    const r = resolve(model, region, chart, S.ft);
    if (!r) {
      const any = modelInits(model).some((i) => listAt(model, i.init, region, chart).length);
      nodata.textContent = any ? "この予報時刻の画像なし（初期値が違うため時刻が合いません。→キーなどで時刻を進めてください）"
                               : "このモデルには、この図・範囲の画像がありません";
      nodata.hidden = false;
      img.removeAttribute("src");
      return null;
    }
    nodata.hidden = true;
    img.src = imgUrl(model, r.init, region, chart, r.ft);
    return r;
  }

  // 別の図を並べる: 右の枠。自由選択（ローカル）の結果なら、保存された画像をそのまま出す（予報時間とは連動しない）
  function renderFig() {
    if (S.region2 !== "custom") return renderView(2, S.model, S.region2, S.chart2);
    const c = customFind(S.chart2), img = $("chartImg2"), nodata = $("noData2");
    if (!c) {
      nodata.innerHTML = "まだ自由選択（断面図・エマグラム）で作図していません。<a href=\"custom.html\">自由選択のページ</a>で作図すると、ここに並べて表示できます。";
      nodata.hidden = false; img.removeAttribute("src"); return null;
    }
    nodata.hidden = true;
    img.onerror = () => { nodata.textContent = "自由選択の画像が見つかりません（ディスクの整理で削除された可能性があります。もう一度作図してください）"; nodata.hidden = false; };
    img.src = c.url;
    return { custom: c };
  }

  // 別の図を並べるときの見出し（図の名前・モデル・初期値・予報時間）
  function capFig(model, region, chart, r) {
    const head = `<b>${esc(chartLabel2(region, chart))}</b> ｜ ${esc(labelOf(model))} ｜ `;
    if (!r) return head + "画像なし";
    const note = r.init !== S.init ? "（別の初期値・予報時刻を合わせて表示）" : (r.exact ? "" : `（FT${r.ft}h＝直前の予報時間）`);
    return head + `初期値 ${fmt(parseInit(r.init), 0, "UTC")} ＋${r.ft}h${note}`;
  }
  function capCustom() {
    const c = customFind(S.chart2);
    const name = S.local ? "自由選択（断面図・エマグラム）" : "保存された断面図・エマグラム";   // 公開サイトでは新しく作れないので「自由選択」とは出さない
    return c ? `<b>${name}</b> ｜ ${esc(c.label)} ｜ ${esc(c.model)} 初期値 ${esc(c.initText)} ＋${c.ft}h（作図済みの画像。予報時間とは連動しません）`
             : "<b>自由選択（断面図・エマグラム）</b> ｜ 未作図";
  }

  // 比較モードの見出し: モデル名・初期値・予報時間（初期値が違う/刻みが合わない場合は分かるように）
  function capText(model, r) {
    if (!r) return `<b>${esc(labelOf(model))}</b> ｜ 画像なし`;
    const d = parseInit(r.init);
    const note = r.init !== S.init ? "（別の初期値・予報時刻を合わせて表示）" : (r.exact ? "" : `（FT${r.ft}h＝直前の予報時間）`);
    return `<b>${esc(labelOf(model))}</b> ｜ 初期値 ${fmt(d, 0, "UTC")} ＋${r.ft}h${note}`;
  }

  function update() {
    const f = fts();
    const slider = $("ftSlider");
    slider.max = Math.max(f.length - 1, 0);
    if (!f.includes(S.ft)) S.ft = f[0] ?? 0;
    slider.value = Math.max(f.indexOf(S.ft), 0);

    const r1 = renderView(1, S.model, S.region, S.chart);
    const r2 = S.cmp && S.model2 ? renderView(2, S.model2, S.region, S.chart) : (S.fig ? renderFig() : null);

    // 保存リンク（左・右）
    if (r1) {
      const u1 = imgUrl(S.model, r1.init, S.region, S.chart, r1.ft);
      $("dlLink").href = u1;
      $("dlLink").download = `${S.model}_${r1.init}_${S.region}_${S.chart}_FT${r1.ft}.png`;
    }
    if (S.cmp) {
      $("cap1").innerHTML = capText(S.model, r1);
      $("cap2Text").innerHTML = capText(S.model2, r2);
      if (r2) {
        $("dl2").href = imgUrl(S.model2, r2.init, S.region, S.chart, r2.ft);
        $("dl2").download = `${S.model2}_${r2.init}_${S.region}_${S.chart}_FT${r2.ft}.png`;
      }
    }

    if (S.fig) {
      $("cap1").innerHTML = capFig(S.model, S.region, S.chart, r1);
      $("cap2Text").innerHTML = S.region2 === "custom" ? capCustom() : capFig(S.model, S.region2, S.chart2, r2);
      if (r2 && r2.custom) { $("dl2").href = r2.custom.url; $("dl2").download = r2.custom.url.split("/").pop(); }
      else if (r2) {
        $("dl2").href = imgUrl(S.model, r2.init, S.region2, S.chart2, r2.ft);
        $("dl2").download = `${S.model}_${r2.init}_${S.region2}_${S.chart2}_FT${r2.ft}.png`;
      }
    }

    const d = parseInit(S.init);
    $("timeInfo").innerHTML = S.cmp
      ? `予報有効時刻 <b>${fmt(d, S.ft, "UTC")}</b> ／ 左のモデルの初期値 ${fmt(d, 0, "UTC")} ＋${S.ft}h`
      : `初期値 ${fmt(d, 0, "UTC")} ／ <b>FT=${S.ft}h</b> ／ 予報時刻 <b>${fmt(d, S.ft, "UTC")}</b>`;
    applySize();                                  // timeInfo の行数で画像の開始位置が変わるため、収まる高さを再計算

    buildThumbs();
    preload();
    saveHash();
  }

  function buildThumbs() {
    const box = $("thumbs");
    const regs = (cur() || {}).regions || {};
    const charts = regs[S.region] || {};
    const list = byChart(Object.keys(charts).filter((c) => charts[c].includes(S.ft)));
    // 図の構成（モデル・初期値・範囲・図種の並び）が同じなら、要素は作り直さず画像だけ差し替える
    const key = [S.model, S.init, S.region, list.join(",")].join("|");
    if (box.dataset.key === key) {
      if (S.timer) return;                                   // 再生中は下の小さな画像を動かさない（ちらつき防止）
      box.querySelectorAll("figure").forEach((fig) => {
        const c = fig.dataset.chart;
        fig.className = c === S.chart ? "on" : "";
        const img = fig.querySelector("img");
        const u = imgUrl(S.model, S.init, S.region, c, S.ft);
        if (img.dataset.want === u) return;
        img.dataset.want = u;
        // 読み込み完了後に差し替える（読み込み中は前の画像を残し、空白を出さない）
        const pre = new Image();
        pre.onload = () => { if (img.dataset.want === u) img.src = u; };
        pre.src = u;
      });
      return;
    }
    box.dataset.key = key;
    box.innerHTML = "";
    for (const c of list) {
      const fig = document.createElement("figure");
      fig.className = c === S.chart ? "on" : "";
      fig.dataset.chart = c;
      const u = imgUrl(S.model, S.init, S.region, c, S.ft);
      fig.innerHTML = `<img loading="lazy" src="${u}" data-want="${u}" alt=""><figcaption>${S.manifest.charts[c] || c}</figcaption>`;
      fig.onclick = () => { S.chart = c; update(); buildTabs(); };
      box.appendChild(fig);
    }
  }

  // 同じ図種の全FTを先読み（アニメーション用。比較中は両モデル分）
  function preload() {
    for (const ft of fts()) {
      for (const model of shownModels()) {
        const r = resolve(model, S.region, S.chart, ft);
        if (!r) continue;
        const u = imgUrl(model, r.init, S.region, S.chart, r.ft);
        if (!cache.has(u)) { const i = new Image(); i.src = u; cache.set(u, i); }
      }
    }
  }

  // ---------- 操作
  function step(d) {
    const f = fts();
    if (!f.length) return;
    let i = f.indexOf(S.ft) + d;
    i = (i + f.length) % f.length; // 端で循環
    S.ft = f[i];
    update();
  }
  function setIndex(i) { const f = fts(); if (f.length) { S.ft = f[Math.min(Math.max(i, 0), f.length - 1)]; update(); } }
  function play() {
    if (S.timer) { clearInterval(S.timer); S.timer = null; $("btnPlay").textContent = "再生"; buildThumbs(); return; }   // 停止時に下の画像を現在の時刻へ更新
    $("btnPlay").textContent = "停止";
    S.timer = setInterval(() => step(1), +$("speedSel").value);
  }

  // 2モデル比較の切り替え
  function setCmp(on) {
    S.cmp = on && modelKeys().length > 1;
    if (S.cmp) S.fig = false;                     // 別の図を並べるとは同時に使えない
    if (S.cmp && (!S.model2 || S.model2 === S.model)) S.model2 = modelKeys().find((k) => k !== S.model);
    applyCmpUi(); keepValid(); buildRegionBtns(); buildTabs(); update(); buildCredit();
  }

  // 別の図を並べる（2モデル比較とは同時に使えない）
  function setFig(on) {
    S.fig = on;
    if (on) { S.cmp = false; if (!S.region2) defaultFig(); }
    applyCmpUi(); keepValid(); buildRegionBtns(); buildTabs(); update(); buildCredit();
    if (on) refreshCustom(false);                  // 自由選択の一覧（サーバー）を読み直す
  }

  // 左右の入れ替え。予報有効時刻を保つため、右の解決結果（初期値・FT）を新しい左の基準にする
  function swapModels() {
    if (!S.cmp || !S.model2) return;
    const r2 = resolve(S.model2, S.region, S.chart, S.ft);
    [S.model, S.model2] = [S.model2, S.model];
    if (r2) { S.init = r2.init; S.ft = r2.ft; }
    else if (!inits().some((i) => i.init === S.init)) { S.init = inits()[0].init; }
    $("modelSel").value = S.model; $("model2Sel").value = S.model2;
    buildInitSel(); keepValid(); buildRegionBtns(); buildTabs(); update(); buildCredit();
  }

  $("btnPrev").onclick = () => step(-1);
  $("btnNext").onclick = () => step(1);
  $("btnFirst").onclick = () => setIndex(0);
  $("btnLast").onclick = () => setIndex(fts().length - 1);
  $("btnPlay").onclick = play;
  $("ftSlider").oninput = (e) => setIndex(+e.target.value);
  // ---------- 画像の表示サイズ（画面に収める／幅いっぱい／割合）
  // fit: 画像の上端から画面の下端までに収める。ヘッダーの折り返し数で位置が変わるので、都度計算する
  function applySize() {
    const v = $("sizeSel").value, views = $("views");
    views.classList.toggle("size-fit", v === "fit");
    views.classList.toggle("size-pct", v !== "fit");
    if (v === "fit") {
      const top = views.getBoundingClientRect().top + window.scrollY;   // ページ先頭からの位置
      views.style.setProperty("--avail", Math.max(240, window.innerHeight - top - 16) + "px");
    } else {
      views.style.setProperty("--imgw", v === "wide" ? "100%" : v + "%");
    }
  }
  try { const sv = localStorage.getItem("tenkizu_size"); if (sv && [...$("sizeSel").options].some((o) => o.value === sv)) $("sizeSel").value = sv; } catch (e) { /* 保存できない環境は既定のまま */ }
  $("sizeSel").onchange = () => { try { localStorage.setItem("tenkizu_size", $("sizeSel").value); } catch (e) { /* 無視 */ } applySize(); };
  window.addEventListener("resize", applySize);
  applySize();

  $("speedSel").onchange = () => { if (S.timer) { play(); play(); } };
  $("cmpChk").onchange = (e) => setCmp(e.target.checked);
  $("figChk").onchange = (e) => setFig(e.target.checked);
  $("btnSwap").onclick = swapModels;

  // 使い方（ヘルプ）
  const toggleHelp = (on) => { $("help").hidden = on === undefined ? !$("help").hidden : !on; };
  $("btnHelp").onclick = () => toggleHelp();
  $("btnHelpClose").onclick = () => toggleHelp(false);

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("help").hidden) { toggleHelp(false); return; }
    if (!S.manifest || e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
    const charts = chartsAvail(S.region);
    const ci = charts.indexOf(S.chart);
    if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowUp") { S.chart = charts[(ci - 1 + charts.length) % charts.length]; update(); buildTabs(); }
    else if (e.key === "ArrowDown") { S.chart = charts[(ci + 1) % charts.length]; update(); buildTabs(); }
    else if (e.key === " ") play();
    else if (e.key === "Home") setIndex(0);
    else if (e.key === "End") setIndex(fts().length - 1);
    else if (e.key === "c" || e.key === "C") setCmp(!S.cmp);
    else if (e.key === "f" || e.key === "F") setFig(!S.fig);
    else if (e.key === "h" || e.key === "H") toggleHelp();
    else return;
    e.preventDefault();
  });

  // ---------- 最新データの取得（サーバーの /api/update を呼ぶ）
  async function reloadManifest() {
    const m = await (await fetch(OUT + "manifest.json", { cache: "no-store" })).json();
    if (!Object.keys(m.models || {}).length) return;
    const prevNewest = S.manifest && S.manifest.models[S.model] ? S.manifest.models[S.model].inits[0].init : null;
    S.manifest = m;
    if (!m.models[S.model]) S.model = modelKeys()[0];
    if (!m.models[S.model2] || S.model2 === S.model) S.model2 = modelKeys().find((k) => k !== S.model) || null;
    if (!S.model2) S.cmp = false;
    const newest = inits()[0].init;
    if (newest !== prevNewest || !inits().some((i) => i.init === S.init)) S.init = newest; // 新しい初期値が増えたら切り替える
    buildModelSel(); applyCmpUi(); keepValid(); buildInitSel(); buildRegionBtns(); buildTabs(); update(); buildCredit();
    $("updated").textContent = "更新 " + updatedUtc(m.updated);
  }

  async function pollUpdate() {
    let j;
    try { j = await (await fetch(API + "status", { cache: "no-store" })).json(); } catch (e) {
      // 更新APIが無い（公開用の静的サイト）場合はボタンごと隠す
      $("btnUpdate").hidden = true; $("updateMsg").hidden = true; return;
    }
    $("lnkCustom").hidden = false;
    if (!S.local) { S.local = true; if (S.fig) buildFigSelects(); }   // ローカルサーバーと分かったら、右の図の選択肢に自由選択を加える                                   // 更新APIがある＝ローカルサーバー。自由選択ページへのリンクを出す
    const last = (j.log && j.log.length) ? j.log[j.log.length - 1] : "";
    if (j.running) {
      $("btnUpdate").disabled = true;
      $("updateMsg").textContent = "更新中…"; $("updateMsg").title = last;       // 長い進捗は行を増やすので、詳細はツールチップへ
      setTimeout(pollUpdate, 3000);
    } else {
      $("btnUpdate").disabled = false;
      if (j.rc === null || j.rc === undefined) { $("updateMsg").textContent = ""; return; }
      $("updateMsg").title = last;
      $("updateMsg").textContent = j.rc !== 0 ? "失敗（ログ確認）" : /変更はありません/.test(last) ? "変更なし" : "更新完了";  // 短く。詳細はツールチップ
      if (j.rc === 0) reloadManifest();
    }
  }

  $("btnUpdate").onclick = async () => {
    $("btnUpdate").disabled = true;
    $("updateMsg").textContent = "更新を開始しています…";
    try { await fetch(API + "update", { method: "POST" }); } catch (e) { $("updateMsg").textContent = "開始できません"; $("btnUpdate").disabled = false; return; }
    pollUpdate();
  };

  // manifest が更新されていたら、画面を開き直したとき・タブに戻ったときに取り込む
  // 自由選択のページ（別のタブ）で作図したら、右の図の選択肢と表示を更新する
  // newest=true（別のタブで新しく作ったとき）は、右の図を最新の結果に切り替える。タブに戻っただけのときは今の選択を保つ
  // 別のタブ・別のブラウザで作図した結果も、サーバーの一覧から読み直して反映する。
  // newest=true: 新しい画像が増えていたら、右の図をその最新に切り替える（タブに戻っただけのときは今の選択を保つ）
  async function refreshCustom(newest) {
    const before = customCache[0] && customBase(customCache[0]);
    await loadCustom();
    if (!S.fig) return;
    const after = customCache[0] && customBase(customCache[0]);
    if (newest && S.region2 === "custom" && after && after !== before) S.chart2 = "last";
    buildFigSelects(); update();
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && S.fig) refreshCustom(true); });

  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState !== "visible" || !S.manifest) return;
    try {
      const m = await (await fetch(OUT + "manifest.json", { cache: "no-store" })).json();
      if (m.updated !== S.manifest.updated) reloadManifest();
    } catch (e) { /* 無視 */ }
  });

  boot().then(pollUpdate); // 画面を開いた時に更新中なら進捗表示を再開
})();
