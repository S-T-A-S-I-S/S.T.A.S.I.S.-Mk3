/**
 * Samsung One UI orb — Three.js particle field
 *
 * States:
 *   idle      – low density, dark Samsung blue, slow drift
 *   listening – medium density, Samsung blue, microphone reactive
 *   thinking  – tight dense core, cyan, fast rotation, full mesh
 *   speaking  – medium spread, blue→cyan pulse, bass reactive
 */
import * as THREE from 'three';

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking';

interface StateCfg {
  radius:    number;
  speed:     number;
  lineRatio: number;
  ptColor:   THREE.Color;
  lnColor:   THREE.Color;
  ptOpacity: number;
  lnOpacity: number;
}

const CFGS: Record<OrbState, StateCfg> = {
  idle: {
    radius: 26, speed: 0.28, lineRatio: 0.10,
    ptColor: new THREE.Color('#1A4DB5'), lnColor: new THREE.Color('#2B7DFF'),
    ptOpacity: 0.70, lnOpacity: 0.12,
  },
  listening: {
    radius: 21, speed: 0.55, lineRatio: 0.32,
    ptColor: new THREE.Color('#2B7DFF'), lnColor: new THREE.Color('#5599FF'),
    ptOpacity: 0.82, lnOpacity: 0.22,
  },
  thinking: {
    radius: 15, speed: 1.35, lineRatio: 0.88,
    ptColor: new THREE.Color('#00BFFF'), lnColor: new THREE.Color('#00D4FF'),
    ptOpacity: 0.95, lnOpacity: 0.38,
  },
  speaking: {
    radius: 19, speed: 0.80, lineRatio: 0.58,
    ptColor: new THREE.Color('#2B7DFF'), lnColor: new THREE.Color('#00D4FF'),
    ptOpacity: 0.88, lnOpacity: 0.28,
  },
};

const N = 2000;        // particle count
const MAX_SEGS = 800;  // max line segments per frame

export interface Orb {
  setState(s: OrbState): void;
  pushAudio(freq: Uint8Array<ArrayBuffer>): void;
  dispose(): void;
}

