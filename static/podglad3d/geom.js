// Geometria siatki STEP: ściany B-rep to zakresy trójkątów (brep_faces z occt-import-js).
// Czyste funkcje na tablicach liczb, bez THREE, żeby dało się je testować osobno.
// Wierzchołki sąsiednich ścian są zduplikowane w siatce, więc sąsiedztwo liczymy po pozycjach.

const QN = 1000;                       // kwantyzacja pozycji: 0.001 mm
const RAD = Math.PI / 180;

// ── wektory ──────────────────────────────────────────────────────────────────
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = a => Math.hypot(a[0], a[1], a[2]);
const unit = a => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const lerp = (a, b, t) => add(a, mul(sub(b, a), t));
export const V = { sub, add, mul, dot, cross, len, unit };

// ── siatka ───────────────────────────────────────────────────────────────────
export function prepare(pos, idx, faces) {
  const m = {
    pos, idx, vk: new Map(),
    faces: faces.map((f, id) => ({ id, first: f.first, last: f.last })),
  };
  m.faces.sort((a, b) => a.first - b.first);
  return m;
}
const P = (m, i) => [m.pos[i * 3], m.pos[i * 3 + 1], m.pos[i * 3 + 2]];
function vkey(m, i) {
  let k = m.vk.get(i);
  if (k === undefined) {
    k = Math.round(m.pos[i * 3] * QN) + ',' + Math.round(m.pos[i * 3 + 1] * QN) + ',' + Math.round(m.pos[i * 3 + 2] * QN);
    m.vk.set(i, k);
  }
  return k;
}
export function faceOfTriangle(m, t) {
  let lo = 0, hi = m.faces.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1, f = m.faces[mid];
    if (t < f.first) hi = mid - 1; else if (t > f.last) lo = mid + 1; else return f;
  }
  return null;
}
function triangles(f) { const a = []; for (let t = f.first; t <= f.last; t++) a.push(t); return a; }

// ── macierz symetryczna 3x3: wartości i wektory własne (Jacobi) ──────────────
function eigenSym(A) {
  const a = [A[0].slice(), A[1].slice(), A[2].slice()];
  const v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  for (let it = 0; it < 30; it++) {
    let p = 0, q = 1, mx = Math.abs(a[0][1]);
    if (Math.abs(a[0][2]) > mx) { p = 0; q = 2; mx = Math.abs(a[0][2]); }
    if (Math.abs(a[1][2]) > mx) { p = 1; q = 2; mx = Math.abs(a[1][2]); }
    if (mx < 1e-14) break;
    const th = (a[q][q] - a[p][p]) / (2 * a[p][q]);
    const t = Math.sign(th || 1) / (Math.abs(th) + Math.sqrt(th * th + 1));
    const c = 1 / Math.sqrt(t * t + 1), s = t * c;
    for (let k = 0; k < 3; k++) { const akp = a[k][p], akq = a[k][q]; a[k][p] = c * akp - s * akq; a[k][q] = s * akp + c * akq; }
    for (let k = 0; k < 3; k++) { const apk = a[p][k], aqk = a[q][k]; a[p][k] = c * apk - s * aqk; a[q][k] = s * apk + c * aqk; }
    for (let k = 0; k < 3; k++) { const vkp = v[k][p], vkq = v[k][q]; v[k][p] = c * vkp - s * vkq; v[k][q] = s * vkp + c * vkq; }
  }
  const vals = [a[0][0], a[1][1], a[2][2]];
  const vecs = [0, 1, 2].map(j => [v[0][j], v[1][j], v[2][j]]);
  return { vals, vecs };
}

// ── okrąg metodą Kåsa (współrzędne 2D) ───────────────────────────────────────
export function fitCircle2(pts) {
  const n = pts.length;
  let mx = 0, my = 0;
  pts.forEach(p => { mx += p[0]; my += p[1]; });
  mx /= n; my /= n;
  let suu = 0, suv = 0, svv = 0, suuu = 0, svvv = 0, suvv = 0, svuu = 0;
  pts.forEach(p => {
    const u = p[0] - mx, w = p[1] - my;
    suu += u * u; suv += u * w; svv += w * w; suuu += u * u * u; svvv += w * w * w; suvv += u * w * w; svuu += w * u * u;
  });
  const det = suu * svv - suv * suv;
  if (Math.abs(det) < 1e-12) return null;
  const uc = (svv * (suuu + suvv) / 2 - suv * (svvv + svuu) / 2) / det;
  const vc = (suu * (svvv + svuu) / 2 - suv * (suuu + suvv) / 2) / det;
  const cx = mx + uc, cy = my + vc;
  const r = Math.sqrt(uc * uc + vc * vc + (suu + svv) / n);
  let err = 0;
  pts.forEach(p => { const d = Math.hypot(p[0] - cx, p[1] - cy) - r; err += d * d; });
  return { c: [cx, cy], r, rms: Math.sqrt(err / n) };
}

// baza w płaszczyźnie o normalnej n
export function planeBasis(n) {
  const h = Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  const u = unit(cross(h, n)), v = cross(n, u);
  return [u, v];
}

