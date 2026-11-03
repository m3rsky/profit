// Wymiary na rozwinięciu blachy: mierzenie wprost na wykroju (2D), przenoszenie zapisanych pomiarów 3D,
// zapis na serwerze i ukrywanie. Cechy wykroju to cechy 3D leżące w z = 0, więc działa ten sam G.measurePair.
import * as G from './geom.js';

const SVGNS = 'http://www.w3.org/2000/svg';
const z0 = p => [p[0], p[1], 0];
const KEEP = ['k', 'p', 'c', 'r', 'n', 'a', 'b', 'dir', 'len', 'anchor', 'area', 'o', 'convex', 'free', 'arc', 'part'];
const plain = f => { const o = {}; KEEP.forEach(k => { if (f[k] !== undefined) o[k] = f[k]; }); return o; };

// ── czyste funkcje (testowane osobno) ────────────────────────────────────────

// cechy do wskazywania na wykroju: otwory, łuki, krawędzie proste konturu, linie gięć, narożniki
export function flatFeatures(res, part) {
  const out = { circles: [], lines: [], corners: [] };
  res.holes.forEach(h => { if (h.circle) out.circles.push({ k: 'circle', c: z0(h.circle.c), r: h.circle.r, n: [0, 0, 1], part }); });
  const loops = [res.outer, ...res.holes.filter(h => !h.circle).map(h => h.pts)];
  loops.forEach(pts => {
    const pf = G.polyFeatures(pts.map(z0), [0, 0, 1]);
    pf.lines.forEach(l => out.lines.push({ k: 'line', a: l.a, b: l.b, dir: l.dir, len: l.len, part }));
    pf.arcs.forEach(c => out.circles.push({ k: 'circle', c: c.c, r: c.r, n: [0, 0, 1], arc: true, part }));
    pf.corners.forEach(p => out.corners.push({ k: 'pt', p, part }));
  });
  res.bends.forEach(b => {
    const a = z0(b.a), c = z0(b.b), L = Math.hypot(c[0] - a[0], c[1] - a[1]);
    if (L > 0.5) out.lines.push({ k: 'line', a, b: c, dir: [(c[0] - a[0]) / L, (c[1] - a[1]) / L, 0], len: L, bend: true, part });
  });
  return out;
}

// cecha 3D -> cecha na wykroju (albo null, gdy leży poza rozwiniętą powierzchnią lub jest płaszczyzną/walcem)
export function mapFeature(res, f) {
  const to2 = p => { const q = res.map3to2(p); return q ? [q[0], q[1], 0] : null; };
  if (f.k === 'pt') { const p = to2(f.p); return p && { k: 'pt', p, part: f.part }; }
  if (f.k === 'circle') { const c = to2(f.c); return c && { k: 'circle', c, r: f.r, n: [0, 0, 1], arc: f.arc, part: f.part }; }
  if (f.k === 'cyl') {                       // ściana otworu: oś prostopadła do blachy daje środek otworu
    const hit = res.axisHit(f.o, f.dir);
    const c = hit && to2(hit);
    return c && { k: 'circle', c, r: f.r, n: [0, 0, 1], part: f.part };
  }
  if (f.k === 'line') {
    const a = to2(f.a), b = to2(f.b);
    if (!a || !b) return null;
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    return L > 1e-6 ? { k: 'line', a, b, dir: [(b[0] - a[0]) / L, (b[1] - a[1]) / L, 0], len: L, part: f.part } : null;
  }
  return null;
}

// zapisany pomiar 3D przeniesiony na wykrój: wartość liczona od nowa w płaszczyźnie wykroju
export function mapRecord(res, d) {
  const why = f => (f.k === 'plane' ? 'płaszczyzna nie ma wymiaru na wykroju'
    : f.k === 'cyl' ? 'walec (gięcie) nie ma wymiaru na wykroju' : 'cecha leży poza rozwiniętą powierzchnią');
  const A = mapFeature(res, d.A), B = mapFeature(res, d.B);
  if (!A) return { ok: false, reason: why(d.A) };
  if (!B) return { ok: false, reason: why(d.B) };
  const m = G.measurePair(A, B);
  if (!m.segs.length) return { ok: false, reason: 'dla tej pary cech nie ma wymiaru na wykroju' };
  return { ok: true, A, B, rows: m.rows, segs: m.segs };
}

