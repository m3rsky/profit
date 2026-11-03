// Rozwinięcie blachy: podgląd 2D (SVG) i eksport do DXF. Obliczenia w geom.js (unfoldSheet).
import * as G from './geom.js';
import { initFlatDims } from './flatdim.js';

const SVGNS = 'http://www.w3.org/2000/svg';

// usuwa punkty leżące na prostej (tolerancja w mm), żeby DXF nie miał tysięcy mikrosegmentów
export function simplify(pts, tol = 0.01) {
  if (pts.length < 4) return pts;
  const keep = [];
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const a = pts[(i - 1 + n) % n], b = pts[i], c = pts[(i + 1) % n];
    const dx = c[0] - a[0], dy = c[1] - a[1], l = Math.hypot(dx, dy);
    const d = l ? Math.abs((b[0] - a[0]) * dy - (b[1] - a[1]) * dx) / l : 0;
    if (d > tol) keep.push(b);
  }
  return keep.length >= 3 ? keep : pts;
}

export function buildDxf(res) {
  const out = [];
  const g = (code, val) => out.push(String(code), String(val));
  const f = x => (Math.round(x * 10000) / 10000).toString();
  g(0, 'SECTION'); g(2, 'HEADER'); g(9, '$ACADVER'); g(1, 'AC1009'); g(0, 'ENDSEC');
  g(0, 'SECTION'); g(2, 'TABLES');
  g(0, 'TABLE'); g(2, 'LTYPE'); g(70, 1);
  g(0, 'LTYPE'); g(2, 'CONTINUOUS'); g(70, 0); g(3, 'Solid line'); g(72, 65); g(73, 0); g(40, 0);
  g(0, 'ENDTAB');
  g(0, 'TABLE'); g(2, 'LAYER'); g(70, 3);
  for (const [name, color] of [['CONTOUR', 7], ['HOLES', 7], ['BEND', 1]]) { g(0, 'LAYER'); g(2, name); g(70, 0); g(62, color); g(6, 'CONTINUOUS'); }
  g(0, 'ENDTAB');
  g(0, 'ENDSEC');
  g(0, 'SECTION'); g(2, 'ENTITIES');
  const line = (layer, a, b) => { g(0, 'LINE'); g(8, layer); g(10, f(a[0])); g(20, f(a[1])); g(30, 0); g(11, f(b[0])); g(21, f(b[1])); g(31, 0); };
  const poly = (layer, pts) => { for (let i = 0; i < pts.length; i++) line(layer, pts[i], pts[(i + 1) % pts.length]); };
  poly('CONTOUR', simplify(res.outer));
  for (const h of res.holes) {
    if (h.circle) { g(0, 'CIRCLE'); g(8, 'HOLES'); g(10, f(h.circle.c[0])); g(20, f(h.circle.c[1])); g(30, 0); g(40, f(h.circle.r)); }
    else poly('HOLES', simplify(h.pts));
  }
  for (const b of res.bends) line('BEND', b.a, b.b);
  g(0, 'ENDSEC'); g(0, 'EOF');
  return out.join('\r\n') + '\r\n';
}

