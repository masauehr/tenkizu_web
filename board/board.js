// ECMWF/GFS天気図ボード（画用紙版）
// 複数の図を並べ、マウスで移動・拡大縮小し、キーで時間を前後させる。
// 各図（パネル）は図種を持ち、図が存在する範囲（北半球・平面図・断面・エマグラム）で表示する。配置は名前を付けて登録できる。
// 画像規則: output/{model}/{init}/{region}/{chart}_{ft:03d}.png、一覧は output/manifest.json（相対パス。静的サイトでも動く）
// 各図（パネル）は「モデル + 図種 + 表示範囲」を持ち、モデル・範囲が null なら全体の指定に揃う。ECMWFとGFSの同時比較もできる。
// モデルによって最新の初期値が違っても比較できる: 全体の初期値が無いモデルの図は、予報有効時刻が一致する別の初期値の画像を出す。
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const IMG_W = 950, IMG_H = 760, TITLE_H = 22;           // 元画像のサイズと見出し帯の高さ
  const MIN_W = IMG_W * 0.25, MAX_W = IMG_W * 2;          // 0.25〜2倍
  const OUT = "../output/";                                  // 画像・manifest（このページは board/ 配下）
  const API = "../api/";                                     // 更新API（ローカルサーバーのみ）
  const LS_KEY = "tenkizuBoard.v1";   // 同じドメイン(masauehr.github.io)の nwp_chart_web の画用紙版と保存先を分ける（共有すると旧図種の配置が読み込まれて何も出ない）                            // 配置の自動保存
  const LS_PRESETS = "tenkizuBoard.presets.v1";                // 登録した配置
  const pad = (n) => String(n).padStart(2, "0");

  // 並び順（高層→地上）。整列すると左上が最も高い高度、右下が地上(surf)になる。未知の図は末尾
  const ORDER = ["polar300", "polar500", "fax57", "fax78", "qvec850", "surffax", "instab", "xs130", "xs140", "em_47401", "em_47646", "em_47778", "em_47807", "em_47936"];
  // 既定で表示しない図（断面は130E・140E、エマグラムは那覇だけを出す。他の地点は上部メニューで ON にできる）
  const DEFAULT_OFF = ["em_47401", "em_47646", "em_47778", "em_47807"];
  const isDefaultOn = (c) => !DEFAULT_OFF.includes(c);
  const REGION_DEFAULT = "japan";
  // 上部メニューとパネル見出し用の短い名前
  const SHORT = { polar300: "北半球300", polar500: "北半球500", instab: "不安定域", fax57: "FAX57", fax78: "FAX78", qvec850: "Q850",
                  surffax: "地上FAX", xs130: "断面130E", xs140: "断面140E", em_47401: "エマ稚内", em_47646: "エマ館野",
                  em_47778: "エマ潮岬", em_47807: "エマ福岡", em_47936: "エマ那覇" };
  const RSHORT = { japan: "平面図", okinawa: "沖縄周辺", asia: "東アジア", polar: "北半球", xsec: "断面", emagram: "エマグラム",
                 };
  const RORDER = ["japan", "okinawa", "asia", "polar", "xsec", "emagram"];

  // items: パネルの一覧。{id, chart, region(null=全体に揃う), model(null=全体に揃う), primary, on, x, y, w, z}
  //   primary=true: 上部メニューのON/OFFで操作する基本パネル。false: モデル違いで増やした複製（✕で削除）
  const S = { m: null, model: null, init: null, region: null, ft: 0, items: [], zTop: 1, seq: 0, active: null, timer: null };
  const els = {}; // id → {root, img, nodata, name, sel, wantUrl}

  const imgUrl = (model, init, region, chart, ft) => `${OUT}${model}/${init}/${region}/${chart}_${String(ft).padStart(3, "0")}.png`;
  const parseInit = (s) => new Date(Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8), +s.slice(8, 10)));
  const fmt = (d, off, tz) => {
    const t = new Date(d.getTime() + off * 3600e3);
    return `${t.getUTCMonth() + 1}/${pad(t.getUTCDate())}(${"日月火水木金土"[t.getUTCDay()]}) ${pad(t.getUTCHours())}時${tz}`;
  };
  const modelKeys = () => Object.keys(S.m.models || {});
  const entry = (model, init) => (((S.m.models[model] || {}).inits) || []).find((i) => i.init === init);
  const HOUR = 3600e3;
  const initMs = (init) => parseInit(init).getTime();
  const modelInits = (model) => ((S.m.models[model] || {}).inits) || [];      // 新しい順
  // 現在の初期値での {region: {chart: [ft]}}。そのモデルに現在の初期値が無ければ、そのモデルの最新の初期値の内容で代用（どの図・範囲があるかの判定用）
  const regionMap = (model) => (entry(model, S.init) || modelInits(model)[0] || {}).regions || {};
  const listAt = (model, init, region, chart) => ((((entry(model, init) || {}).regions) || {})[region] || {})[chart] || [];
  const allInits = () => [...new Set(modelKeys().flatMap((m) => S.m.models[m].inits.map((i) => i.init)))].sort().reverse();
  const rank = (arr, v) => { const i = arr.indexOf(v); return i < 0 ? 99 : i; };
  const regionsOf = () => [...new Set(modelKeys().flatMap((m) => Object.keys(regionMap(m))))]
    .sort((a, b) => rank(RORDER, a) - rank(RORDER, b));
  const chartsOf = () => [...new Set(modelKeys().flatMap((m) => Object.values(regionMap(m)).flatMap((r) => Object.keys(r))))]
    .sort((a, b) => rank(ORDER, a) - rank(ORDER, b));
  const getItem = (id) => S.items.find((i) => i.id === id);
  const effModel = (it) => it.model || S.model;
  const has = (model, region, chart) => !!((regionMap(model)[region] || {})[chart]);
  // 範囲が「全体」の図は全体の表示範囲に揃う。ただし、その図が全体の範囲に無い場合（北半球・断面・エマグラムの図）は、その図がある範囲で出す
  const effRegion = (it) => {
    if (it.region) return it.region;
    const m = effModel(it);
    return has(m, S.region, it.chart) ? S.region : (regionsOf().find((r) => has(m, r, it.chart)) || S.region);
  };
  // 予報時間 ft がその図に無いときは、直前の予報時間、無ければ最小のものを使う
  const pickFt = (list, ft) => {
    if (list.includes(ft)) return ft;
    const before = list.filter((f) => f <= ft);
    return before.length ? Math.max(...before) : Math.min(...list);
  };
  const visible = () => S.items.filter((i) => i.on);

  // 図 it を、全体の予報時間 gft（全体の初期値からのFT）のときに、どの初期値・FTの画像で出すかを決める。
  //  - そのモデルに全体の初期値があれば、その初期値を使う（刻みが合わなければ直前のFT）
  //  - 無ければ（モデルごとに最新の初期値が違う場合）、予報有効時刻が一致する別の初期値の画像を探す
  function resolve(it, gft) {
    if (gft === undefined) gft = S.ft;
    const model = effModel(it), region = effRegion(it);
    if (entry(model, S.init)) {
      const list = listAt(model, S.init, region, it.chart);
      return list.length ? { init: S.init, ft: pickFt(list, gft), exact: list.includes(gft) } : null;
    }
    const valid = initMs(S.init) + gft * HOUR;
    for (const e of modelInits(model)) {
      const ft = (valid - initMs(e.init)) / HOUR;
      if (ft >= 0 && listAt(model, e.init, region, it.chart).includes(ft)) return { init: e.init, ft, exact: true, other: true };
    }
    return null;
  }

  // 全体の予報時間の候補。全体の初期値が無いモデルの図は、その初期値の予報時刻を全体のFTへ換算して加える（負は除く）
  function panelFts(it) {
    const model = effModel(it), region = effRegion(it);
    if (entry(model, S.init)) return listAt(model, S.init, region, it.chart);
    const out = [];
    for (const e of modelInits(model)) {
      const shift = (initMs(e.init) - initMs(S.init)) / HOUR;
      for (const f of listAt(model, e.init, region, it.chart)) if (f + shift >= 0) out.push(f + shift);
    }
    return out;
  }

  // 表示中の図の予報時間の和集合（無ければ全図）
  function fts() {
    const set = new Set();
    for (const it of visible()) panelFts(it).forEach((f) => set.add(f));
    if (!set.size) for (const m of modelKeys()) for (const r of Object.values(regionMap(m))) for (const a of Object.values(r)) a.forEach((f) => set.add(f));
    return [...set].sort((a, b) => a - b);
  }

  function toast(msg) {
    const t = $("toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 2200);
  }

  const barH = () => (document.body.classList.contains("nobar") ? 0 : $("bar").offsetHeight);
  function fitBoard() {
    $("board").style.top = barH() + "px";
    $("board").style.bottom = ($("credit").offsetHeight + 8) + "px"; // 画面下の出典表示に図が隠れないように
  }

  // ---------- 保存・復元
  const save = () => localStorage.setItem(LS_KEY, JSON.stringify(
    { items: S.items, model: S.model, region: S.region, init: S.init, ft: S.ft, seq: S.seq }));
  function load() { try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch (e) { return {}; } }
  const loadPresets = () => { try { return JSON.parse(localStorage.getItem(LS_PRESETS)) || {}; } catch (e) { return {}; } };

  // ---------- パネルの作成・配置
  function newItem(chart, o) {
    return Object.assign({ id: "p" + (++S.seq), chart, region: null, model: null, primary: true, on: true,
                           x: 0, y: 0, w: 450, z: ++S.zTop }, o || {});
  }
  // 全ての図種に基本パネルが1つあるようにする（上部メニューのON/OFF用）
  function ensurePrimaries() {
    for (const c of chartsOf()) {
      if (!S.items.some((i) => i.primary && i.chart === c)) S.items.push(newItem(c, { on: false }));
    }
  }
  const sortedVisible = () => visible().sort((a, b) =>
    rank(ORDER, a.chart) - rank(ORDER, b.chart) ||
    (b.primary - a.primary) ||
    rank(RORDER, effRegion(a)) - rank(RORDER, effRegion(b)) ||
    rank(modelKeys(), effModel(a)) - rank(modelKeys(), effModel(b)) || a.id.localeCompare(b.id));

  function ensureEl(it) {
    if (els[it.id]) return els[it.id];
    const root = document.createElement("div");
    root.className = "panel";
    root.innerHTML =
      `<div class="title"><span class="left"><span class="name"></span></span>` +
      `<span class="btns"><span class="x" title="閉じる">✕</span></span></div>` +
      `<div class="body"><img alt=""><div class="nodata" hidden>この図・範囲の画像なし</div><div class="grip" title="ドラッグで大きさ変更"></div></div>`;
    $("board").appendChild(root);
    const e = { root, img: root.querySelector("img"), nodata: root.querySelector(".nodata"),
                name: root.querySelector(".name"), wantUrl: "" };
    els[it.id] = e;
    wirePanel(it.id, e);
    return e;
  }

  function applyGeom(it) {
    const e = els[it.id];
    if (!e) return;
    e.root.style.display = it.on ? "" : "none";
    e.root.style.left = it.x + "px";
    e.root.style.top = it.y + "px";
    e.root.style.width = it.w + "px";
    e.root.style.zIndex = it.z;
    e.root.classList.toggle("variant", !it.primary);
  }

  function activate(id) {
    S.active = id;
    for (const k in els) els[k].root.classList.toggle("active", k === id);
    const it = getItem(id);
    if (it) { it.z = ++S.zTop; applyGeom(it); }
  }

  function wirePanel(id, e) {
    // 移動（タイトル帯をドラッグ。範囲選択などの操作部品は除く）
    e.root.querySelector(".title").addEventListener("pointerdown", (ev) => {
      if (ev.target.closest(".btns") || ev.target.tagName === "SELECT") return;
      const it = getItem(id); if (!it) return;
      activate(id);
      const sx = ev.clientX, sy = ev.clientY, ox = it.x, oy = it.y;
      const mv = (m) => { it.x = Math.max(0, ox + m.clientX - sx); it.y = Math.max(0, oy + m.clientY - sy); applyGeom(it); };
      const up = () => { removeEventListener("pointermove", mv); removeEventListener("pointerup", up); save(); };
      addEventListener("pointermove", mv); addEventListener("pointerup", up);
      ev.preventDefault();
    });
    // 大きさ変更（右下をドラッグ。縦横比は元画像のまま）
    e.root.querySelector(".grip").addEventListener("pointerdown", (ev) => {
      const it = getItem(id); if (!it) return;
      activate(id);
      const sx = ev.clientX, sy = ev.clientY, ow = it.w;
      const mv = (m) => {
        const dw = Math.max(m.clientX - sx, (m.clientY - sy) * IMG_W / IMG_H);
        it.w = Math.min(MAX_W, Math.max(MIN_W, ow + dw));
        applyGeom(it);
      };
      const up = () => { removeEventListener("pointermove", mv); removeEventListener("pointerup", up); save(); };
      addEventListener("pointermove", mv); addEventListener("pointerup", up);
      ev.preventDefault(); ev.stopPropagation();
    });
    e.root.addEventListener("pointerdown", () => activate(id));
    // ホイールで拡大縮小（カーソル位置を基準にする）
    e.root.addEventListener("wheel", (ev) => {
      const it = getItem(id); if (!it) return;
      ev.preventDefault();
      const r = e.root.getBoundingClientRect();
      const k = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
      const nw = Math.min(MAX_W, Math.max(MIN_W, it.w * k));
      const f = nw / it.w;
      it.x = Math.max(0, it.x + (ev.clientX - r.left) * (1 - f)); // タイトル帯が画面外に出ないよう0以上
      it.y = Math.max(0, it.y + (ev.clientY - r.top) * (1 - f));
      it.w = nw;
      applyGeom(it); save();
    }, { passive: false });
    e.root.querySelector(".x").onclick = () => {
      const it = getItem(id); if (!it) return;
      if (it.primary) { it.on = false; applyGeom(it); buildToggles(); }
      else { removeItem(it); }
      save();
    };
  }

  function removeItem(it) {
    if (els[it.id]) { els[it.id].root.remove(); delete els[it.id]; }
    S.items = S.items.filter((i) => i !== it);
    if (S.active === it.id) S.active = null;
  }

  // 画像の差し替え（読み込み完了まで古い画像を残してちらつきを防ぐ）
  const initShort = (init) => { const d = parseInit(init); return `${d.getUTCMonth() + 1}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}Z`; };
  function renderItem(it) {
    const e = ensureEl(it);
    applyGeom(it);
    // 全体と別のモデルの図は、見出しにモデル名（ECMWF/GFS）を出す（選択欄は幅が狭いと隠れるため）
    const mname = effModel(it) !== S.model && S.m.models[effModel(it)] ? " " + S.m.models[effModel(it)].label : "";
    const base = (SHORT[it.chart] || it.chart) + mname;
    const r = resolve(it);
    if (!r) {
      const any = modelInits(effModel(it)).some((x) => listAt(effModel(it), x.init, effRegion(it), it.chart).length);
      e.nodata.textContent = any ? "この予報時刻の画像なし（初期値が違うため時刻が合いません）" : "この図・範囲の画像なし";
      e.nodata.hidden = false; e.name.textContent = base; e.cur = null;
      return;
    }
    e.cur = r;
    // 全体と別の初期値の図は、その初期値と予報時間を見出しに出す（予報有効時刻は合わせてある）
    e.name.textContent = r.init !== S.init ? `${base} [${initShort(r.init)} +${r.ft}h]` : (r.exact ? base : `${base} (FT${r.ft}h)`);
    e.nodata.hidden = true;
    if (!it.on) return;
    const url = imgUrl(effModel(it), r.init, effRegion(it), it.chart, r.ft);
    e.wantUrl = url;
    const im = new Image();
    im.onload = () => { if (e.wantUrl === url) e.img.src = url; };
    im.src = url;
  }

  // 出典・ライセンス・免責・改変の表示（配信元の利用条件に基づく。文言は manifest.json から読む）
  const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const linkify = (t) => esc(t).replace(/(https?:\/\/[^\s）)]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  // 画面に出ているモデルの出典を画面下に常時表示する（モデルごと。ライセンス名はリンク）
  function refreshCredit() {
    const used = [...new Set(visible().map(effModel))].filter((m) => S.m.models[m]);
    $("credit").innerHTML = used.map((m) => {
      const md = S.m.models[m];
      return esc(md.credit).replace("CC BY 4.0", `<a href="${md.license_url}" target="_blank" rel="noopener noreferrer">CC BY 4.0</a>`);
    }).join("<br>") + ' <a href="#" id="legalLink">[出典・免責の詳細]</a>';
    $("legalLink").onclick = (e) => { e.preventDefault(); toggleLegal(); };
  }

  function toggleLegal() {
    const box = $("legalPanel");
    if (box.hidden) {
      let html = "<h2>出典・ライセンス・免責・改変</h2>";
      for (const k of modelKeys()) {
        html += `<h3>${esc(S.m.models[k].label)}</h3>` + S.m.models[k].notice.map((t) => `<p>${linkify(t)}</p>`).join("");
      }
      if (S.m.disclaimer) html += `<h3>本サイトについて</h3><p>${esc(S.m.disclaimer)}</p>`;
      html += '<button id="btnLegalClose">閉じる</button>';
      box.innerHTML = html;
      $("btnLegalClose").onclick = () => { box.hidden = true; };
    }
    box.hidden = !box.hidden;
  }

  function refreshLabel() {
    const d = parseInit(S.init);
    const other = visible().filter((it) => { const e = els[it.id]; return e && e.cur && e.cur.other; }).length;
    $("dateLabel").textContent = `${fmt(d, S.ft, "UTC")} ｜ 初期値 ${pad(d.getUTCHours())}UTC ＋${S.ft}h`
      + (other ? ` ｜ ${other}図は別の初期値（予報時刻を合わせて表示）` : "");
  }

  function refresh() {
    for (const it of S.items) renderItem(it);
    refreshLabel();
    refreshCredit();
    prefetch();
    save();
  }

  // 前後のFTを先読み（↑↓での閲覧を軽くする）
  function prefetch() {
    const f = fts(), i = f.indexOf(S.ft);
    for (const ft of [f[i + 1], f[i - 1]]) {
      if (ft === undefined) continue;
      for (const it of visible()) {
        const r = resolve(it, ft);
        if (r) new Image().src = imgUrl(effModel(it), r.init, effRegion(it), it.chart, r.ft);
      }
    }
  }

  // ---------- 配置
  // 画像の縦横比（高さ/幅）。北半球図は正方形、他は 950×760。読み込み済みなら実際の画像の比率を使う
  const ASPECT = { polar300: 1, polar500: 1 };
  const aspectOf = (it) => {
    const e = els[it.id];
    if (e && e.img && e.img.naturalWidth) return e.img.naturalHeight / e.img.naturalWidth;
    return ASPECT[it.chart] || IMG_H / IMG_W;
  };
  // 敷き詰め。keepOrder=true なら今の並び（上の段から、段の中は左から）のまま、false なら高度順。
  // どちらも全て同じ幅にして、段ごとに一番高い図に高さを合わせる（正方形の北半球図が下の段に隠れない・重ならない）
  function tile(keepOrder) {
    let on = sortedVisible();
    if (!on.length) return;
    if (keepOrder === true) {
      const rows0 = [];
      for (const it of on.slice().sort((a, b) => a.y - b.y)) {
        const r = rows0.find((r) => Math.abs(r.y - it.y) < 60);
        if (r) r.items.push(it); else rows0.push({ y: it.y, items: [it] });
      }
      on = rows0.flatMap((r) => r.items.sort((a, b) => a.x - b.x));
    }
    const W = innerWidth, H = innerHeight - barH() - ($("credit").offsetHeight + 8);   // 画面下の出典表示の分も引く（fitBoard と同じ）
    const GAP = 4;
    const layout = (cols) => {
      const rows = [];
      for (let i = 0; i < on.length; i += cols) rows.push(on.slice(i, i + cols));
      const asp = rows.map((r) => Math.max(...r.map(aspectOf)));
      const w = Math.min(W / cols - GAP, (H - rows.length * (TITLE_H + GAP)) / asp.reduce((a, b) => a + b, 0));
      return { cols, rows, asp, w };
    };
    let best = null;
    for (let cols = 1; cols <= on.length; cols++) {
      const l = layout(cols);
      if (!best || l.w > best.w) best = l;
    }
    const w = Math.min(MAX_W, Math.max(MIN_W, Math.floor(best.w)));
    let y = 2;
    best.rows.forEach((r, ri) => {
      r.forEach((it, ci) => {
        it.w = w; it.x = ci * (w + GAP) + 2; it.y = y; it.z = ++S.zTop;
        applyGeom(it);
      });
      y += w * best.asp[ri] + TITLE_H + GAP;
    });
    save();
  }

  // 初期化: 既定の図（全て全体の表示範囲に揃う）を高層→地上の順で敷き詰める
  function resetLayout() {
    for (const k of Object.keys(els)) { els[k].root.remove(); delete els[k]; }
    S.items = [];
    for (const c of chartsOf()) S.items.push(newItem(c, { on: isDefaultOn(c) }));
    buildToggles(); refresh(); tile();
  }

  // 選択中の図を、他のモデルでも（同じ表示範囲で）並べて整列する＝モデル比較
  function addAllModels() {
    const it = getItem(S.active) || sortedVisible()[0];
    if (!it) { toast("先に図を選択（クリック）してください"); return; }
    const region = effRegion(it);
    const shown = new Set(visible().filter((i) => i.chart === it.chart && effRegion(i) === region).map(effModel));
    let n = 0;
    for (const m of modelKeys()) {
      if (shown.has(m) || !has(m, region, it.chart)) continue;
      S.items.push(newItem(it.chart, { region: it.region, model: m, primary: false, w: it.w }));
      n++;
    }
    if (!n) { toast("この図は、全てのモデルで既に並んでいます（またはデータがありません）"); return; }
    refresh(); tile();
    toast(`${SHORT[it.chart] || it.chart} を ${n} モデルぶん追加しました`);
  }

  // ---------- 登録した配置
  function buildPresetSel(selected) {
    const sel = $("presetSel");
    const p = loadPresets();
    sel.innerHTML = "";
    const o0 = document.createElement("option");
    o0.value = ""; o0.textContent = Object.keys(p).length ? "（選択して呼び出し）" : "（登録なし）";
    sel.appendChild(o0);
    for (const name of Object.keys(p)) {
      const o = document.createElement("option");
      o.value = name; o.textContent = name;
      sel.appendChild(o);
    }
    sel.value = selected && p[selected] ? selected : "";
  }

  function savePreset() {
    const name = $("presetName").value.trim();
    if (!name) { toast("登録名を入力してください"); return; }
    const p = loadPresets();
    const existed = !!p[name];
    p[name] = { items: JSON.parse(JSON.stringify(S.items)), model: S.model, region: S.region, savedAt: Date.now() };
    localStorage.setItem(LS_PRESETS, JSON.stringify(p));
    buildPresetSel();   // 選択を空に戻す（同じ名前を選び直しても「変更」にならず呼び出せない、を防ぐ）
    toast(existed ? `「${name}」を上書き登録しました` : `「${name}」を登録しました`);
  }

  function applyPreset(name) {
    const pr = loadPresets()[name];
    if (!pr) return;
    for (const k of Object.keys(els)) { els[k].root.remove(); delete els[k]; }
    S.items = JSON.parse(JSON.stringify(pr.items));
    S.seq = Math.max(S.seq, ...S.items.map((i) => +String(i.id).slice(1) || 0));
    S.zTop = Math.max(1, ...S.items.map((i) => i.z || 1));
    if (pr.model && modelKeys().includes(pr.model)) { S.model = pr.model; $("modelSel").value = S.model; }
    if (pr.region && regionsOf().includes(pr.region)) S.region = pr.region;
    ensurePrimaries();
    fixFt(); buildToggles(); refresh();
    $("presetName").value = name;
    $("presetSel").value = "";   // 呼び出し後は選択を空に戻す（もう一度同じ配置を選べるように）
    toast(`「${name}」を呼び出しました`);
  }

  function deletePreset() {
    const name = $("presetSel").value;
    if (!name) { toast("削除する登録を選択してください"); return; }
    const p = loadPresets();
    delete p[name];
    localStorage.setItem(LS_PRESETS, JSON.stringify(p));
    buildPresetSel();
    toast(`「${name}」を削除しました`);
  }

  // ---------- UI
  function buildToggles() {
    const box = $("chartToggles");
    box.innerHTML = "";
    for (const c of chartsOf()) {
      const prim = S.items.find((i) => i.primary && i.chart === c);
      const b = document.createElement("button");
      b.textContent = SHORT[c] || (S.m.charts[c] || c);
      b.title = S.m.charts[c] || c;
      b.className = prim && prim.on ? "on" : "";
      b.onclick = () => {
        let p = S.items.find((i) => i.primary && i.chart === c);
        if (!p) { p = newItem(c, { on: false }); S.items.push(p); }
        p.on = !p.on;
        if (p.on) { renderItem(p); activate(p.id); }
        applyGeom(p); buildToggles(); save();
      };
      box.appendChild(b);
    }
  }

  function buildSelects() {
    const sm = $("modelSel");
    sm.innerHTML = "";
    for (const k of modelKeys()) {
      const o = document.createElement("option");
      o.value = k; o.textContent = S.m.models[k].label;
      sm.appendChild(o);
    }
    sm.value = S.model;
    sm.onchange = () => { S.model = sm.value; fixFt(); rebuildRegionSelects(); refresh(); };
    buildInitOptions();
    rebuildRegionSelects();
  }

  // 初期値は全モデルの和集合。どのモデルにあるかを併記する（モデルによって最新の初期値が違うため）
  function buildInitOptions() {
    const si = $("initSel");
    si.innerHTML = "";
    for (const init of allInits()) {
      const o = document.createElement("option");
      const who = modelKeys().filter((m) => entry(m, init)).map((m) => S.m.models[m].label).join("/");
      o.value = init; o.textContent = `${fmt(parseInit(init), 0, "UTC")} 初期値 [${who}]`;
      si.appendChild(o);
    }
    si.value = S.init;
    si.onchange = () => { S.init = si.value; fixFt(); rebuildRegionSelects(); refresh(); };
  }

  function rebuildRegionSelects() {
    // 表示範囲の選択欄は無い（図ごとに範囲は1つ）。全体の範囲が無効なら既定へ戻す
    if (!regionsOf().includes(S.region)) S.region = regionsOf().includes(REGION_DEFAULT) ? REGION_DEFAULT : regionsOf()[0];
  }

  function fixFt() {
    const f = fts();
    if (f.length && !f.includes(S.ft)) S.ft = f.reduce((a, b) => (Math.abs(b - S.ft) < Math.abs(a - S.ft) ? b : a));
  }

  // ---------- 時間操作
  function stepFt(d) { // d=+1: 予報時間を進める（端で止まる）
    const f = fts(), i = f.indexOf(S.ft) + d;
    if (i >= 0 && i < f.length) { S.ft = f[i]; refresh(); }
  }
  function setFt(ft) { S.ft = ft; fixFt(); refresh(); }
  // 初期時刻を前後へ。keepValid=true なら予報有効時刻を固定（FTを逆方向に補正）
  function stepInit(d, keepValid) {
    const list = allInits().slice().reverse(); // 昇順（古→新）
    const j = list.indexOf(S.init) + d;
    if (j < 0 || j >= list.length) return;
    const dh = (parseInit(list[j]) - parseInit(S.init)) / 3600e3;
    S.init = list[j];
    if (keepValid) S.ft = Math.max(0, S.ft - dh);
    $("initSel").value = S.init;
    rebuildRegionSelects(); fixFt(); refresh();
  }
  function play() {
    if (S.timer) { stop(); return; }
    $("btnPlay").textContent = "⏸";
    S.timer = setInterval(() => {
      const f = fts(), i = f.indexOf(S.ft);
      S.ft = f[(i + 1) % f.length]; refresh();
    }, 700);
  }
  function stop() { if (S.timer) { clearInterval(S.timer); S.timer = null; } $("btnPlay").textContent = "▶"; }

  addEventListener("keydown", (e) => {
    if (["SELECT", "INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
    const k = e.key;
    if (k === "ArrowUp") { e.ctrlKey ? setFt(Math.max(...fts())) : stepFt(+1); }
    else if (k === "ArrowDown") { e.ctrlKey ? setFt(Math.min(...fts())) : stepFt(-1); }
    else if (k === "ArrowRight") stepInit(+1, e.ctrlKey);
    else if (k === "ArrowLeft") stepInit(-1, e.ctrlKey);
    else if (k === "p" || k === "P") play();
    else if (k === "s" || k === "S") stop();
    else if (k === "t" || k === "T") tile(true);
    else if (k === "i" || k === "I") resetLayout();
    else if (k === "d" || k === "D") { document.body.classList.toggle("nobar"); fitBoard(); }
    else if (k === "h" || k === "H") $("help").hidden = !$("help").hidden;
    else if (k === "b" || k === "B") { const it = getItem(S.active); if (it) { it.z = 0; applyGeom(it); } }
    else return;
    e.preventDefault();
    if (k.startsWith("Arrow")) stop(); // 矢印キーで再生を止める
  });

  $("btnTile").onclick = () => tile(true);   // 整列＝今の並びのまま重ならないように敷き詰める（初期化とは別）
  $("btnModels").onclick = addAllModels;
  $("btnPlay").onclick = play;
  $("btnReset").onclick = resetLayout;
  $("btnHelp").onclick = () => { $("help").hidden = !$("help").hidden; };
  $("btnHelpClose").onclick = () => { $("help").hidden = true; };
  $("btnLegal").onclick = toggleLegal;
  $("btnPresetSave").onclick = savePreset;
  $("btnPresetDel").onclick = deletePreset;
  $("presetSel").onchange = () => { if ($("presetSel").value) applyPreset($("presetSel").value); };
  $("presetName").addEventListener("keydown", (e) => { if (e.key === "Enter") savePreset(); });
  addEventListener("resize", fitBoard);
  new ResizeObserver(fitBoard).observe($("bar"));
  new ResizeObserver(fitBoard).observe($("credit")); // 折り返しでメニューの高さが変わっても画用紙の開始位置を合わせる

  // ---------- 起動
  async function boot() {
    try { S.m = await (await fetch(OUT + "manifest.json", { cache: "no-store" })).json(); }
    catch (e) { $("dateLabel").textContent = "manifest.json を読めません（update_latest.py を先に実行）"; return; }
    if (!modelKeys().length) { $("dateLabel").textContent = "天気図がありません（update_latest.py を実行）"; return; }
    const sv = load();
    S.model = modelKeys().includes(sv.model) ? sv.model : modelKeys()[0];
    const inits = allInits();
    S.init = inits.includes(sv.init) ? sv.init : (S.m.models[S.model].inits[0] || {}).init || inits[0];
    const regs = regionsOf();
    S.region = regs.includes(sv.region) ? sv.region : (regs.includes(REGION_DEFAULT) ? REGION_DEFAULT : regs[0]);
    S.ft = sv.ft || 0;
    S.seq = sv.seq || 0;
    const fresh = !Array.isArray(sv.items) || !sv.items.some((i) => chartsOf().includes(i.chart));
    const known = new Set(chartsOf());
    S.items = fresh ? [] : sv.items.filter((i) => known.has(i.chart));   // 存在しない図種（古い保存データ）は捨てる
    S.zTop = Math.max(1, ...S.items.map((i) => i.z || 1));
    if (fresh) for (const c of chartsOf()) S.items.push(newItem(c, { on: isDefaultOn(c) }));
    ensurePrimaries();
    fixFt();
    buildSelects(); buildToggles(); buildPresetSel();
    fitBoard();
    refresh();
    if (fresh) tile();
  }

  // ---------- 最新データの取得（サーバーの /api/update を呼ぶ）
  async function reloadManifest() {
    if (!S.m) { location.reload(); return; }
    const m = await (await fetch(OUT + "manifest.json", { cache: "no-store" })).json();
    if (!Object.keys(m.models || {}).length) return;
    const prevNewest = allInits()[0];
    S.m = m;
    if (!m.models[S.model]) S.model = modelKeys()[0];
    const newest = allInits()[0];
    if (newest !== prevNewest || !allInits().includes(S.init)) S.init = newest; // 新しい初期値が増えたら切り替える
    ensurePrimaries();
    fixFt(); buildSelects(); buildToggles(); refresh();
  }

  async function pollUpdate() {
    let j;
    try { j = await (await fetch(API + "status")).json(); } catch (e) {
      // 更新APIが無い（公開用の静的サイト）場合はボタンごと隠す
      $("btnUpdate").hidden = true; $("updateMsg").hidden = true; return;
    }
    const last = (j.log && j.log.length) ? j.log[j.log.length - 1] : "";
    if (j.running) {
      $("btnUpdate").disabled = true;
      $("updateMsg").textContent = "更新中…"; $("updateMsg").title = last;
      setTimeout(pollUpdate, 3000);
    } else {
      $("btnUpdate").disabled = false;
      if (j.rc === null || j.rc === undefined) { $("updateMsg").textContent = ""; return; }
      $("updateMsg").title = last;
      $("updateMsg").textContent = j.rc !== 0 ? "失敗（ログ確認）" : /変更はありません/.test(last) ? "変更なし" : "更新完了";
      if (j.rc === 0) reloadManifest();
    }
  }

  $("btnUpdate").onclick = async () => {
    $("btnUpdate").disabled = true;
    $("updateMsg").textContent = "更新を開始しています…";
    try { await fetch(API + "update", { method: "POST" }); } catch (e) { $("updateMsg").textContent = "開始できません"; $("btnUpdate").disabled = false; return; }
    pollUpdate();
  };

  // 自動同期（launchd）で manifest が更新されていたら、画面を開き直したとき・タブに戻ったときに取り込む
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState !== "visible" || !S.m) return;
    try {
      const m = await (await fetch(OUT + "manifest.json", { cache: "no-store" })).json();
      if (m.updated !== S.m.updated) reloadManifest();
    } catch (e) { /* 無視 */ }
  });

  boot().then(pollUpdate);
})();