// ── interfejs ────────────────────────────────────────────────────────────────

export function initFlatDims(ctx) {
  const host = document.getElementById('flat-dims');
  let st = null, token = 0, seq = 0;

  const mk = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const el = (name, attrs) => { const e = document.createElementNS(SVGNS, name); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

  function detach() { token++; st = null; if (host) host.textContent = ''; }

  async function attach({ svg, res, part, body }) {
    const my = ++token;
    st = {
      svg, res, part, body, feats: flatFeatures(res, part), items: [], skipped: [], active: false, pending: null, hint: '',
      W: res.width, H: res.height, down: null,
    };
    st.overlay = el('g', {});
    svg.append(st.overlay);
    bindPicking(svg);
    collect3d();
    if (ctx.api && ctx.api.list) {
      try {
        const list = await ctx.api.list();
        if (my !== token) return;
        list.forEach(rec => addRecord(rec));      // tu trafiają tylko zapisane pomiary z wykroju
      } catch (e) { st.hint = 'Nie udało się wczytać zapisanych pomiarów.'; }
    }
    if (my !== token) return;
    render();
  }

  // pomiary 3D z bieżącej sesji (zapisane i niezapisane): wartość liczona od nowa na wykroju
  function collect3d() {
    st.items = st.items.filter(i => i.src !== '3d');
    st.skipped = [];
    const list = ctx.measure ? ctx.measure.results() : [];
    list.forEach(r => {
      if (r.space === 'flat') return;                       // pomiar z wykroju, pokazany w 3D, nie wraca na wykrój
      const name = '#' + r.id + (r.label ? ' ' + r.label : '');
      if (r.A.part !== st.part || r.B.part !== st.part) { st.skipped.push({ name, reason: 'dotyczy innej bryły niż ta rozwinięta' }); return; }
      const m = mapRecord(st.res, { A: r.A, B: r.B });
      if (!m.ok) { st.skipped.push({ name, reason: m.reason }); return; }
      st.items.push({ id: ++seq, src: '3d', saved: r.saved, label: r.label || '', visible: true, drawable: true, A: m.A, B: m.B, rows: m.rows, segs: m.segs, rows3d: r.rows, note: '' });
    });
  }

  function addRecord(rec) {
    const d = rec.data;
    if (!d || d.space !== 'flat' || !d.A || !d.B || d.A.part !== st.part || d.B.part !== st.part) return;
    const same = Math.abs((d.K === undefined ? st.res.K : d.K) - st.res.K) < 1e-6;
    st.items.push({
      id: ++seq, dbId: rec.id, saved: true, src: 'flat', label: rec.label || '', visible: true, author: rec.author, canEdit: rec.can_edit,
      A: d.A, B: d.B, rows: d.rows, segs: d.segs, drawable: same,
      note: same ? '' : 'Zapisany dla K=' + Number(d.K).toFixed(2) + ', ustaw to K, aby go zobaczyć.',
    });
  }

  // ── wskazywanie cech na rysunku ───────────────────────────────────────────
  function bindPicking(svg) {
    svg.addEventListener('pointerdown', e => { st && (st.down = { x: e.clientX, y: e.clientY }); });
    svg.addEventListener('pointerup', e => {
      if (!st || !st.active || !st.down) return;
      const moved = Math.hypot(e.clientX - st.down.x, e.clientY - st.down.y);
      st.down = null;
      if (moved > 5) return;
      pick(e.clientX, e.clientY);
    });
  }
  function toFlat(cx, cy) {
    const m = st.svg.getScreenCTM();
    const pt = st.svg.createSVGPoint(); pt.x = cx; pt.y = cy;
    const p = pt.matrixTransform(m.inverse());
    return { x: p.x, y: -p.y, mmPerPx: 1 / m.a };
  }
  const distSeg = (px, py, a, b) => {
    const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
    let t = l2 ? ((px - a[0]) * dx + (py - a[1]) * dy) / l2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy));
  };
  function featureAtFlat(cx, cy) {
    const { x, y, mmPerPx } = toFlat(cx, cy);
    const cands = [];
    st.feats.corners.forEach(f => cands.push({ d: Math.hypot(x - f.p[0], y - f.p[1]), lim: 10 * mmPerPx, f }));
    st.feats.circles.forEach(f => { const h = Math.hypot(x - f.c[0], y - f.c[1]); cands.push({ d: h <= f.r && !f.arc ? 0.0001 : Math.abs(h - f.r), lim: 11 * mmPerPx, f }); });
    st.feats.lines.forEach(f => cands.push({ d: distSeg(x, y, f.a, f.b), lim: 8 * mmPerPx, f }));
    const near = cands.filter(c => c.d <= c.lim).sort((a, b) => a.d - b.d)[0];
    return near ? near.f : null;
  }
  function pick(cx, cy) {
    const f = featureAtFlat(cx, cy);
    if (!f) { st.hint = 'Kliknij otwór, krawędź wykroju, linię gięcia lub narożnik.'; render(); return; }
    if (!st.pending) {
      st.pending = f;
      st.hint = 'Wybrano: ' + G.describe(f) + '. Kliknij drugą cechę.';
    } else {
      const A = st.pending; st.pending = null;
      const m = G.measurePair(A, f);
      if (!m.segs.length) { st.hint = 'Dla tej pary cech nie ma wymiaru.'; render(); return; }
      st.items.push({ id: ++seq, src: 'flat', saved: false, A, B: f, rows: m.rows, segs: m.segs, visible: true, drawable: true, label: '', note: '' });
      st.hint = 'Pomiar #' + seq + ': ' + m.rows[0][0] + ' ' + m.rows[0][1] + '. Zapisz go lub mierz dalej.';
    }
    render();
  }

  // ── rysowanie na wykroju ──────────────────────────────────────────────────
  function drawFeature(g, f, color, sw) {
    const fs = Math.max(st.W, st.H);
    if (f.k === 'pt') g.append(el('circle', { cx: f.p[0], cy: -f.p[1], r: fs / 150, fill: color }));
    else if (f.k === 'circle') { g.append(el('circle', { cx: f.c[0], cy: -f.c[1], r: f.r, fill: 'none', stroke: color, 'stroke-width': sw })); g.append(el('circle', { cx: f.c[0], cy: -f.c[1], r: fs / 220, fill: color })); }
    else if (f.k === 'line') g.append(el('line', { x1: f.a[0], y1: -f.a[1], x2: f.b[0], y2: -f.b[1], stroke: color, 'stroke-width': sw * 1.6 }));
  }
  function redraw() {
    const g = st.overlay;
    g.textContent = '';
    const fs = Math.max(st.W, st.H), sw = fs / 600, tsz = fs / 50, placed = [];
    if (st.pending) drawFeature(g, st.pending, '#1a6e8a', sw);
    st.items.filter(i => i.visible && i.drawable).forEach(i => {
      drawFeature(g, i.A, '#1a6e8a', sw); drawFeature(g, i.B, '#1a6e8a', sw);
      i.segs.forEach(([p, q]) => g.append(el('line', { x1: p[0], y1: -p[1], x2: q[0], y2: -q[1], stroke: '#c8102e', 'stroke-width': sw * 1.2 })));
      const s0 = i.segs[0];
      if (s0 && i.rows[0]) {
        const mx = (s0[0][0] + s0[1][0]) / 2, my = -(s0[0][1] + s0[1][1]) / 2, txt = i.rows[0][1];
        const w = txt.length * tsz * 0.62 + tsz * 0.6;
        let top = my - tsz * 1.2;
        // etykiety na jednej linii nie mogą się nakładać: przesuwamy w górę
        while (placed.some(q => Math.abs(q.top - top) < tsz * 1.3 && Math.abs(q.x - mx) < (q.w + w) / 2)) top -= tsz * 1.35;
        placed.push({ x: mx, top, w });
        g.append(el('rect', { x: mx - w / 2, y: top, width: w, height: tsz * 1.25, rx: tsz / 5, fill: '#c8102e' }));
        const t = el('text', { x: mx, y: top + tsz * 0.92, 'font-size': tsz, fill: '#fff', 'text-anchor': 'middle', 'font-family': 'system-ui,sans-serif', 'font-weight': '600' });
        t.textContent = txt;
        g.append(t);
      }
    });
  }

  // ── lista i przyciski ─────────────────────────────────────────────────────
  async function saveItem(i) {
    if (!ctx.api || i.saved || i.src !== 'flat') return;
    try {
      const rec = await ctx.api.save({ label: i.label, element: st.body, data: { space: 'flat', K: st.res.K, A: plain(i.A), B: plain(i.B), rows: i.rows, segs: i.segs } });
      i.dbId = rec.id; i.saved = true; i.author = rec.author; i.canEdit = rec.can_edit;
      if (ctx.measure) ctx.measure.addFlatRecord(rec);            // zapisany pomiar z wykroju pojawia się też na modelu 3D
      st.hint = 'Zapisano pomiar #' + i.id + '.';
    } catch (e) { st.hint = 'Nie udało się zapisać pomiaru (' + e.message + ').'; }
    render();
  }
  async function removeItem(i) {
    if (i.saved && ctx.api) {
      try { await ctx.api.remove(i.dbId); } catch (e) { st.hint = 'Nie udało się usunąć pomiaru (' + e.message + ').'; render(); return; }
    }
    if (i.saved && ctx.measure) ctx.measure.dropByDbId(i.dbId);
    st.items.splice(st.items.indexOf(i), 1);
    render();
  }

  function render() {
    if (!st || !host) return;
    redraw();
    host.textContent = '';
    host.append(mk('h4', 'Pomiary na wykroju'));
    const bar = mk('div', undefined, 'fd-bar');
    const tog = mk('button', st.active ? 'Wyłącz pomiar' : 'Mierz na wykroju');
    tog.className = st.active ? 'on' : '';
    tog.onclick = () => { st.active = !st.active; st.pending = null; st.hint = st.active ? 'Kliknij otwór, krawędź wykroju, linię gięcia lub narożnik.' : ''; st.svg.style.cursor = st.active ? 'crosshair' : ''; render(); };
    const show = mk('button', 'Pokaż wszystkie'); show.onclick = () => { st.items.forEach(i => { i.visible = true; }); render(); };
    const hide = mk('button', 'Ukryj wszystkie'); hide.onclick = () => { st.items.forEach(i => { i.visible = false; }); render(); };
    const all = mk('button', 'Zapisz wszystkie'); all.disabled = !ctx.api; all.onclick = async () => { for (const i of st.items.filter(x => x.src === 'flat' && !x.saved)) await saveItem(i); };
    const drafts = mk('button', 'Usuń niezapisane'); drafts.onclick = () => { st.items = st.items.filter(i => i.saved); st.pending = null; render(); };
    bar.append(tog, show, hide, all, drafts);
    host.append(bar);
    if (st.hint) host.append(mk('div', st.hint, 'fd-hint'));

    const group = (title, items) => {
      if (!items.length) return;
      const gh = mk('label', undefined, 'fd-ghead');
      const cb = mk('input'); cb.type = 'checkbox'; cb.checked = items.every(i => i.visible); cb.indeterminate = !cb.checked && items.some(i => i.visible);
      cb.onchange = () => { items.forEach(i => { i.visible = cb.checked; }); render(); };
      gh.append(cb, mk('span', title + ' (' + items.length + ')'));
      host.append(gh);
      items.forEach(i => host.append(rowFor(i)));
    };
    if (st.skipped && st.skipped.length) {
      const sk = mk('div', undefined, 'fd-skipped');
      sk.append(mk('b', 'Nie przeniesiono ' + st.skipped.length + ' pomiarów z modelu 3D:'));
      st.skipped.forEach(x => sk.append(mk('div', x.name + ': ' + x.reason)));
      host.append(sk);
    }
    group('Na wykroju', st.items.filter(i => i.src === 'flat'));
    group('Z modelu 3D', st.items.filter(i => i.src === '3d'));
  }

  const nameOf = i => i.label || ((i.src === '3d' ? 'Z modelu 3D ' : 'Pomiar ') + '#' + i.id + (i.rows[0] ? ' (' + i.rows[0][0].toLowerCase() + ')' : ''));

  // wiersz zwinięty: oko, nazwa, wartość; po kliknięciu rozwija dane i zmianę nazwy
  function rowFor(i) {
    const row = mk('div', undefined, 'fd-row' + (i.saved ? ' saved' : ' draft') + (i.open ? ' open' : ''));
    row.dataset.src = i.src;
    const top = mk('div', undefined, 'fd-top');
    const eye = mk('input'); eye.type = 'checkbox'; eye.checked = i.visible; eye.disabled = !i.drawable; eye.title = 'Pokaż lub ukryj ten wymiar';
    eye.onchange = () => { i.visible = eye.checked; render(); };
    const title = mk('button', undefined, 'fd-title'); title.type = 'button';
    title.setAttribute('aria-expanded', String(!!i.open));
    title.title = i.open ? 'Zwiń' : 'Rozwiń dane pomiaru';
    title.append(mk('span', i.open ? '▾' : '▸', 'fd-chev'), mk('span', nameOf(i), 'fd-nm'), mk('span', i.rows[0] ? i.rows[0][1] : '', 'fd-val'));
    title.onclick = () => { i.open = !i.open; render(); };
    top.append(eye, title);
    if (i.src === 'flat' && !i.saved) { const sv = mk('button', 'Zapisz', 'fd-save'); sv.type = 'button'; sv.disabled = !ctx.api; sv.onclick = () => saveItem(i); top.append(sv); }
    row.append(top);
    if (!i.open) return row;

    const body = mk('div', undefined, 'fd-body');
    body.append(mk('div', G.describe(i.A) + '  ↔  ' + G.describe(i.B), 'fd-desc'));
    i.rows.forEach(([k, v]) => { const l = mk('div'); l.append(k + ': ', mk('b', v)); body.append(l); });
    if (i.src === '3d' && i.rows3d && i.rows3d[0]) body.append(mk('div', 'W modelu 3D: ' + i.rows3d[0][1], 'fd-note'));
    if (i.note) body.append(mk('div', i.note, 'fd-note'));
    if (i.src === 'flat') {
      const nameRow = mk('div', undefined, 'fd-namerow');
      const name = mk('input', undefined, 'fd-name'); name.type = 'text'; name.maxLength = 128; name.placeholder = 'Nazwa pomiaru'; name.value = i.label || '';
      name.setAttribute('aria-label', 'Nazwa pomiaru');
      const commit = () => {
        const v = name.value.trim();
        if (v === (i.label || '')) return;
        i.label = v;
        if (i.saved && ctx.api) ctx.api.rename(i.dbId, v).then(() => { st.hint = 'Zmieniono nazwę pomiaru #' + i.id + '.'; render(); }).catch(() => { st.hint = 'Nie udało się zapisać nazwy.'; render(); });
        else render();
      };
      name.onchange = commit;
      name.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); commit(); } };
      const ren = mk('button', 'Zmień nazwę', 'fd-rename'); ren.type = 'button'; ren.onclick = commit;
      nameRow.append(name, ren);
      body.append(nameRow);
    } else body.append(mk('div', 'Nazwę tego pomiaru zmienisz w widoku 3D.', 'fd-note'));
    const bar = mk('div', undefined, 'fd-actions');
    bar.append(mk('span', i.src === '3d' ? (i.saved ? 'Pomiar z modelu 3D (zapisany)' : 'Pomiar z modelu 3D (niezapisany)') : (i.saved ? 'Zapisany' + (i.author ? ' (' + i.author + ')' : '') : 'Niezapisany'), 'fd-state'));
    if (i.src === 'flat' && (!i.saved || i.canEdit !== false)) { const del = mk('button', 'Usuń'); del.type = 'button'; del.onclick = () => removeItem(i); bar.append(del); }
    body.append(bar);
    row.append(body);
    return row;
  }

  return { attach, detach, items: () => (st ? st.items : []), feats: () => (st ? st.feats : null) };
}