export function initFlat(ctx) {
  const modal = document.getElementById('flat');
  const svgHost = document.getElementById('flat-svg');
  const infoEl = document.getElementById('flat-info');
  const kInput = document.getElementById('flat-k');
  const titleEl = document.getElementById('flat-title');
  let current = null;
  const dims = initFlatDims(ctx);

  function el(name, attrs) {
    const e = document.createElementNS(SVGNS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  const pathOf = pts => 'M' + pts.map(p => p[0].toFixed(3) + ' ' + (-p[1]).toFixed(3)).join('L') + 'Z';

  function render(res) {
    svgHost.textContent = '';
    infoEl.textContent = '';
    const add = (label, value) => { const d = document.createElement('div'); d.append(label + ': '); const b = document.createElement('b'); b.textContent = value; d.append(b); infoEl.append(d); };
    if (!res.ok) {
      const d = document.createElement('div');
      d.className = 'err';
      d.textContent = res.errors.join(' ');
      infoEl.append(d);
      return;
    }
    const W = res.width, H = res.height, pad = Math.max(W, H) * 0.06;
    const svg = el('svg', { viewBox: `${-pad} ${-H - pad} ${W + 2 * pad} ${H + 2 * pad}`, preserveAspectRatio: 'xMidYMid meet' });
    svg.append(el('rect', { x: 0, y: -H, width: W, height: H, fill: 'none', stroke: '#9aa4b2', 'stroke-width': Math.max(W, H) / 900, 'stroke-dasharray': `${W / 120} ${W / 120}` }));
    svg.append(el('path', { d: pathOf(res.outer), fill: '#eef3f8', stroke: '#1d2329', 'stroke-width': Math.max(W, H) / 500, 'stroke-linejoin': 'round' }));
    res.holes.forEach(h => {
      if (h.circle) svg.append(el('circle', { cx: h.circle.c[0], cy: -h.circle.c[1], r: h.circle.r, fill: '#fff', stroke: '#1d2329', 'stroke-width': Math.max(W, H) / 500 }));
      else svg.append(el('path', { d: pathOf(h.pts), fill: '#fff', stroke: '#1d2329', 'stroke-width': Math.max(W, H) / 500 }));
    });
    res.bends.forEach(b => svg.append(el('line', { x1: b.a[0], y1: -b.a[1], x2: b.b[0], y2: -b.b[1], stroke: '#c8102e', 'stroke-width': Math.max(W, H) / 600, 'stroke-dasharray': `${W / 60} ${W / 120}` })));
    const txt = (x, y, s, anchor) => { const t = el('text', { x, y, 'font-size': Math.max(W, H) / 45, fill: '#1d2329', 'text-anchor': anchor || 'middle', 'font-family': 'system-ui,sans-serif' }); t.textContent = s; svg.append(t); };
    txt(W / 2, -H - pad * 0.25, W.toFixed(2) + ' mm');
    const vt = el('text', { x: -pad * 0.35, y: -H / 2, 'font-size': Math.max(W, H) / 45, fill: '#1d2329', 'text-anchor': 'middle', transform: `rotate(-90 ${-pad * 0.35} ${-H / 2})`, 'font-family': 'system-ui,sans-serif' });
    vt.textContent = H.toFixed(2) + ' mm';
    svg.append(vt);
    svgHost.append(svg);

    add('Grubość blachy', res.t.toFixed(2) + ' mm');
    add('Współczynnik K', res.K.toFixed(2));
    add('Wymiar półfabrykatu', W.toFixed(2) + ' × ' + H.toFixed(2) + ' mm');
    add('Pole', (res.area / 100).toFixed(1) + ' cm²');
    add('Gięcia', String(res.bends.length) + (res.bends.length ? ' (' + [...new Set(res.bends.map(b => b.angle.toFixed(0) + '° R' + b.rIn.toFixed(2)))].join(', ') + ')' : ''));
    const circ = res.holes.filter(h => h.circle), other = res.holes.length - circ.length;
    const byD = {};
    circ.forEach(h => { const k = (2 * h.circle.r).toFixed(2); byD[k] = (byD[k] || 0) + 1; });
    add('Otwory', circ.length ? Object.keys(byD).map(k => byD[k] + '× Ø' + k).join(', ') + (other ? ' + ' + other + ' innych' : '') : (other ? other + ' (niekołowe)' : 'brak'));
    res.warnings.forEach(w => { const d = document.createElement('div'); d.className = 'warn'; d.textContent = w; infoEl.append(d); });
  }

  const bodySel = document.getElementById('flat-body');
  let cands = [], results = [];
  const score = r => (!r.ok ? -1 : (r.bends.length ? 1e9 : 0) + r.area);

  // rysunek wykroju + wymiary na nim (pomiary z wykroju i z modelu 3D)
  function show(res) {
    render(res);
    const svg = svgHost.querySelector('svg');
    if (res && res.ok && svg) dims.attach({ svg, res, part: cands[bodySel.selectedIndex].index, body: titleEl.textContent.replace(/^Rozwinięcie blachy: /, '') });
    else dims.detach();
  }

  function kValue() {
    const K = parseFloat(String(kInput.value).replace(',', '.'));
    return isFinite(K) ? K : 0.5;
  }
  function label(c, r) {
    return c.label + (r && r.ok ? ': t=' + r.t.toFixed(2) + ' mm, ' + r.width.toFixed(1) + ' × ' + r.height.toFixed(1) + ' mm' : ': nie jest blachą');
  }
  function compute(all) {
    const K = kValue();
    if (all) results = cands.map(c => ctx.safeUnfold(c.index, K));
    else results[bodySel.selectedIndex] = ctx.safeUnfold(cands[bodySel.selectedIndex].index, K);
    if (all) {
      let best = 0;
      results.forEach((r, i) => { if (score(r) > score(results[best])) best = i; });
      bodySel.textContent = '';
      cands.forEach((c, i) => { const o = document.createElement('option'); o.value = i; o.textContent = label(c, results[i]); bodySel.append(o); });
      bodySel.selectedIndex = best;
    }
    bodySel.style.display = cands.length > 1 ? '' : 'none';
    current = results[bodySel.selectedIndex];
    show(current);
  }

  function open(candidates, name) {
    cands = candidates;
    titleEl.textContent = 'Rozwinięcie blachy: ' + name;
    modal.classList.add('open');
    infoEl.textContent = 'Obliczanie…';
    svgHost.textContent = '';
    setTimeout(() => compute(true), 20);
  }
  bodySel.onchange = () => { current = results[bodySel.selectedIndex]; show(current); };

  document.getElementById('flat-calc').onclick = () => compute(false);
  document.getElementById('flat-close').onclick = () => { dims.detach(); modal.classList.remove('open'); };
  document.getElementById('flat-dxf').onclick = () => {
    if (!current || !current.ok) return;
    const blob = new Blob([buildDxf(current)], { type: 'application/dxf' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (titleEl.dataset.file || 'rozwiniecie') + '.dxf';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  return { open, result: () => current, compute, cands: () => cands, dims };
}