export function createOrb(canvas: HTMLCanvasElement): Orb {
  // ── Renderer ───────────────────────────────────────────────────────────────
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);

  const scene  = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(58, 1, 0.1, 1000);
  camera.position.z = 85;

  // ── Particles ──────────────────────────────────────────────────────────────
  const rawPos = new Float32Array(N * 3);
  const vel:   THREE.Vector3[] = [];
  const base:  THREE.Vector3[] = [];

  for (let i = 0; i < N; i++) {
    const θ = Math.acos(2 * Math.random() - 1);
    const φ = Math.random() * Math.PI * 2;
    const r = 16 + Math.random() * 12;
    const x = r * Math.sin(θ) * Math.cos(φ);
    const y = r * Math.sin(θ) * Math.sin(φ);
    const z = r * Math.cos(θ);
    rawPos[i * 3] = x; rawPos[i * 3 + 1] = y; rawPos[i * 3 + 2] = z;
    base.push(new THREE.Vector3(x, y, z));
    vel.push(new THREE.Vector3(
      (Math.random() - 0.5) * 0.04,
      (Math.random() - 0.5) * 0.04,
      (Math.random() - 0.5) * 0.04,
    ));
  }

  const ptGeo = new THREE.BufferGeometry();
  ptGeo.setAttribute('position', new THREE.BufferAttribute(rawPos, 3));

  const ptMat = new THREE.PointsMaterial({
    size: 0.52,
    color: CFGS.idle.ptColor.clone(),
    transparent: true,
    opacity: CFGS.idle.ptOpacity,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  const points = new THREE.Points(ptGeo, ptMat);
  scene.add(points);

  // ── Lines ──────────────────────────────────────────────────────────────────
  const lnRaw = new Float32Array(MAX_SEGS * 2 * 3);
  const lnGeo = new THREE.BufferGeometry();
  lnGeo.setAttribute('position', new THREE.BufferAttribute(lnRaw, 3));
  lnGeo.setDrawRange(0, 0);

  const lnMat = new THREE.LineBasicMaterial({
    color: CFGS.idle.lnColor.clone(),
    transparent: true,
    opacity: CFGS.idle.lnOpacity,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  const lines = new THREE.LineSegments(lnGeo, lnMat);
  scene.add(lines);

  // ── State ──────────────────────────────────────────────────────────────────
  let state: OrbState = 'idle';
  let cfg: StateCfg   = CFGS.idle;
  let curRadius = CFGS.idle.radius;
  let rotation  = 0;
  let tumble    = 0;
  let audioFreq: Uint8Array<ArrayBuffer> = new Uint8Array(256);

  // ── Resize ─────────────────────────────────────────────────────────────────
  function resize(): void {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  resize();

  // ── Animation loop ─────────────────────────────────────────────────────────
  let raf: number;

  function tick(): void {
    raf = requestAnimationFrame(tick);

    // Lerp radius / color / opacity toward target
    curRadius += (cfg.radius - curRadius) * 0.05;
    ptMat.color.lerp(cfg.ptColor, 0.06);
    lnMat.color.lerp(cfg.lnColor, 0.06);
    ptMat.opacity += (cfg.ptOpacity - ptMat.opacity) * 0.06;
    lnMat.opacity += (cfg.lnOpacity - lnMat.opacity) * 0.06;

    // Audio
    let bass = 0, mid = 0;
    for (let i = 0; i < 8;  i++) bass += audioFreq[i] / 255;
    for (let i = 8; i < 60; i++) mid  += audioFreq[i] / 255;
    bass /= 8; mid /= 52;

    // Rotation
    const speedMult = 1 + bass * 2.2 + tumble * 1.8;
    rotation += cfg.speed * 0.005 * speedMult;
    tumble    = Math.max(0, tumble - 0.018);

    // Update particle positions
    const pos = (ptGeo.attributes.position as THREE.BufferAttribute).array as Float32Array;
    for (let i = 0; i < N; i++) {
      const v = vel[i];
      v.x += (Math.random() - 0.5) * 0.003;
      v.y += (Math.random() - 0.5) * 0.003;
      v.z += (Math.random() - 0.5) * 0.003;
      v.clampLength(0, 0.09);

      const nx = pos[i * 3]     + v.x;
      const ny = pos[i * 3 + 1] + v.y;
      const nz = pos[i * 3 + 2] + v.z;
      const d  = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;

      // Bass pushes particles out; pull back toward target sphere
      const spread = bass * 5.5 + mid * 2.5;
      const target = curRadius + spread * (d / (curRadius + 1));
      const scale  = target / d;

      pos[i * 3]     = nx * scale;
      pos[i * 3 + 1] = ny * scale + Math.sin(rotation + i * 0.009) * 0.08;
      pos[i * 3 + 2] = nz * scale;
    }
    ptGeo.attributes.position.needsUpdate = true;

    // Update line segments
    let seg = 0;
    const lp = (lnGeo.attributes.position as THREE.BufferAttribute).array as Float32Array;
    const ratio = cfg.lineRatio;

    for (let i = 0; i < N && seg < MAX_SEGS; i += 4) {
      if (Math.random() > ratio) continue;
      for (let j = i + 1; j < Math.min(i + 14, N) && seg < MAX_SEGS; j++) {
        const dx = pos[i * 3]     - pos[j * 3];
        const dy = pos[i * 3 + 1] - pos[j * 3 + 1];
        const dz = pos[i * 3 + 2] - pos[j * 3 + 2];
        if (dx * dx + dy * dy + dz * dz < 72) {
          const o = seg * 6;
          lp[o]     = pos[i * 3];     lp[o + 1] = pos[i * 3 + 1]; lp[o + 2] = pos[i * 3 + 2];
          lp[o + 3] = pos[j * 3];     lp[o + 4] = pos[j * 3 + 1]; lp[o + 5] = pos[j * 3 + 2];
          seg++;
        }
      }
    }
    lnGeo.attributes.position.needsUpdate = true;
    lnGeo.setDrawRange(0, seg * 2);

    // Rotate group
    points.rotation.y = rotation * 0.4;
    points.rotation.x = Math.sin(rotation * 0.14) * 0.28 + tumble * 0.45;
    lines.rotation.copy(points.rotation);

    renderer.render(scene, camera);
  }

  tick();

  return {
    setState(s: OrbState): void {
      if (s === state) return;
      state  = s;
      cfg    = CFGS[s];
      tumble = 1.0;
    },
    pushAudio(freq: Uint8Array<ArrayBuffer>): void {
      audioFreq = freq;
    },
    dispose(): void {
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.dispose();
    },
  };
}