// ── analiza ściany: płaszczyzna / walec / inna ───────────────────────────────
export function analyzeFace(m, f) {
  if (f.a) return f.a;
  const ts = triangles(f);
  let area = 0, sn = [0, 0, 0], sc = [0, 0, 0];
  const normals = [];
  const C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const t of ts) {
    const a = P(m, m.idx[t * 3]), b = P(m, m.idx[t * 3 + 1]), c = P(m, m.idx[t * 3 + 2]);
    const cr = cross(sub(b, a), sub(c, a)), ar = len(cr) / 2;
    if (ar < 1e-12) continue;
    const n = mul(cr, 1 / (2 * ar));
    normals.push([n, ar]);
    area += ar;
    sn = add(sn, mul(n, ar));
    sc = add(sc, mul(add(add(a, b), c), ar / 3));
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) C[i][j] += ar * n[i] * n[j];
  }
  const res = { type: 'other', area, tris: ts.length };
  if (!area) return (f.a = res);
  const centroid = mul(sc, 1 / area);
  res.centroid = centroid;
  const nm = unit(sn);
  // płaszczyzna: wszystkie normalne zgodne
  const minDot = Math.min(...normals.map(([n]) => dot(n, nm)));
  if (minDot > Math.cos(0.3 * RAD)) {
    res.type = 'plane'; res.n = nm; res.d = dot(nm, centroid);
    return (f.a = res);
  }
  // walec: normalne prostopadłe do osi (najmniejsza wartość własna ~ 0)
  const { vals, vecs } = eigenSym(C);
  const order = [0, 1, 2].sort((i, j) => vals[i] - vals[j]);
  const tr = vals[0] + vals[1] + vals[2];
  res.ratio = vals[order[0]] / tr;
  if (vals[order[0]] / tr < 2e-4) {
    const axis = unit(vecs[order[0]]);
    const [u, w] = planeBasis(axis);
    const vs = new Map();
    for (const t of ts) for (let k = 0; k < 3; k++) { const i = m.idx[t * 3 + k]; vs.set(vkey(m, i), i); }
    const pts3 = [...vs.values()].map(i => P(m, i));
    const pts2 = pts3.map(p => [dot(p, u), dot(p, w)]);
    const fit = fitCircle2(pts2);
    res.fitRms = fit ? fit.rms : null; res.fitR = fit ? fit.r : null;
    if (fit && fit.rms < 0.02 && fit.r > 0.05) {
      const s0 = dot(pts3[0], axis);
      const o = add(add(mul(u, fit.c[0]), mul(w, fit.c[1])), mul(axis, s0));   // punkt na osi
      // położenie wzdłuż osi i zasięg kątowy
      let smin = 1e18, smax = -1e18;
      const angs = pts3.map(p => {
        const s = dot(sub(p, o), axis); smin = Math.min(smin, s); smax = Math.max(smax, s);
        const rho = sub(sub(p, o), mul(axis, s));
        return Math.atan2(dot(rho, w), dot(rho, u));
      }).sort((a, b) => a - b);
      let gap = 0;
      for (let i = 0; i < angs.length; i++) {
        const nx = i + 1 < angs.length ? angs[i + 1] : angs[0] + 2 * Math.PI;
        gap = Math.max(gap, nx - angs[i]);
      }
      // wypukły (normalne od osi) czy wklęsły (do osi)
      let conv = 0;
      for (const t of ts) {
        const a = P(m, m.idx[t * 3]), b = P(m, m.idx[t * 3 + 1]), c = P(m, m.idx[t * 3 + 2]);
        const cr = cross(sub(b, a), sub(c, a));
        const cen = mul(add(add(a, b), c), 1 / 3);
        const rho = sub(cen, add(o, mul(axis, dot(sub(cen, o), axis))));
        conv += dot(cr, rho);
      }
      Object.assign(res, { type: 'cylinder', axis, o, r: fit.r, span: 2 * Math.PI - gap, convex: conv > 0, s0: smin, s1: smax, rms: fit.rms });
      return (f.a = res);
    }
  }
  return (f.a = res);
}

// ── krawędzie brzegowe zbioru trójkątów ──────────────────────────────────────
// Zwraca mapę krawędzi (klucz nieskierowany) z liczbą użyć; brzegowe mają count 1.
export function edgeMap(m, tris) {
  const em = new Map();
  for (const t of tris) {
    const vi = [m.idx[t * 3], m.idx[t * 3 + 1], m.idx[t * 3 + 2]];
    for (let k = 0; k < 3; k++) {
      const ia = vi[k], ib = vi[(k + 1) % 3], io = vi[(k + 2) % 3];
      const ka = vkey(m, ia), kb = vkey(m, ib);
      if (ka === kb) continue;
      const key = ka < kb ? ka + '|' + kb : kb + '|' + ka;
      const e = em.get(key);
      if (e) e.count++;
      else em.set(key, { ka, kb, ia, ib, io, t, count: 1 });
    }
  }
  return em;
}

