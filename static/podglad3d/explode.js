// Widok rozstrzelony: komponenty rozsuwają się od środka modelu (promieniowo albo wzdłuż osi).
import * as THREE from 'three';

export function initExplode(ctx) {
  let units = null;                       // [{meshes:[i...], center: Vector3}]
  let factor = 0, mode = 'radial';
  const center = new THREE.Vector3();

  function build() {
    const groups = ctx.getUnits();
    units = groups.map(ms => {
      const b = new THREE.Box3();
      ms.forEach(i => { const g = ctx.parts[i].mesh.geometry; g.computeBoundingBox(); b.union(g.boundingBox); });
      return { meshes: ms, center: b.getCenter(new THREE.Vector3()) };
    });
    ctx.box.getCenter(center);
  }

  function apply() {
    if (!units) build();
    const axes = { x: 0, y: 1, z: 2 };
    units.forEach(u => {
      const d = u.center.clone().sub(center);
      let off;
      if (mode === 'radial') off = d.multiplyScalar(factor);
      else { off = new THREE.Vector3(); const a = axes[mode]; off.setComponent(a, d.getComponent(a) * factor); }
      u.meshes.forEach(i => {
        const p = ctx.parts[i];
        p.mesh.position.copy(off);
        if (p.edges) p.edges.position.copy(off);
      });
    });
  }

  return {
    set(k) { factor = k; apply(); },
    setMode(m) { mode = m; apply(); },
    active: () => factor > 0,
    count: () => (units ? units.length : ctx.getUnits().length),
  };
}
