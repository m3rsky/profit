// Sprawdzenia geometrii podglądu 3D w przeglądarce (ładowane przez _browser_step.py).
// Siatki syntetyczne o znanych wymiarach: wspornik L z blachy (rozwinięcie liczone ze wzoru)
// i podkładka z okręgami (wykrywanie otworów). Bez żadnych plików projektowych.
async () => {
  const G = await import('/static/podglad3d/geom.js');
  const F = await import('/static/podglad3d/flat.js');
  const out = {};

  function Builder() {
    const pos = [], idx = [], faces = [];
    // quads: lista [p0,p1,p2,p3]; want(cen, n) -> true gdy normalna n ma być zwrócona na zewnątrz
    const addFace = (quads, want) => {
      const first = idx.length / 3;
      for (const q of quads) for (const tri of [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]) {
        const [a, b, c] = tri;
        const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], w = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        const n = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]];
        const cen = [(a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3, (a[2] + b[2] + c[2]) / 3];
        const ord = want(cen, n) ? [a, b, c] : [a, c, b];
        for (const p of ord) { idx.push(pos.length / 3); pos.push(p[0], p[1], p[2]); }
      }
      faces.push({ first, last: idx.length / 3 - 1 });
    };
    return { pos, idx, faces, addFace };
  }
  const dir = v => (cen, n) => n[0] * v[0] + n[1] * v[1] + n[2] * v[2] > 0;

  // wspornik L: blacha t, promień wewnętrzny r, ramię 1 (L1), ramię 2 (L2), szerokość W, gięcie 90 stopni
  function bracket(t, r, L1, L2, W, N) {
    const b = Builder(), R = r + t, cz = t + r;
    b.addFace([[[0, 0, 0], [L1, 0, 0], [L1, W, 0], [0, W, 0]]], dir([0, 0, -1]));        // zewn. 1
    b.addFace([[[0, 0, t], [L1, 0, t], [L1, W, t], [0, W, t]]], dir([0, 0, 1]));          // wewn. 1
    const cyl = (rad) => {
      const q = [];
      for (let k = 0; k < N; k++) {
        const f0 = -Math.PI / 2 + k * (Math.PI / 2) / N, f1 = -Math.PI / 2 + (k + 1) * (Math.PI / 2) / N;
        const p = (f, y) => [L1 + rad * Math.cos(f), y, cz + rad * Math.sin(f)];
        q.push([p(f0, 0), p(f1, 0), p(f1, W), p(f0, W)]);
      }
      return q;
    };
    b.addFace(cyl(R), (cen, n) => n[0] * (cen[0] - L1) + n[2] * (cen[2] - cz) > 0);       // walec zewn. (wypukły)
    b.addFace(cyl(r), (cen, n) => n[0] * (cen[0] - L1) + n[2] * (cen[2] - cz) < 0);       // walec wewn. (wklęsły)
    const xo = L1 + r + t, xi = L1 + r, z0 = cz, z1 = cz + L2;
    b.addFace([[[xo, 0, z0], [xo, W, z0], [xo, W, z1], [xo, 0, z1]]], dir([1, 0, 0]));    // zewn. 2
    b.addFace([[[xi, 0, z0], [xi, W, z0], [xi, W, z1], [xi, 0, z1]]], dir([-1, 0, 0]));   // wewn. 2
    b.addFace([[[0, 0, 0], [0, W, 0], [0, W, t], [0, 0, t]]], dir([-1, 0, 0]));           // czoło ramienia 1
    b.addFace([[[xi, 0, z1], [xo, 0, z1], [xo, W, z1], [xi, W, z1]]], dir([0, 0, 1]));    // czoło ramienia 2
    return b;
  }

  // ── rozwinięcie wspornika: długość = L1 + L2 + 90/360 * 2*pi * (r + K t) ──
  const T = 2, RIN = 3, L1 = 40, L2 = 30, W = 50;
  const br = bracket(T, RIN, L1, L2, W, 12);
  const m = G.prepare(br.pos, br.idx, br.faces);
  const unf = K => G.unfoldSheet(m, { K });
  const r5 = unf(0.5), r33 = unf(0.33);
  const dims = r => (r.ok ? [r.width, r.height].sort((a, b) => b - a) : null);
  out.bracket = {
    ok: r5.ok, errors: r5.errors, t: r5.t, bends: r5.ok ? r5.bends.length : -1,
    angle: r5.ok ? r5.bends[0].angle : null, rIn: r5.ok ? r5.bends[0].rIn : null,
    dims05: dims(r5), expect05: [L1 + L2 + Math.PI / 2 * (RIN + 0.5 * T), W],
    dims033: dims(r33), expect033: [L1 + L2 + Math.PI / 2 * (RIN + 0.33 * T), W],
    holes: r5.ok ? r5.holes.length : -1,
  };
  if (r5.ok) {
    const dxf = F.buildDxf(r5);
    out.dxf = { eof: dxf.trim().endsWith('EOF'), contour: dxf.includes('CONTOUR'), bend: dxf.includes('BEND'), lines: (dxf.match(/\nLINE\r?\n/g) || []).length };
  }

  // bryła bez blachy (tylko płaszczyzna) nie rozwija się
  const lone = Builder();
  lone.addFace([[[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]]], dir([0, 0, 1]));
  out.notSheet = G.unfoldSheet(G.prepare(lone.pos, lone.idx, lone.faces), {}).ok;

  // ── podkładka: dwa okręgi (otwory) o znanych średnicach, środek (7, 5) ──
  const wsh = Builder(), N = 48, cx = 7, cy = 5, qs = [];
  for (let k = 0; k < N; k++) {
    const a0 = k / N * 2 * Math.PI, a1 = (k + 1) / N * 2 * Math.PI;
    const o = a => [cx + 10 * Math.cos(a), cy + 10 * Math.sin(a), 0], i = a => [cx + 4 * Math.cos(a), cy + 4 * Math.sin(a), 0];
    qs.push([o(a0), o(a1), i(a1), i(a0)]);
  }
  wsh.addFace(qs, dir([0, 0, 1]));
  const wm = G.prepare(wsh.pos, wsh.idx, wsh.faces);
  const ft = G.faceFeatures(wm, wm.faces[0]);
  out.washer = { type: ft.type, circles: ft.circles.map(c => ({ d: 2 * c.r, c: c.c })).sort((a, b) => a.d - b.d) };

  // ── pomiary między cechami ──
  const rows = (A, B) => G.measurePair(A, B).rows;
  const s30 = Math.sin(Math.PI / 6), c30 = Math.cos(Math.PI / 6);
  out.pairs = {
    ptpt: rows({ k: 'pt', p: [0, 0, 0] }, { k: 'pt', p: [3, 4, 0] }),
    circleLine: rows({ k: 'circle', c: [10, 10, 0], r: 3, n: [0, 0, 1] }, { k: 'line', a: [0, 0, 0], dir: [1, 0, 0] }),
    planeParallel: rows({ k: 'plane', n: [0, 0, 1], anchor: [0, 0, 0] }, { k: 'plane', n: [0, 0, -1], anchor: [5, 5, 1.2] }),
    planeAngle: rows({ k: 'plane', n: [0, 0, 1], anchor: [0, 0, 0] }, { k: 'plane', n: [0, s30, c30], anchor: [0, 0, 0] }),
    lineSkew: rows({ k: 'line', a: [0, 0, 0], dir: [1, 0, 0] }, { k: 'line', a: [0, 0, 5], dir: [0, 1, 0] }),
    pointPlane: rows({ k: 'pt', p: [1, 2, 7.5] }, { k: 'plane', n: [0, 0, 1], anchor: [0, 0, 2] }),
  };
  // ── wykrój: cechy i przenoszenie pomiarów 3D (także z wewnętrznej strony blachy) ──
  const FD = await import('/static/podglad3d/flatdim.js');
  const ff = FD.flatFeatures(r5, 7);
  out.flatFeats = { lines: ff.lines.length, bendLines: ff.lines.filter(l => l.bend).length, corners: ff.corners.length, circles: ff.circles.length };
  const dist = rec => { const m = FD.mapRecord(r5, rec); return m.ok ? m.rows[0] : null; };
  const zTop = T + RIN + L2;
  out.transfer = {
    inner: dist({ A: { k: 'pt', p: [L1 + RIN, 0, zTop], part: 7 }, B: { k: 'pt', p: [0, 0, T], part: 7 } }),
    outer: dist({ A: { k: 'pt', p: [L1 + RIN + T, 0, zTop], part: 7 }, B: { k: 'pt', p: [0, 0, 0], part: 7 } }),
    line: dist({ A: { k: 'line', a: [0, 0, T], b: [0, W, T], dir: [0, 1, 0], part: 7 }, B: { k: 'pt', p: [L1 + RIN, 0, zTop], part: 7 } }),
    straight3d: Math.hypot(L1 + RIN, zTop - T),
    outside: FD.mapRecord(r5, { A: { k: 'pt', p: [100, 100, 100], part: 7 }, B: { k: 'pt', p: [0, 0, 0], part: 7 } }).ok,
    plane: FD.mapRecord(r5, { A: { k: 'plane', n: [0, 0, 1], anchor: [0, 0, T], part: 7 }, B: { k: 'pt', p: [0, 0, 0], part: 7 } }).ok,
  };
  let far = 0;
  for (const a of ff.corners) for (const b of ff.corners) far = Math.max(far, Math.hypot(a.p[0] - b.p[0], a.p[1] - b.p[1]));
  out.flatDiag = { got: far, expect: Math.hypot(L1 + L2 + Math.PI / 2 * (RIN + 0.5 * T), W) };

  // wspornik jako bryła testowa o numerze 9999 (do sprawdzeń interfejsu okna rozwinięcia)
  if (window.viewer3d) window.viewer3d.meshData[9999] = { pos: br.pos, idx: br.idx, faces: br.faces };
  out.zTop = zTop;
  return out;
}