// pętle brzegowe jako listy indeksów wierzchołków (kolejność zgodna z orientacją trójkątów)
export function loopsOf(m, em) {
  const out = new Map();
  for (const e of em.values()) {
    if (e.count !== 1) continue;
    if (!out.has(e.ka)) out.set(e.ka, []);
    out.get(e.ka).push(e);
  }
  const used = new Set(), loops = [];
  for (const e0 of [...em.values()].filter(e => e.count === 1)) {
    if (used.has(e0)) continue;
    const loop = [];
    let e = e0;
    while (e && !used.has(e)) {
      used.add(e);
      loop.push(e.ia);
      const next = (out.get(e.kb) || []).find(x => !used.has(x));
      e = next;
    }
    if (loop.length >= 3) loops.push(loop);
  }
  return loops;
}

// ── cechy płaskiej ściany: otwory (okręgi), linie, łuki, narożniki ───────────
export function polyFeatures(pts, n) {
  const N = pts.length;
  const turn = [], dirs = [];
  for (let i = 0; i < N; i++) dirs.push(unit(sub(pts[(i + 1) % N], pts[i])));
  for (let i = 0; i < N; i++) {
    const d0 = dirs[(i - 1 + N) % N], d1 = dirs[i];
    turn.push(Math.acos(Math.max(-1, Math.min(1, dot(d0, d1)))) / RAD);
  }
  const brk = [];
  for (let i = 0; i < N; i++) if (turn[i] > 2.5) brk.push(i);
  const lines = [], arcs = [], corners = [];
  if (brk.length < 2) return { lines, arcs, corners };
  // łuki: kolejne punkty załamania o małym skręcie (< 50 stopni) po obu stronach
  const isArcV = i => turn[i] > 2.5 && turn[i] < 50;
  const inArc = new Array(N).fill(false);
  let i = 0;
  while (i < brk.length) {
    let j = i;
    while (j + 1 < brk.length && isArcV(brk[j]) && isArcV(brk[j + 1]) && brk[j + 1] === (brk[j] + 1) % N) j++;
    if (j - i >= 2) {
      const idxs = [];
      for (let k = i; k <= j; k++) idxs.push(brk[k]);
      // dołącz punkty końcowe łuku (sąsiedzi pierwszego i ostatniego)
      const first = (idxs[0] - 1 + N) % N, last = (idxs[idxs.length - 1] + 1) % N;
      const arcPts = [pts[first], ...idxs.map(k => pts[k]), pts[last]];
      const [u, v] = planeBasis(n);
      const f2 = fitCircle2(arcPts.map(p => [dot(p, u), dot(p, v)]));
      if (f2 && f2.rms < 0.02) {
        const c3 = add(add(mul(u, f2.c[0]), mul(v, f2.c[1])), mul(n, dot(arcPts[0], n)));
        arcs.push({ c: c3, r: f2.r, n, a: arcPts[0], b: arcPts[arcPts.length - 1] });
        idxs.forEach(k => { inArc[k] = true; });
      }
    }
    i = j + 1;
  }
  // linie między kolejnymi załamaniami, z pominięciem łuków
  const sharp = brk.filter(k => !inArc[k]);
  corners.push(...sharp.map(k => pts[k]));
  for (let a = 0; a < brk.length; a++) {
    const p = brk[a], q = brk[(a + 1) % brk.length];
    const arcSeg = inArc[p] || inArc[q];
    if (arcSeg && (inArc[p] && inArc[q])) continue;
    const A = pts[p], B = pts[q], L = len(sub(B, A));
    if (L > 0.3 && !(inArc[p] || inArc[q])) lines.push({ a: A, b: B, dir: unit(sub(B, A)), len: L });
  }
  return { lines, arcs, corners };
}

export function faceFeatures(m, f) {
  if (f.feat) return f.feat;
  const an = analyzeFace(m, f);
  const feat = { type: an.type, circles: [], arcs: [], lines: [], corners: [], plane: null, cyl: null };
  if (an.type === 'plane') {
    feat.plane = { n: an.n, d: an.d, anchor: an.centroid, area: an.area, face: f.id };
    const em = edgeMap(m, triangles(f));
    const [u, v] = planeBasis(an.n);
    for (const loop of loopsOf(m, em)) {
      const pts = loop.map(i => P(m, i));
      if (pts.length >= 8) {
        const fit = fitCircle2(pts.map(p => [dot(p, u), dot(p, v)]));
        if (fit && fit.rms < 0.01 && fit.r > 0.2) {
          const c3 = add(add(mul(u, fit.c[0]), mul(v, fit.c[1])), mul(an.n, an.d));
          feat.circles.push({ c: c3, r: fit.r, n: an.n, face: f.id });
          continue;
        }
      }
      const pf = polyFeatures(pts, an.n);
      feat.lines.push(...pf.lines); feat.arcs.push(...pf.arcs); feat.corners.push(...pf.corners);
    }
  } else if (an.type === 'cylinder') {
    feat.cyl = { o: an.o, dir: an.axis, r: an.r, convex: an.convex, span: an.span, face: f.id,
                 p0: add(an.o, mul(an.axis, an.s0)), p1: add(an.o, mul(an.axis, an.s1)) };
  }
  return (f.feat = feat);
}

