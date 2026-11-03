// Pomiary w podglądzie 3D: przyciąganie do otworów (środek), krawędzi prostych, płaszczyzn i narożników.
// Geometria z geom.js (cechy ścian B-rep), tu interakcja, rysowanie, zapis na serwerze i widoczność.
import * as THREE from 'three';
import * as G from './geom.js';

const V3 = p => new THREE.Vector3(p[0], p[1], p[2]);
const KEEP = ['k', 'p', 'c', 'r', 'n', 'a', 'b', 'dir', 'len', 'anchor', 'area', 'o', 'convex', 'free', 'arc', 'part'];

// cecha do zapisu: same dane liczbowe (bez obiektów siatki)
function plain(f) {
  const o = {};
  KEEP.forEach(k => { if (f[k] !== undefined) o[k] = f[k]; });
  return o;
}

export function initMeasure(ctx) {
  const { scene, camera, renderer } = ctx;
  const root = new THREE.Group();
  scene.add(root);
  const pendingGroup = new THREE.Group();
  root.add(pendingGroup);
  const listEl = document.getElementById('meas-list');
  const hintEl = document.getElementById('meas-hint');
  const labelsEl = document.getElementById('meas-labels');

  const red = new THREE.LineBasicMaterial({ color: 0xc8102e, depthTest: false, transparent: true });
  const blue = new THREE.LineBasicMaterial({ color: 0x1a6e8a, depthTest: false, transparent: true });
  const ptBlue = new THREE.PointsMaterial({ color: 0x1a6e8a, size: 9, sizeAttenuation: false, depthTest: false });
  const planeMat = new THREE.MeshBasicMaterial({ color: 0x1a6e8a, transparent: true, opacity: 0.35, depthTest: false, side: THREE.DoubleSide });

  let active = false, pending = null, seq = 0;
  const results = [];

  const say = t => { if (hintEl) hintEl.textContent = t; };
  const HINT = 'Kliknij otwór (przy krawędzi), krawędź, narożnik albo płaszczyznę. Druga cecha daje odległość lub kąt.';

  function toScreen(p) {
    const r = renderer.domElement.getBoundingClientRect();
    const v = V3(p).project(camera);
    return [(v.x * 0.5 + 0.5) * r.width + r.left, (-v.y * 0.5 + 0.5) * r.height + r.top, v.z];
  }
  function distSeg(px, py, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
    let t = l2 ? ((px - a[0]) * dx + (py - a[1]) * dy) / l2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy));
  }
  function ringDist(px, py, c, r, n) {
    const [u, v] = G.planeBasis(n);
    let best = 1e9, prev = null;
    for (let i = 0; i <= 48; i++) {
      const a = (i / 48) * Math.PI * 2;
      const p = [c[0] + r * (Math.cos(a) * u[0] + Math.sin(a) * v[0]), c[1] + r * (Math.cos(a) * u[1] + Math.sin(a) * v[1]), c[2] + r * (Math.cos(a) * u[2] + Math.sin(a) * v[2])];
      const s = toScreen(p);
      if (prev) best = Math.min(best, distSeg(px, py, prev, s));
      prev = s;
    }
    return best;
  }

  // cecha pod kursorem: najbliższa cecha ściany w promieniu kilku pikseli, inaczej płaszczyzna/walec/punkt swobodny
  function featureAt(cx, cy) {
    const hit = ctx.castRay(cx, cy);
    if (!hit) return null;
    const withPart = f => { f.part = hit.partIndex; return f; };
    const gm = ctx.getGeom(hit.partIndex);
    const face = G.faceOfTriangle(gm, hit.triangle);
    if (!face) return withPart({ k: 'pt', p: hit.point, free: true });
    const ft = G.faceFeatures(gm, face);
    const cands = [];
    ft.corners.forEach(p => { const s = toScreen(p); cands.push({ d: Math.hypot(cx - s[0], cy - s[1]), lim: 10, f: { k: 'pt', p } }); });
    ft.circles.forEach(c => cands.push({ d: ringDist(cx, cy, c.c, c.r, c.n), lim: 11, f: { k: 'circle', c: c.c, r: c.r, n: c.n } }));
    ft.arcs.forEach(c => cands.push({ d: ringDist(cx, cy, c.c, c.r, c.n), lim: 9, f: { k: 'circle', c: c.c, r: c.r, n: c.n, arc: true } }));
    ft.lines.forEach(l => { const a = toScreen(l.a), b = toScreen(l.b); cands.push({ d: distSeg(cx, cy, a, b), lim: 8, f: { k: 'line', a: l.a, b: l.b, dir: l.dir, len: l.len } }); });
    const near = cands.filter(c => c.d <= c.lim).sort((x, y) => x.d - y.d)[0];
    if (near) return withPart(near.f);
    if (ft.plane) return withPart({ k: 'plane', n: ft.plane.n, anchor: ft.plane.anchor, area: ft.plane.area, face, gm, point: hit.point });
    if (ft.cyl) return withPart({ k: 'cyl', o: ft.cyl.o, dir: ft.cyl.dir, r: ft.cyl.r, convex: ft.cyl.convex, a: ft.cyl.p0, b: ft.cyl.p1 });
    return withPart({ k: 'pt', p: hit.point, free: true });
  }

  // ── rysowanie cech i wyników ───────────────────────────────────────────────
  function featureObjects(f, mat, ptMat) {
    const out = [];
    const line = pts => { const g = new THREE.BufferGeometry().setFromPoints(pts.map(V3)); const l = new THREE.Line(g, mat); l.renderOrder = 999; return l; };
    const point = p => { const g = new THREE.BufferGeometry().setFromPoints([V3(p)]); const o = new THREE.Points(g, ptMat); o.renderOrder = 1000; return o; };
    if (f.k === 'pt') out.push(point(f.p));
    else if (f.k === 'circle') {
      const [u, v] = G.planeBasis(f.n), pts = [];
      for (let i = 0; i <= 64; i++) { const a = (i / 64) * Math.PI * 2; pts.push([f.c[0] + f.r * (Math.cos(a) * u[0] + Math.sin(a) * v[0]), f.c[1] + f.r * (Math.cos(a) * u[1] + Math.sin(a) * v[1]), f.c[2] + f.r * (Math.cos(a) * u[2] + Math.sin(a) * v[2])]); }
      out.push(line(pts), point(f.c));
    } else if (f.k === 'line') out.push(line([f.a, f.b]));
    else if (f.k === 'cyl') out.push(line([f.a, f.b]));
    else if (f.k === 'plane') {
      if (f.face && f.gm) {
        const pos = [], m = f.gm;
        for (let t = f.face.first; t <= f.face.last; t++) for (let k = 0; k < 3; k++) { const i = m.idx[t * 3 + k]; pos.push(m.pos[i * 3], m.pos[i * 3 + 1], m.pos[i * 3 + 2]); }
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
        const mesh = new THREE.Mesh(g, planeMat); mesh.renderOrder = 998; out.push(mesh);
      } else if (f.anchor) out.push(point(f.anchor));    // zapisany pomiar: bez siatki ściany
    }
    return out;
  }
  function clearGroup(g) { while (g.children.length) { const o = g.children.pop(); o.geometry && o.geometry.dispose(); } }

  // ── lista pomiarów: grupy wg elementu, widoczność, zapis ───────────────────
  const UNASSIGNED = 'Bez elementu';
  function mk(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }

  function renderList() {
    document.dispatchEvent(new CustomEvent('v3d-measures', { detail: { count: results.length } }));
    if (!listEl) return;
    listEl.textContent = '';
    const groups = new Map();
    results.forEach(r => { const k = r.element || UNASSIGNED; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(r); });
    [...groups.keys()].sort().forEach(name => {
      const rs = groups.get(name);
      const box = mk('div', undefined, 'mgroup');
      const head = mk('label', undefined, 'mghead');
      const cb = mk('input'); cb.type = 'checkbox'; cb.title = 'Pokaż lub ukryj pomiary tego elementu';
      cb.checked = rs.every(r => r.visible); cb.indeterminate = !cb.checked && rs.some(r => r.visible);
      cb.onchange = () => { rs.forEach(r => setVisible(r, cb.checked)); renderList(); };
      head.append(cb, mk('span', name + ' (' + rs.length + ')'));
      box.append(head);
      rs.forEach(r => box.append(rowFor(r)));
      listEl.append(box);
    });
  }

  // nazwa na liście: nadana przez użytkownika albo numer i rodzaj pomiaru
  const displayName = r => r.label || ('Pomiar #' + r.id + (r.rows[0] ? ' (' + r.rows[0][0].toLowerCase() + ')' : ''));

  // wiersz zwinięty: oko, nazwa, wartość; po kliknięciu rozwija dane i zmianę nazwy
  function rowFor(r) {
    const row = mk('div', undefined, 'mrow' + (r.saved ? ' saved' : ' draft') + (r.open ? ' open' : ''));
    const top = mk('div', undefined, 'mtop');
    const eye = mk('input'); eye.type = 'checkbox'; eye.checked = r.visible; eye.title = 'Pokaż lub ukryj ten pomiar';
    eye.onchange = () => { setVisible(r, eye.checked); renderList(); };
    const title = mk('button', undefined, 'mtitle'); title.type = 'button';
    title.setAttribute('aria-expanded', String(!!r.open));
    title.title = r.open ? 'Zwiń' : 'Rozwiń dane pomiaru';
    title.append(mk('span', r.open ? '▾' : '▸', 'mchev'), mk('span', displayName(r), 'mnm'), mk('span', r.rows[0] ? r.rows[0][1] : '', 'mval'));
    title.onclick = () => { r.open = !r.open; renderList(); };
    top.append(eye, title);
    if (!r.saved) {
      const sv = mk('button', 'Zapisz', 'msave'); sv.type = 'button'; sv.disabled = !ctx.api; sv.title = 'Zapisz ten pomiar na serwerze';
      sv.onclick = () => save(r);
      top.append(sv);
    }
    row.append(top);
    if (!r.open) return row;

    const body = mk('div', undefined, 'mbody');
    body.append(mk('div', (r.space === 'flat' ? '[z wykroju] ' : '') + G.describe(r.A) + '  ↔  ' + G.describe(r.B), 'mhead'));
    r.rows.forEach(([k, v]) => { const l = mk('div'); l.append(k + ': ', mk('b', v)); body.append(l); });
    if (r.note) body.append(mk('div', r.note, 'mnote'));
    const nameRow = mk('div', undefined, 'mnamerow');
    const name = mk('input', undefined, 'mname'); name.type = 'text'; name.maxLength = 128; name.placeholder = 'Nazwa pomiaru'; name.value = r.label || '';
    name.setAttribute('aria-label', 'Nazwa pomiaru');
    const commit = () => {
      const v = name.value.trim();
      if (v === (r.label || '')) return;
      r.label = v;
      if (r.saved && ctx.api) ctx.api.rename(r.dbId, v).then(() => { say('Zmieniono nazwę pomiaru #' + r.id + '.'); renderList(); }).catch(() => say('Nie udało się zapisać nazwy.'));
      else renderList();
    };
    name.onchange = commit;
    name.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); commit(); } };
    const ren = mk('button', 'Zmień nazwę', 'mrename'); ren.type = 'button'; ren.onclick = commit;
    nameRow.append(name, ren);
    body.append(nameRow);
    const bar = mk('div', undefined, 'mbar');
    bar.append(mk('span', r.saved ? 'Zapisany' + (r.author ? ' (' + r.author + ')' : '') : 'Niezapisany', 'mstate'));
    if (!r.saved || r.canEdit !== false) { const del = mk('button', 'Usuń'); del.type = 'button'; del.onclick = () => remove(r); bar.append(del); }
    body.append(bar);
    row.append(body);
    return row;
  }

  function setVisible(r, v) { r.visible = v; r.group.visible = v; }

  async function save(r) {
    if (!ctx.api || r.saved) return;
    try {
      const rec = await ctx.api.save({ label: r.label || '', element: r.element || '', data: { A: plain(r.A), B: plain(r.B), rows: r.rows, segs: r.segs } });
      r.dbId = rec.id; r.saved = true; r.author = rec.author; r.canEdit = rec.can_edit;
      say('Zapisano pomiar #' + r.id + '.');
    } catch (e) { say('Nie udało się zapisać pomiaru (' + e.message + ').'); }
    renderList();
  }
  async function saveAll() {
    for (const r of results.filter(x => !x.saved)) await save(r);
  }
  async function remove(r) {
    if (r.saved && ctx.api) {
      try { await ctx.api.remove(r.dbId); } catch (e) { say('Nie udało się usunąć pomiaru (' + e.message + ').'); return; }
    }
    const i = results.indexOf(r);
    if (i < 0) return;
    r.group.parent && r.group.parent.remove(r.group);
    clearGroup(r.group);
    r.labelEl && r.labelEl.remove();
    results.splice(i, 1);
    renderList();
  }
  function clear(onlyDrafts) {
    [...results].filter(r => !onlyDrafts || !r.saved).forEach(r => {
      r.group.parent && r.group.parent.remove(r.group);
      clearGroup(r.group);
      r.labelEl && r.labelEl.remove();
      results.splice(results.indexOf(r), 1);
    });
    pending = null; clearGroup(pendingGroup);
    renderList();
    say(HINT);
  }
  function setAllVisible(v) { results.forEach(r => setVisible(r, v)); renderList(); }

  function addResult(A, B, rows, segs, meta = {}) {
    const r = {
      id: ++seq, A, B, rows, segs, group: new THREE.Group(), labelEl: null, visible: true,
      saved: !!meta.saved, dbId: meta.dbId || null, label: meta.label || '', element: meta.element || '',
      author: meta.author || '', canEdit: meta.canEdit, space: meta.space || '3d', note: meta.note || '',
    };
    featureObjects(A, blue, ptBlue).forEach(o => r.group.add(o));
    featureObjects(B, blue, ptBlue).forEach(o => r.group.add(o));
    segs.forEach(([p, q]) => {
      const g = new THREE.BufferGeometry().setFromPoints([V3(p), V3(q)]);
      const l = new THREE.Line(g, red); l.renderOrder = 1001; r.group.add(l);
    });
    root.add(r.group);
    if (labelsEl) {
      const el = mk('div', rows[0] ? rows[0][1] : '', 'mlabel');
      labelsEl.append(el);
      r.labelEl = el;
    }
    results.push(r);
    return r;
  }
  function elementsOf(A, B) {
    const names = [...new Set([A.part, B.part].filter(x => x !== undefined).map(i => (ctx.elementOf ? ctx.elementOf(i) : '')).filter(Boolean))];
    return names.join(' / ');
  }

  function complete(A, B) {
    const m = G.measurePair(A, B);
    const r = addResult(A, B, m.rows, m.segs, { element: elementsOf(A, B) });
    renderList();
    say('Pomiar #' + r.id + ': ' + (m.rows[0] ? m.rows[0][0] + ' ' + m.rows[0][1] : '') + '. Zapisz go lub kliknij kolejną cechę.');
  }

  // pomiar z wykroju -> cechy 3D na modelu (odwrócenie rozwinięcia); null, gdy nie da się odwzorować
  function fromFlat(rec) {
    const d = rec.data, part = d.A && d.A.part;
    const K = d.K === undefined ? 0.5 : d.K;
    const fm = ctx.flatMapper && part !== undefined ? ctx.flatMapper(part, K) : null;
    if (!fm || !fm.ok || !fm.map2to3) return null;
    const m3 = q => fm.map2to3([q[0], q[1]]);
    const feat = f => {
      if (f.bend) return null;                                // linia gięcia nie leży na płaskiej ścianie
      if (f.k === 'pt') { const r = m3(f.p); return r && { k: 'pt', p: r.p, part }; }
      if (f.k === 'circle') { const r = m3(f.c); return r && { k: 'circle', c: r.p, r: f.r, n: r.n, arc: f.arc, part }; }
      if (f.k === 'line') {
        const a = m3(f.a), b = m3(f.b);
        if (!a || !b) return null;
        const v = [b.p[0] - a.p[0], b.p[1] - a.p[1], b.p[2] - a.p[2]], L = Math.hypot(...v);
        return L > 1e-9 ? { k: 'line', a: a.p, b: b.p, dir: [v[0] / L, v[1] / L, v[2] / L], len: f.len, part } : null;
      }
      return null;
    };
    const A = feat(d.A), B = feat(d.B);
    if (!A || !B) return null;
    const segs = (d.segs || []).map(([p, q]) => { const a = m3(p), b = m3(q); return a && b ? [a.p, b.p] : null; });
    if (segs.some(x => !x)) return null;
    const s0 = segs[0], straight = s0 ? Math.hypot(s0[1][0] - s0[0][0], s0[1][1] - s0[0][1], s0[1][2] - s0[0][2]) : 0;
    return { A, B, segs, note: 'Wymiar z wykroju (K=' + K.toFixed(2) + ')' + (s0 ? ', w 3D w linii prostej ' + straight.toFixed(2) + ' mm' : '') };
  }

  // jeden zapisany pomiar (z widoku 3D albo z wykroju)
  function addRecord(rec) {
    const d = rec.data;
    if (!d || !d.A || !d.B) return false;
    if (d.space === 'flat') {
      const m = fromFlat(rec);
      if (!m) return false;
      addResult(m.A, m.B, d.rows || [], m.segs, { saved: true, dbId: rec.id, label: rec.label, element: rec.element, author: rec.author, canEdit: rec.can_edit, space: 'flat', note: m.note });
    } else {
      addResult(d.A, d.B, d.rows || [], d.segs || [], { saved: true, dbId: rec.id, label: rec.label, element: rec.element, author: rec.author, canEdit: rec.can_edit });
    }
    return true;
  }
  function addFlatRecord(rec) {
    const ok = addRecord(rec);
    renderList();
    if (!ok) say('Pomiar z wykroju zapisany, ale nie da się go pokazać w 3D (np. dotyczy linii gięcia).');
  }
  function dropByDbId(id) {
    const r = results.find(x => x.dbId === id && x.space === 'flat');
    if (!r) return;
    r.group.parent && r.group.parent.remove(r.group);
    clearGroup(r.group);
    r.labelEl && r.labelEl.remove();
    results.splice(results.indexOf(r), 1);
    renderList();
  }

  // zapisane pomiary z serwera
  function load(records) {
    let skipped = 0;
    records.forEach(rec => { if (!addRecord(rec)) skipped++; });
    renderList();
    if (skipped) say('Nie pokazano ' + skipped + ' zapisanych pomiarów w widoku 3D (np. z wykroju do linii gięcia).');
  }

  function pick(cx, cy) {
    if (ctx.isExploded()) { say('Wyłącz rozstrzelenie, aby mierzyć (pomiary dotyczą położenia w modelu).'); return true; }
    const f = featureAt(cx, cy);
    if (!f) { say(HINT); return true; }
    if (!pending) {
      pending = f;
      clearGroup(pendingGroup);
      featureObjects(f, blue, ptBlue).forEach(o => pendingGroup.add(o));
      say('Wybrano: ' + (f.free ? 'punkt swobodny (bez przyciągania, mniej dokładny)' : G.describe(f)) + '. Kliknij drugą cechę.');
    } else {
      const A = pending;
      pending = null; clearGroup(pendingGroup);
      complete(A, f);
    }
    return true;
  }

  function update() {
    const exploded = ctx.isExploded();
    root.visible = !exploded;          // pomiary dotyczą położenia w modelu, przy rozstrzeleniu byłyby w złych miejscach
    if (!labelsEl) return;
    const host = labelsEl.getBoundingClientRect();
    results.forEach(r => {
      const el = r.labelEl;
      if (!el) return;
      const s0 = r.segs[0];
      if (!s0 || !r.visible || exploded) { el.style.display = 'none'; return; }
      const mid = [(s0[0][0] + s0[1][0]) / 2, (s0[0][1] + s0[1][1]) / 2, (s0[0][2] + s0[1][2]) / 2];
      const sc = toScreen(mid);
      el.style.display = sc[2] < 1 ? '' : 'none';
      el.style.left = (sc[0] - host.left) + 'px';
      el.style.top = (sc[1] - host.top) + 'px';
    });
  }

  function setActive(on) {
    active = on;
    if (!on) { pending = null; clearGroup(pendingGroup); }
    say(on ? HINT : 'Pomiar wyłączony.');
  }

  return { pick, update, clear, setActive, isActive: () => active, results: () => results, featureAt, compose: complete, save, saveAll, load, addFlatRecord, dropByDbId, setAllVisible, setVisible };
}