// ── pomiary między cechami ───────────────────────────────────────────────────
// cecha: {k:'pt', p} | {k:'circle', c, r, n} | {k:'line', a, dir} | {k:'plane', n, anchor} | {k:'cyl', o, dir, r}
const asPoint = x => (x.k === 'pt' ? x.p : x.k === 'circle' ? x.c : null);
const asLine = x => (x.k === 'line' ? { a: x.a, dir: x.dir } : x.k === 'cyl' ? { a: x.o, dir: x.dir } : null);
const fmt = v => v.toFixed(2);

function closestOnLine(p, L) {
  const t = dot(sub(p, L.a), L.dir);
  return add(L.a, mul(L.dir, t));
}

export function measurePair(A, B) {
  const pa = asPoint(A), pb = asPoint(B);
  const la = asLine(A), lb = asLine(B);
  const out = { segs: [], rows: [] };
  const seg = (p, q) => out.segs.push([p, q]);
  const ang = (d1, d2) => { const a = Math.acos(Math.min(1, Math.abs(dot(d1, d2)))) / RAD; return a; };

  if (pa && pb) {
    const d = sub(pb, pa);
    seg(pa, pb);
    out.rows.push(['Odległość', fmt(len(d)) + ' mm'], ['ΔX / ΔY / ΔZ', [d[0], d[1], d[2]].map(x => fmt(Math.abs(x))).join(' / ') + ' mm']);
    if (A.k === 'circle' && B.k === 'circle') out.rows.push(['Średnice', 'Ø' + fmt(2 * A.r) + ' i Ø' + fmt(2 * B.r)]);
    return out;
  }
  if ((pa && lb) || (la && pb)) {
    const p = pa || pb, L = lb || la;
    const foot = closestOnLine(p, L);
    seg(p, foot);
    out.rows.push(['Odległość', fmt(len(sub(p, foot))) + ' mm']);
    if ((pa ? A : B).k === 'circle') out.rows.push(['Średnica otworu', 'Ø' + fmt(2 * (pa ? A : B).r)]);
    return out;
  }
  if ((pa && B.k === 'plane') || (A.k === 'plane' && pb)) {
    const p = pa || pb, pl = A.k === 'plane' ? A : B;
    const dist = dot(sub(p, pl.anchor), pl.n);
    seg(p, sub(p, mul(pl.n, dist)));
    out.rows.push(['Odległość od płaszczyzny', fmt(Math.abs(dist)) + ' mm']);
    if ((pa ? A : B).k === 'circle') out.rows.push(['Średnica otworu', 'Ø' + fmt(2 * (pa ? A : B).r)]);
    return out;
  }
  if (la && lb) {
    const c = cross(la.dir, lb.dir), sinA = len(c);
    if (sinA < Math.sin(0.1 * RAD)) {                 // równoległe
      const foot = closestOnLine(lb.a, la);
      seg(lb.a, foot);
      out.rows.push(['Odległość (równoległe)', fmt(len(sub(lb.a, foot))) + ' mm']);
    } else {                                           // skośne lub przecinające się
      const n = mul(c, 1 / sinA), w = sub(lb.a, la.a);
      const dd = dot(w, n);
      const t1 = dot(cross(w, lb.dir), n) / sinA, t2 = dot(cross(w, la.dir), n) / sinA;
      seg(add(la.a, mul(la.dir, t1)), add(lb.a, mul(lb.dir, t2)));
      out.rows.push(['Najmniejsza odległość', fmt(Math.abs(dd)) + ' mm'], ['Kąt', fmt(ang(la.dir, lb.dir)) + '°']);
    }
    return out;
  }
  if ((la && B.k === 'plane') || (A.k === 'plane' && lb)) {
    const L = la || lb, pl = A.k === 'plane' ? A : B;
    const sinA = Math.abs(dot(L.dir, pl.n));
    if (sinA < Math.sin(0.1 * RAD)) {
      const dist = dot(sub(L.a, pl.anchor), pl.n);
      seg(L.a, sub(L.a, mul(pl.n, dist)));
      out.rows.push(['Odległość (równoległe)', fmt(Math.abs(dist)) + ' mm']);
    } else {
      out.rows.push(['Kąt względem płaszczyzny', fmt(Math.asin(sinA) / RAD) + '°']);
    }
    return out;
  }
  if (A.k === 'plane' && B.k === 'plane') {
    const cs = Math.abs(dot(A.n, B.n));
    if (cs > Math.cos(0.1 * RAD)) {
      const dist = dot(sub(B.anchor, A.anchor), A.n);
      seg(B.anchor, sub(B.anchor, mul(A.n, dist)));
      out.rows.push(['Odległość (równoległe)', fmt(Math.abs(dist)) + ' mm']);
    } else {
      const a = Math.acos(cs) / RAD;
      out.rows.push(['Kąt między płaszczyznami', fmt(a) + '°'], ['Dopełnienie', fmt(180 - a) + '°']);
    }
    return out;
  }
  out.rows.push(['Pomiar', 'dla tej pary cech niedostępny']);
  return out;
}

// opis pojedynczej cechy do wyświetlenia
export function describe(x) {
  switch (x.k) {
    case 'circle': return 'Otwór Ø' + fmt(2 * x.r) + ' mm (środek)';
    case 'cyl': return (x.convex ? 'Walec wypukły R' : 'Walec wklęsły R') + fmt(x.r) + ' mm (Ø' + fmt(2 * x.r) + ')';
    case 'line': return (x.bend ? 'Linia gięcia, długość ' : 'Krawędź prosta, długość ') + fmt(x.len || 0) + ' mm';
    case 'plane': return 'Płaszczyzna, pole ' + fmt(x.area || 0) + ' mm²';
    default: return 'Punkt';
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// Rozwinięcie blachy: jedna bryła = jeden element z blachy (płaskie ściany + walce gięć).
// Grubość t z par równoległych ścian, powierzchnia A (jedna strona blachy) rozwijana po
// ścianach: płaszczyzna jest izometrią, walec gięcia to pasek o długości kąt * (r_wewn + K*t).
// Zaokrąglone naroża i inne ściany niestandardowe są pomijane (szczelina w narożu).
// ═════════════════════════════════════════════════════════════════════════════
function planeMap(E, a3, t3, P2, d2, w2) {
  return x => { const r = sub(x, E); const s = dot(r, a3), u = dot(r, t3); return [P2[0] + d2[0] * s + w2[0] * u, P2[1] + d2[1] * s + w2[1] * u]; };
}

function perpUnit(v, a) { return unit(sub(v, mul(a, dot(v, a)))); }

export function unfoldSheet(m, opts = {}) {
  const K = opts.K === undefined ? 0.5 : opts.K;
  const res = { ok: false, errors: [], warnings: [], K };
  const F = m.faces, an = F.map(f => analyzeFace(m, f));
  const totalArea = an.reduce((s, a) => s + a.area, 0);

  // 1. grubość blachy: najczęstsza odległość między przeciwnymi równoległymi płaszczyznami
  const planes = F.map((f, i) => i).filter(i => an[i].type === 'plane');
  const tw = new Map();
  for (let x = 0; x < planes.length; x++) for (let y = x + 1; y < planes.length; y++) {
    const A = an[planes[x]], B = an[planes[y]];
    if (dot(A.n, B.n) > -0.9999) continue;
    const t = A.d + B.d;
    if (t < 0.05 || t > 15) continue;
    const key = Math.round(t * 100);
    tw.set(key, (tw.get(key) || 0) + Math.min(A.area, B.area));
  }
  let best = null;
  for (const [k, w] of tw) if (!best || w > best.w) best = { k, w };
  if (!best) { res.errors.push('Nie znaleziono pary równoległych ścian o grubości blachy. To nie wygląda na element z blachy.'); return res; }
  const t = best.k / 100;
  res.t = t;

  // 2. powierzchnie blachy: płaszczyzny z partnerem w odległości t i walce gięć (R i R - t)
  const skin = new Set();
  for (const i of planes) for (const j of planes) {
    if (i === j) continue;
    if (dot(an[i].n, an[j].n) < -0.9999 && Math.abs(an[i].d + an[j].d - t) < 0.02) { skin.add(i); skin.add(j); }
  }
  const cyls = F.map((f, i) => i).filter(i => an[i].type === 'cylinder');
  for (const i of cyls) for (const j of cyls) {
    if (i === j) continue;
    const A = an[i], B = an[j];
    if (Math.abs(dot(A.axis, B.axis)) < 0.9999 || A.convex === B.convex) continue;
    if (Math.abs(Math.abs(A.r - B.r) - t) > 0.03) continue;
    const w = sub(B.o, A.o);
    if (len(cross(w, A.axis)) > 0.03) continue;            // osie się nie pokrywają
    skin.add(i); skin.add(j);
  }
  const others = F.map((f, i) => i).filter(i => an[i].type === 'other');
  const othersArea = others.reduce((s, i) => s + an[i].area, 0);
  if (others.length) {
    if (othersArea > 0.1 * totalArea) { res.errors.push('Duża część powierzchni to ściany inne niż płaszczyzna i walec (wytłoczenia, stożki, zaokrąglenia). Rozwinięcie niemożliwe.'); return res; }
    res.warnings.push('Pominięto ' + others.length + ' małych ścian niestandardowych (np. naroża gięć), w rozwinięciu to szczeliny w narożach.');
  }

  // 3. sąsiedztwo ścian powierzchni (wspólne krawędzie po pozycjach)
  const edgeIdx = new Map();                 // klucz krawędzi -> [{fi, e}]
  for (const i of skin) {
    const em = edgeMap(m, triangles(F[i]));
    for (const [key, e] of em) if (e.count === 1) {
      if (!edgeIdx.has(key)) edgeIdx.set(key, []);
      edgeIdx.get(key).push({ fi: i, e });
    }
  }
  const adj = new Map();                     // "i>j" -> lista {ei, ej}
  const nb = new Map();
  for (const [, lst] of edgeIdx) {
    if (lst.length !== 2 || lst[0].fi === lst[1].fi) continue;
    const [p, q] = lst;
    for (const [a, b] of [[p, q], [q, p]]) {
      const k = a.fi + '>' + b.fi;
      if (!adj.has(k)) adj.set(k, []);
      adj.get(k).push({ ei: a.e, ej: b.e });
      if (!nb.has(a.fi)) nb.set(a.fi, new Set());
      nb.get(a.fi).add(b.fi);
    }
  }
  // składowa spójna z największą płaszczyzną = jedna strona blachy
  const baseFace = [...skin].filter(i => an[i].type === 'plane').sort((a, b) => an[b].area - an[a].area)[0];
  if (baseFace === undefined) { res.errors.push('Brak płaskiej ściany do rozwinięcia.'); return res; }
  const compA = new Set([baseFace]);
  const stack = [baseFace];
  while (stack.length) { const x = stack.pop(); for (const y of nb.get(x) || []) if (!compA.has(y)) { compA.add(y); stack.push(y); } }

  // 4. rozwijanie od ściany bazowej
  const maps = new Map();                    // i -> mapa 3D -> 2D
  const [bu, bv] = planeBasis(an[baseFace].n);
  const bo = an[baseFace].centroid;
  maps.set(baseFace, { kind: 'plane', E: bo, a3: bu, t3: bv, P2: [0, 0], d2: [1, 0], w2: [0, 1], f: planeMap(bo, bu, bv, [0, 0], [1, 0], [0, 1]) });
  const linP = (M, vec) => [M.d2[0] * dot(vec, M.a3) + M.w2[0] * dot(vec, M.t3), M.d2[1] * dot(vec, M.a3) + M.w2[1] * dot(vec, M.t3)];
  const queue = [baseFace];
  const bends = [], conflicts = [];
  const unsupported = new Set();
  const elen = e => len(sub(P(m, e.ib), P(m, e.ia)));
  const bestEdge = lst => lst.slice().sort((a, b) => elen(b.ei) - elen(a.ei))[0];

  while (queue.length) {
    const fi = queue.shift(), Fm = maps.get(fi);
    for (const nj of nb.get(fi) || []) {
      if (!compA.has(nj)) continue;
      const pair = bestEdge(adj.get(fi + '>' + nj));
      const A = an[fi], B = an[nj];
      if (maps.has(nj)) {                         // cykl: sprawdź zgodność wspólnej krawędzi
        const Mj = maps.get(nj), p = P(m, pair.ei.ia), q = P(m, pair.ei.ib);
        const e = Math.max(Math.hypot(...sub(Fm.f(p), Mj.f(p))), Math.hypot(...sub(Fm.f(q), Mj.f(q))));
        if (e > 0.15) conflicts.push(fi + '-' + nj);
        continue;
      }
      if (A.type === 'plane' && B.type === 'cylinder') {
        const a = B.axis, E = P(m, pair.ei.ia);
        if (Math.abs(dot(unit(sub(P(m, pair.ei.ib), E)), a)) < 0.99) { unsupported.add(nj); continue; }
        const tin = perpUnit(sub(P(m, pair.ei.io), E), a), tout = mul(tin, -1);
        const d2 = linP(Fm, a), w2 = linP(Fm, tout), P2 = Fm.f(E);
        const rho0 = unit(sub(sub(E, B.o), mul(a, dot(sub(E, B.o), a))));
        const sigma = dot(cross(a, rho0), tout) >= 0 ? 1 : -1;
        const rIn = B.convex ? B.r - t : B.r, rn = rIn + K * t;
        const f = x => {
          const s = dot(sub(x, E), a);
          const rho = sub(sub(x, B.o), mul(a, dot(sub(x, B.o), a)));
          const th = sigma * Math.atan2(dot(a, cross(rho0, rho)), dot(rho0, rho));
          return [P2[0] + d2[0] * s + w2[0] * th * rn, P2[1] + d2[1] * s + w2[1] * th * rn];
        };
        maps.set(nj, { kind: 'cyl', f, d2, w2, P2, rn, rIn, sigma, a, E, rho0, o: B.o, face: nj });
        queue.push(nj);
      } else if (A.type === 'cylinder' && B.type === 'plane') {
        const a = A.axis, E1 = P(m, pair.ei.ia);
        if (Math.abs(dot(unit(sub(P(m, pair.ei.ib), E1)), a)) < 0.99) { unsupported.add(nj); continue; }
        const tin = perpUnit(sub(P(m, pair.ej.io), E1), a);
        const P2 = Fm.f(E1);
        maps.set(nj, { kind: 'plane', E: E1, a3: a, t3: tin, P2, d2: Fm.d2, w2: Fm.w2, f: planeMap(E1, a, tin, P2, Fm.d2, Fm.w2) });
        queue.push(nj);
      } else {
        unsupported.add(nj);
      }
    }
  }
  if (unsupported.size) res.warnings.push('Pominięto ' + unsupported.size + ' ścian o nieobsługiwanym połączeniu (ostra krawędź bez promienia lub gięcia styczne).');
  if (conflicts.length) res.warnings.push('Wykryto zamknięte narożniki (' + conflicts.length + '), wymiary w ich pobliżu mogą być przybliżone.');

  // 5. kontur: krawędzie brzegowe sumy rozwiniętych ścian
  const placed = [...maps.keys()];
  const tris = [];
  const p2 = new Map();                       // indeks wierzchołka -> [X,Y]
  for (const i of placed) for (let tt = F[i].first; tt <= F[i].last; tt++) {
    tris.push(tt);
    for (let k = 0; k < 3; k++) { const vi = m.idx[tt * 3 + k]; if (!p2.has(vi)) p2.set(vi, maps.get(i).f(P(m, vi))); }
  }
  const em = edgeMap(m, tris);
  const loops = loopsOf(m, em).map(l => l.map(vi => p2.get(vi)));
  if (!loops.length) { res.errors.push('Nie udało się wyznaczyć konturu rozwinięcia.'); return res; }
  const area2 = pts => { let s = 0; for (let i = 0; i < pts.length; i++) { const a = pts[i], b = pts[(i + 1) % pts.length]; s += a[0] * b[1] - b[0] * a[1]; } return s / 2; };
  loops.sort((a, b) => Math.abs(area2(b)) - Math.abs(area2(a)));
  const outer = loops[0], holes = loops.slice(1);

  // 6. gięcia: oś neutralna każdego walca
  for (const mp of maps.values()) {
    if (mp.kind !== 'cyl') continue;
    const A = an[mp.face];
    const ang = A.span / RAD;
    const s0 = dot(sub(add(A.o, mul(A.axis, A.s0)), mp.E), mp.a), s1 = dot(sub(add(A.o, mul(A.axis, A.s1)), mp.E), mp.a);
    const mid = A.span * mp.rn / 2;
    const pa = [mp.P2[0] + mp.d2[0] * s0 + mp.w2[0] * mid, mp.P2[1] + mp.d2[1] * s0 + mp.w2[1] * mid];
    const pb = [mp.P2[0] + mp.d2[0] * s1 + mp.w2[0] * mid, mp.P2[1] + mp.d2[1] * s1 + mp.w2[1] * mid];
    bends.push({ a: pa, b: pb, angle: ang, rIn: mp.rIn, flat: A.span * mp.rn });
  }

  // 7. wymiary: wykrój układamy wzdłuż najdłuższej prostej krawędzi konturu (jak w CAD),
  //    prostokąt opisany w tej orientacji, przesunięcie do (0,0)
  const ang = longestEdgeAngle(outer);
  const ca = Math.cos(-ang), sa = Math.sin(-ang);
  let x0 = 1e18, x1 = -1e18, y0 = 1e18, y1 = -1e18;
  for (const p of outer) { const x = p[0] * ca - p[1] * sa, y = p[0] * sa + p[1] * ca; x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const bestR = { ang, x0, y0, w: x1 - x0, h: y1 - y0 };
  const rc = Math.cos(-bestR.ang), rs = Math.sin(-bestR.ang);
  const tf = p => [p[0] * rc - p[1] * rs - bestR.x0, p[0] * rs + p[1] * rc - bestR.y0];
  // odwzorowanie punktu 3D (z którejkolwiek strony blachy) na wykrój: przez płaską ścianę powierzchni A
  const planeFaces = placed.filter(i => an[i].type === 'plane');
  const map3to2 = P0 => {
    let best = null;
    for (const i of planeFaces) {
      const a = an[i], dA = dot(a.n, P0) - a.d;
      let Q = null;
      if (Math.abs(dA) < 0.05) Q = P0;                              // punkt na powierzchni A
      else if (Math.abs(dA + t) < 0.05) Q = add(P0, mul(a.n, t));   // punkt na drugiej stronie blachy
      if (!Q) continue;
      let d = 1e18;
      for (let tt = F[i].first; tt <= F[i].last; tt++) {
        d = Math.min(d, distPointTri(Q, P(m, m.idx[tt * 3]), P(m, m.idx[tt * 3 + 1]), P(m, m.idx[tt * 3 + 2])));
      }
      if (!best || d < best.d) best = { d, i, Q };
    }
    return best ? tf(maps.get(best.i).f(best.Q)) : null;
  };
  // odwzorowanie odwrotne: punkt wykroju -> punkt 3D na płaskiej ścianie powierzchni A
  // (ściana: ta, w której trójkątach 2D leży punkt albo najbliższa; gięcia nie są odwzorowywane)
  const tris2 = new Map();
  const trisOf = i => {
    if (!tris2.has(i)) {
      const f = maps.get(i).f, arr = [];
      for (let tt = F[i].first; tt <= F[i].last; tt++) arr.push([0, 1, 2].map(k => f(P(m, m.idx[tt * 3 + k]))));
      tris2.set(i, arr);
    }
    return tris2.get(i);
  };
  const map2to3 = q => {
    const X = q[0] + bestR.x0, Y = q[1] + bestR.y0;
    const q0 = [rc * X + rs * Y, -rs * X + rc * Y];             // odwrócenie tf (obrót + przesunięcie)
    let best = null;
    for (const i of planeFaces) {
      for (const [a, b, c] of trisOf(i)) {
        const d = dist2Tri(q0, a, b, c);
        if (!best || d < best.d) best = { d, i };
      }
    }
    if (!best) return null;
    const M = maps.get(best.i), v = [q0[0] - M.P2[0], q0[1] - M.P2[1]];
    const sx = v[0] * M.d2[0] + v[1] * M.d2[1], sy = v[0] * M.w2[0] + v[1] * M.w2[1];
    return { p: add(M.E, add(mul(M.a3, sx), mul(M.t3, sy))), n: an[best.i].n, face: best.i };
  };
  // przecięcie osi (np. ściany otworu) z płaską ścianą blachy: punkt 3D albo null, gdy oś jest równoległa do blachy
  const axisHit = (o, dir) => {
    let best = null;
    for (const i of planeFaces) {
      const a = an[i], nd = dot(a.n, dir);
      if (Math.abs(nd) < 0.999) continue;
      const Q = add(o, mul(dir, (a.d - dot(a.n, o)) / nd));
      let d = 1e18;
      for (let tt = F[i].first; tt <= F[i].last; tt++) d = Math.min(d, distPointTri(Q, P(m, m.idx[tt * 3]), P(m, m.idx[tt * 3 + 1]), P(m, m.idx[tt * 3 + 2])));
      if (!best || d < best.d) best = { d, Q };
    }
    return best ? best.Q : null;
  };
  const holesOut = holes.map(h => {
    const pts = h.map(tf);
    const fit = pts.length >= 8 ? fitCircle2(pts) : null;
    const circle = fit && fit.rms < 0.02 ? { c: fit.c, r: fit.r } : null;
    return { pts, circle, area: Math.abs(area2(pts)) };
  });
  Object.assign(res, {
    ok: true, outer: outer.map(tf), holes: holesOut, bends: bends.map(b => ({ ...b, a: tf(b.a), b: tf(b.b) })),
    width: bestR.w, height: bestR.h,
    area: Math.abs(area2(outer)) - holesOut.reduce((s, h) => s + h.area, 0),
    faces: placed.length, map3to2, map2to3, axisHit,
  });
  return res;
}

// kąt najdłuższej prostej krawędzi konturu (sąsiednie współliniowe odcinki są scalane)
function longestEdgeAngle(pts) {
  const n = pts.length;
  const seg = i => { const a = pts[i], b = pts[(i + 1) % n]; return [b[0] - a[0], b[1] - a[1]]; };
  let best = { l: 0, ang: 0 }, i = 0, guard = 0;
  // start od załamania, żeby nie ciąć krawędzi w połowie
  let start = 0;
  for (let k = 0; k < n; k++) {
    const p = seg((k - 1 + n) % n), q = seg(k);
    const cs = (p[0] * q[0] + p[1] * q[1]) / ((Math.hypot(...p) * Math.hypot(...q)) || 1);
    if (cs < Math.cos(2 * RAD)) { start = k; break; }
  }
  i = start;
  while (guard++ < n) {
    let [dx, dy] = seg(i % n), l = Math.hypot(dx, dy), ang = Math.atan2(dy, dx), j = i + 1;
    while (j - start < n) {
      const [ex, ey] = seg(j % n), el = Math.hypot(ex, ey);
      if (!el) { j++; continue; }
      const da = Math.abs(Math.atan2(ex * dy - ey * dx, ex * dx + ey * dy));
      if (da > 2 * RAD) break;
      dx += ex; dy += ey; l += el; j++;
    }
    if (l > best.l) best = { l, ang: Math.atan2(dy, dx) };
    if (j - start >= n) break;
    i = j;
  }
  return best.ang;
}

// odległość punktu od trójkąta w 3D
function distPointTri(p, a, b, c) {
  const ab = sub(b, a), ac = sub(c, a), ap = sub(p, a);
  const d1 = dot(ab, ap), d2 = dot(ac, ap);
  if (d1 <= 0 && d2 <= 0) return len(ap);
  const bp = sub(p, b), d3 = dot(ab, bp), d4 = dot(ac, bp);
  if (d3 >= 0 && d4 <= d3) return len(bp);
  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) return len(sub(p, add(a, mul(ab, d1 / (d1 - d3)))));
  const cp = sub(p, c), d5 = dot(ab, cp), d6 = dot(ac, cp);
  if (d6 >= 0 && d5 <= d6) return len(cp);
  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) return len(sub(p, add(a, mul(ac, d2 / (d2 - d6)))));
  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
    const w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
    return len(sub(p, add(b, mul(sub(c, b), w))));
  }
  const dn = 1 / (va + vb + vc);
  return len(sub(p, add(a, add(mul(ab, vb * dn), mul(ac, vc * dn)))));
}

// odległość punktu od trójkąta na płaszczyźnie (0, gdy punkt leży wewnątrz)
function dist2Tri(p, a, b, c) {
  const cr = (u, v, w) => (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0]);
  const d1 = cr(a, b, p), d2 = cr(b, c, p), d3 = cr(c, a, p);
  if ((d1 >= 0 && d2 >= 0 && d3 >= 0) || (d1 <= 0 && d2 <= 0 && d3 <= 0)) return 0;
  const seg = (u, v) => {
    const dx = v[0] - u[0], dy = v[1] - u[1], l2 = dx * dx + dy * dy;
    const t = l2 ? Math.max(0, Math.min(1, ((p[0] - u[0]) * dx + (p[1] - u[1]) * dy) / l2)) : 0;
    return Math.hypot(p[0] - (u[0] + t * dx), p[1] - (u[1] + t * dy));
  };
  return Math.min(seg(a, b), seg(b, c), seg(c, a));
}

function convexHull(pts) {
  const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [];
  for (const q of p) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  const up = [];
  for (const q of p.slice().reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
  lo.pop(); up.pop();
  return lo.concat(up);
}
