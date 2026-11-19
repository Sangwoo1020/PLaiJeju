// 제주 섬 3D 장면. 섬 위의 기둥 하나가 지역 하나이고, 높이와 색이 지표 값이다.
// 카메라는 "연출"을 한다: 시작 화면에서는 섬을 천천히 돌고, 지표를 바꾸면 지표마다 정해 둔 구도로 날아가며,
// 지역을 고르면 그 동네로 내려간다. 이용자가 끌거나 휠을 굴리면 그때부터는 이용자가 카메라를 잡는다.
import * as THREE from "three";
import { OrbitControls } from "/PLaiJeju/static/vendor/OrbitControls.js";
import { CENTER, HALLASAN, ISLETS } from "/PLaiJeju/static/areas.js";
import { loadProps } from "/PLaiJeju/static/props.js";
import { buildCity, LANDMARK_KINDS } from "/PLaiJeju/static/city.js";

const SCALE = 46;               // 경도 1도 = 46 단위
const LAT_STRETCH = 1.19;       // 북위 33도에서 위도 1도는 경도 1도보다 길다
const ISLAND_SPAN = 31;         // 화면에 꽉 차게 담을 폭. 섬이 비스듬히 보여 실제 동서 폭(약 37)보다 작게 잡는다
const MIN_VIEW_HEIGHT = 17;
const PROP_SIZE = 1.5;          // 소품은 실제 비례보다 크게 그린다
const COLORS = {
  up: 0x2a6fdb, down: 0x43b6e3, hidden: 0xc5ced6, shareLow: 0xb5d2f2, shareHigh: 0x1d5fb0, riskLow: 0xf7c3b5, riskHigh: 0xd8432a,
  sea: 0xa8d3f6, shallow: 0xc6ecf1, foam: 0xf4fbff,
  stone: 0x8e979c, tree: 0x6fb67c, fruit: 0xf29a3f, white: 0xffffff, navy: 0x2c4a6e, red: 0xe5584f, trunk: 0xa98467,
};
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** [경도, 위도] → 장면 좌표 (x: 동쪽, z: 남쪽) */
export function toScene([lon, lat]) {
  return new THREE.Vector3((lon - CENTER[0]) * SCALE, 0, -(lat - CENTER[1]) * SCALE * LAT_STRETCH);
}

const smoothstep = (a, b, v) => { const t = Math.min(1, Math.max(0, (v - a) / (b - a))); return t * t * (3 - 2 * t); };
const material = (color, options = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.8, metalness: 0, ...options });

function mesh(geometry, color, { cast = true, receive = true, ...options } = {}) {
  const object = new THREE.Mesh(geometry, material(color, options));
  object.castShadow = cast;
  object.receiveShadow = receive;
  return object;
}

// ---------- 지형 (실제 고도 데이터) ----------
// jeju_terrain.bin: tools/build_terrain.py 가 공개 고도 타일로 만든 격자.
//   양수 = 땅의 고도(m), 음수 = 바다이며 -(해안까지 거리 × 100).
const HEIGHT_PER_METER = 0.00247;   // 한라산(1,947m)이 4.8 단위쯤 되도록, 실제보다 약 5배 높게 그린다
const SEA_LEVEL = -0.03;
const LAND_RAMP = [[0, 0xf3ecd6], [25, 0xd3ecc2], [180, 0xb2dda9], [550, 0x86c391], [1050, 0x7fad8c], [1450, 0xa9ad9e], [1850, 0xf4f4f0]];  // [고도 m, 색]
const SEA_RAMP = [[0, COLORS.foam], [0.22, COLORS.shallow], [1.2, COLORS.shallow], [2.6, COLORS.sea]];                                  // [해안까지 거리, 색]
const halla = toScene(HALLASAN);
const chuja = toScene(ISLETS["추자도"]);   // 추자도는 실제로는 훨씬 북쪽이라 격자에 없다. 화면 안에 따로 그린다.

/** [값, 색] 목록을 부드럽게 잇는 색 함수 */
function ramp(stops) {
  const colors = stops.map(([, hex]) => new THREE.Color(hex));
  return (value, target) => {
    let i = 1;
    while (i < stops.length - 1 && value > stops[i][0]) i++;
    return target.copy(colors[i - 1]).lerp(colors[i], smoothstep(stops[i - 1][0], stops[i][0], value));
  };
}

function random(seed) {   // 고정 난수 (매번 같은 잔물결이 나오도록)
  let state = seed;
  return () => { state = (state * 1664525 + 1013904223) % 4294967296; return state / 4294967296; };
}

async function loadTerrain() {
  const [meta, buffer] = await Promise.all([
    fetch("/PLaiJeju/static/jeju_terrain.json").then((response) => response.json()),
    fetch("/PLaiJeju/static/jeju_terrain.bin").then((response) => response.arrayBuffer()),
  ]);
  const grid = new Int16Array(buffer), { columns, rows, cell } = meta;
  const width = (columns - 1) * cell, depth = (rows - 1) * cell;

  /** 격자 값을 네 점 사이에서 부드럽게 읽는다. 격자 밖은 먼바다. */
  function raw(x, z) {
    const c = (x + width / 2) / cell, r = (z + depth / 2) / cell;
    if (c < 0 || r < 0 || c > columns - 1 || r > rows - 1) return -30000;
    const c0 = Math.min(columns - 2, Math.floor(c)), r0 = Math.min(rows - 2, Math.floor(r)), u = c - c0, v = r - r0, i = r0 * columns + c0;
    return (grid[i] * (1 - u) + grid[i + 1] * u) * (1 - v) + (grid[i + columns] * (1 - u) + grid[i + columns + 1] * u) * v;
  }
  const chujaRise = (x, z) => { const q = Math.hypot(x - chuja.x, z - chuja.z) / 0.9; return q < 1 ? 0.3 * (1 - q * q) : 0; };

  const geometry = new THREE.PlaneGeometry(width, depth, columns - 1, rows - 1).rotateX(-Math.PI / 2);
  const position = geometry.attributes.position, colors = new Float32Array(position.count * 3), color = new THREE.Color();
  const landColor = ramp(LAND_RAMP), seaColor = ramp(SEA_RAMP), deepSea = new THREE.Color(COLORS.sea);
  for (let i = 0; i < position.count; i++) {   // 꼭짓점 순서가 격자 순서(북→남, 서→동)와 같다
    const value = grid[i];
    position.setY(i, value > 0 ? 0.05 + value * HEIGHT_PER_METER : SEA_LEVEL);
    (value > 0 ? landColor(value, color) : seaColor(-value / 100, color));
    // 격자 끝에서 얕은 바다색이 직선으로 잘리지 않게 먼바다 색으로 녹인다
    const edge = Math.min(width / 2 - Math.abs(position.getX(i)), depth / 2 - Math.abs(position.getZ(i)));
    if (value <= 0) color.lerp(deepSea, 1 - smoothstep(0, 2.2, edge));
    color.toArray(colors, i * 3);
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  const land = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.92 }));
  land.castShadow = land.receiveShadow = true;

  return {
    land,
    isLand: (x, z) => raw(x, z) > 0 || chujaRise(x, z) > 0,
    heightAt: (x, z) => { const value = raw(x, z); return value > 0 ? 0.05 + value * HEIGHT_PER_METER : chujaRise(x, z) || SEA_LEVEL; },
    meters: (x, z) => Math.max(0, raw(x, z)),
    seaDistance: (x, z) => { const value = raw(x, z); return value > 0 ? 0 : -value / 100; },
  };
}

// ---------- 소품 ----------
function windTurbine() {
  const group = new THREE.Group();
  const tower = mesh(new THREE.CylinderGeometry(0.045, 0.09, 1.9, 12), COLORS.white, { roughness: 0.5 });
  tower.position.y = 0.95;
  const rotor = new THREE.Group();
  rotor.position.set(0, 1.9, 0.1);
  rotor.add(mesh(new THREE.SphereGeometry(0.1, 14, 12), COLORS.white));
  for (let i = 0; i < 3; i++) {
    const arm = new THREE.Group();
    const blade = mesh(new THREE.BoxGeometry(0.1, 0.9, 0.025), COLORS.white, { roughness: 0.5 });
    blade.position.y = 0.48;
    arm.add(blade);
    arm.rotation.z = (i * Math.PI * 2) / 3;
    rotor.add(arm);
  }
  group.add(tower, rotor);
  group.userData.rotor = rotor;
  return group;
}

function tangerineTree() {
  const group = new THREE.Group();
  const trunk = mesh(new THREE.CylinderGeometry(0.05, 0.07, 0.3, 8), COLORS.trunk);
  trunk.position.y = 0.15;
  group.add(trunk);
  for (const [x, y, z, r] of [[0, 0.5, 0, 0.3], [0.17, 0.42, 0.08, 0.2], [-0.16, 0.44, -0.06, 0.2]]) {
    const crown = mesh(new THREE.IcosahedronGeometry(r, 2), COLORS.tree);
    crown.position.set(x, y, z);
    group.add(crown);
  }
  for (const [x, y, z] of [[0.22, 0.56, 0.16], [-0.2, 0.5, 0.2], [0.04, 0.7, 0.22], [0.02, 0.4, 0.28]]) {
    const fruit = mesh(new THREE.SphereGeometry(0.065, 10, 8), COLORS.fruit, { cast: false, roughness: 0.5 });
    fruit.position.set(x, y, z);
    group.add(fruit);
  }
  return group;
}

function stoneGrandfather() {
  const group = new THREE.Group();
  const body = mesh(new THREE.CapsuleGeometry(0.2, 0.3, 6, 14), COLORS.stone, { roughness: 1 });
  body.position.y = 0.36;
  const head = mesh(new THREE.SphereGeometry(0.21, 16, 14), COLORS.stone, { roughness: 1 });
  head.position.y = 0.78;
  const hat = mesh(new THREE.CylinderGeometry(0.15, 0.24, 0.15, 16), COLORS.stone, { roughness: 1 });
  hat.position.y = 0.99;
  group.add(body, head, hat);
  return group;
}

function boat() {
  const group = new THREE.Group();
  const hull = mesh(new THREE.CapsuleGeometry(0.16, 0.6, 4, 12), COLORS.white, { roughness: 0.4 });
  hull.rotation.z = Math.PI / 2;
  hull.position.y = -0.04;
  const cabin = mesh(new THREE.BoxGeometry(0.3, 0.2, 0.22), COLORS.navy);
  cabin.position.set(-0.1, 0.18, 0);
  group.add(hull, cabin);
  return group;
}

function cloud() {
  const group = new THREE.Group();
  for (const [x, y, z, r] of [[0, 0, 0, 0.8], [0.9, -0.1, 0.1, 0.58], [-0.85, -0.12, -0.05, 0.52], [0.25, 0.36, -0.1, 0.52], [-0.3, 0.2, 0.2, 0.45]]) {
    const puff = mesh(new THREE.SphereGeometry(r, 20, 16), COLORS.white, { cast: false, receive: false, transparent: true, opacity: 0.94, roughness: 1 });
    puff.position.set(x, y, z);
    group.add(puff);
  }
  return group;
}

function airplane() {
  const group = new THREE.Group();
  const body = mesh(new THREE.CapsuleGeometry(0.12, 0.8, 6, 14), COLORS.white, { roughness: 0.4 });
  body.rotation.z = Math.PI / 2;
  const wing = mesh(new THREE.BoxGeometry(0.22, 0.03, 1.15), COLORS.white, { roughness: 0.4 });
  const tail = mesh(new THREE.BoxGeometry(0.16, 0.3, 0.03), COLORS.up);
  tail.position.set(-0.45, 0.16, 0);
  group.add(body, wing, tail);
  return group;
}

function cruiseShip() {
  const group = new THREE.Group();
  const hull = mesh(new THREE.BoxGeometry(2.6, 0.34, 0.6), COLORS.white, { roughness: 0.45 });
  hull.position.y = 0.05;
  const keel = mesh(new THREE.BoxGeometry(2.62, 0.09, 0.62), COLORS.navy);
  keel.position.y = -0.08;
  const bow = mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.34, 3), COLORS.white, { roughness: 0.45 });
  bow.rotation.y = Math.PI / 2;
  bow.scale.set(1, 1, 0.98);
  bow.position.set(1.3, 0.05, 0);
  group.add(hull, keel, bow);
  for (let deck = 0; deck < 3; deck++) {
    const length = 2.1 - deck * 0.42, width = 0.5 - deck * 0.06, y = 0.31 + deck * 0.19;
    const level = mesh(new THREE.BoxGeometry(length, 0.17, width), COLORS.white, { roughness: 0.4 });
    level.position.set(-0.12 - deck * 0.08, y, 0);
    const windows = mesh(new THREE.BoxGeometry(length + 0.01, 0.045, width + 0.01), 0x6aa6e0, { cast: false, roughness: 0.2 });
    windows.position.set(level.position.x, y + 0.01, 0);
    group.add(level, windows);
  }
  const funnel = mesh(new THREE.CylinderGeometry(0.08, 0.11, 0.32, 14), COLORS.red);
  funnel.position.set(-0.7, 0.96, 0);
  group.add(funnel);
  return group;
}

const clamp01 = (v) => Math.min(1, Math.max(0, v));
const easeOutBack = (k) => { const c = 1.35; return 1 + (c + 1) * Math.pow(k - 1, 3) + c * Math.pow(k - 1, 2); };
const easeInOutCubic = (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);

// 카메라 구도: az = 남쪽에서 동쪽으로 돈 각도, polar = 천정에서 내려온 각도(클수록 낮게 깔림), zoom, target = 바라보는 점
const DISTANCE = 45;
const OPENING = { az: -0.85, polar: 0.52, zoom: 0.6, target: [0, 0, 0] };
const INTRO = { az: -0.5, polar: 0.98, zoom: 0.95, target: [1.5, 0.8, 0.5] };
const SHOTS = [   // 지표 탭 순서와 같다
  { az: 0.22, polar: 0.8, zoom: 1, target: [0, 1.2, 0] },
  { az: -0.32, polar: 0.86, zoom: 1, target: [0, 1.2, 0] },
  { az: 0.42, polar: 0.9, zoom: 1.02, target: [0, 1.2, 0] },
  { az: -0.48, polar: 0.78, zoom: 1, target: [0, 1.2, 0] },
  { az: 0.08, polar: 0.98, zoom: 1.04, target: [0, 1.4, 0.5] },
  { az: 0.58, polar: 0.84, zoom: 1.04, target: [2.5, 1.2, 0] },
  { az: -0.12, polar: 0.66, zoom: 1, target: [0, 1, 0.5] },
  { az: 0.7, polar: 0.92, zoom: 1.06, target: [0, 1.1, 0] },      // 저녁·밤 소비: 해 지는 쪽에서 낮게
  { az: -0.62, polar: 0.74, zoom: 1, target: [0, 1.2, 0.3] },     // 입도객 1인당 소비
  { az: 0.3, polar: 0.7, zoom: 1.12, target: [1, 1, -1] },          // 동네 식당 체감: 제주시 도심 쪽
  { az: -0.2, polar: 0.88, zoom: 0.96, target: [0, 1.2, 0] },      // 도민 가게 몫
  { az: 0.5, polar: 0.76, zoom: 1.08, target: [0.5, 1.1, -0.5] },  // 가게당 관광객 소비
  { az: 0.36, polar: 0.82, zoom: 1.02, target: [0, 1.2, 0.2] },    // 기대 대비 실제
  { az: -0.4, polar: 0.74, zoom: 1.04, target: [0.5, 1.1, 0] },    // 크루즈 효과 보정
  { az: 0.12, polar: 0.7, zoom: 0.98, target: [0, 1.2, 0] },        // 동네 유형
  { az: -0.05, polar: 0.8, zoom: 1, target: [0, 1.2, 0] },         // 주의 지역 종합
];
// 저녁·밤 소비 탭에서 하늘·빛을 해 질 녘으로 바꾼다
const LAMP = new THREE.Color(0xbfe6ff);
const DUSK = { sea: new THREE.Color(0x5a6f9e), sky: new THREE.Color(0x46557e), sun: new THREE.Color(0xb9c9ff), hemiSky: new THREE.Color(0x9fb0e0), hemiGround: new THREE.Color(0x40486a) };
const PORTS = { 제주항: [126.543, 33.523], 강정항: [126.483, 33.229] };

export async function createScene(canvas, { labels }) {
  const [terrain, models] = await Promise.all([loadTerrain(), loadProps()]), { heightAt } = terrain;
  const prop = (name, handMade) => (models[name] ? models[name]() : handMade());   // .glb 가 있으면 그것을, 없으면 직접 만든 소품을 쓴다
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(COLORS.sea);
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 300);
  camera.position.set(8, 34, 29);
  const clock = new THREE.Clock();

  const hemi = new THREE.HemisphereLight(0xffffff, 0xc4dcf2, 1.2);
  scene.add(hemi);
  const DAY = { sky: new THREE.Color(COLORS.sea), sun: new THREE.Color(0xfff8ee), hemiSky: hemi.color.clone(), hemiGround: hemi.groundColor.clone() };
  let dusk = 0, duskGoal = 0, duskShown = 0;
  const sun = new THREE.DirectionalLight(0xfff8ee, 2.0);
  sun.position.set(-20, 30, 14);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -26, right: 26, top: 18, bottom: -18, near: 1, far: 90 });
  sun.shadow.radius = 4;
  sun.shadow.bias = -0.0006;
  sun.shadow.normalBias = 0.04;
  scene.add(sun, sun.target);
  const SUN_OFFSET = sun.position.clone();
  let shadowHalf = 26;

  const controls = new OrbitControls(camera, canvas);
  Object.assign(controls, {
    enablePan: false, enableDamping: true, dampingFactor: 0.08,
    minPolarAngle: 0.5, maxPolarAngle: 1.2, minAzimuthAngle: -0.9, maxAzimuthAngle: 0.9, minZoom: 0.55, maxZoom: 42,
  });
  controls.target.set(0, 1.2, 0);

  // ----- 카메라 연출 -----
  let mode = "intro", shot = 0, flight = null, userHolds = false, driftFrom = 0, opened = false;
  let zoomedTo = null, onZoom = () => {};   // 지명이나 섬을 눌러 당겨 본 곳 (null = 전체)
  const toView = (v) => ({ ...v, target: new THREE.Vector3(...(Array.isArray(v.target) ? v.target : v.target.toArray())) });
  function readCamera() {
    const spherical = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    return { az: spherical.theta, polar: spherical.phi, zoom: camera.zoom, target: controls.target.clone() };
  }
  function applyCamera(v) {
    controls.target.copy(v.target);
    const r = DISTANCE * Math.sin(v.polar);
    camera.position.set(v.target.x + r * Math.sin(v.az), v.target.y + DISTANCE * Math.cos(v.polar), v.target.z + r * Math.cos(v.az));
    camera.zoom = v.zoom;
    camera.updateProjectionMatrix();
    camera.lookAt(v.target);
  }
  function fly(goal, duration = 1.6) {
    const to = toView(goal);
    userHolds = false;
    if (reducedMotion) { applyCamera(to); flight = null; return; }
    flight = { from: readCamera(), to, start: clock.elapsedTime, duration };
  }
  function stepFlight(t) {
    if (!flight) return;
    const k = easeInOutCubic(clamp01((t - flight.start) / flight.duration)), { from, to } = flight;
    applyCamera({
      az: from.az + (to.az - from.az) * k, polar: from.polar + (to.polar - from.polar) * k,
      zoom: Math.exp(Math.log(from.zoom) + (Math.log(to.zoom) - Math.log(from.zoom)) * k), target: from.target.clone().lerp(to.target, k),
    });
    if (k >= 1) { flight = null; driftFrom = t; }
  }
  // 이용자가 섬을 잡으면 연출을 멈춘다
  controls.addEventListener("start", () => { flight = null; userHolds = true; });
  applyCamera(toView(OPENING));

  // ----- 바다와 섬 -----
  const world = new THREE.Group();
  scene.add(world);
  const decor = new THREE.Group();   // 소품. 도심까지 확대하면 실제 건물이 보이도록 숨긴다
  world.add(decor);

  const sea = mesh(new THREE.PlaneGeometry(400, 400).rotateX(-Math.PI / 2), COLORS.sea, { cast: false, roughness: 0.92 });
  sea.position.y = SEA_LEVEL - 0.01;
  const chujaShallow = mesh(new THREE.CircleGeometry(1.5, 40).rotateX(-Math.PI / 2), COLORS.shallow, { cast: false, roughness: 0.92 });
  chujaShallow.position.set(chuja.x, SEA_LEVEL + 0.012, chuja.z);
  const chujaIsland = mesh(new THREE.SphereGeometry(0.9, 36, 18), 0xbfe2b5, { roughness: 0.92 });
  chujaIsland.scale.y = 0.34;
  chujaIsland.position.set(chuja.x, SEA_LEVEL, chuja.z);
  world.add(sea, terrain.land, chujaShallow, chujaIsland);

  /** 바다에 떨어진 좌표를 섬 안쪽으로 조금씩 옮겨 땅 위에 올린다 (손으로 잡은 좌표가 실제 해안과 어긋날 때). */
  function settle(x, z) {
    const spot = new THREE.Vector3(x, 0, z);
    for (let step = 0; step < 80 && !terrain.isLand(spot.x, spot.z); step++) {
      spot.x += (halla.x - spot.x) * 0.03;
      spot.z += (halla.z - spot.z) * 0.03;
    }
    spot.y = heightAt(spot.x, spot.z);
    return spot;
  }

  // 잔물결: 생겼다 사라지는 흰 선
  const ripples = new THREE.InstancedMesh(new THREE.CapsuleGeometry(0.035, 1, 2, 6).rotateZ(Math.PI / 2), new THREE.MeshBasicMaterial({ color: COLORS.foam, transparent: true, opacity: 0.75 }), 70);
  const rippleSpots = [];
  for (const next = random(11); rippleSpots.length < ripples.count;) {
    const x = (next() - 0.5) * 70, z = (next() - 0.5) * 44;
    if (terrain.seaDistance(x, z) > 1.6) rippleSpots.push({ x, z, phase: next() * Math.PI * 2, speed: 0.35 + next() * 0.4, length: 0.5 + next() * 0.9 });
  }
  const rippleMatrix = new THREE.Matrix4();
  function drawRipples(t) {
    rippleSpots.forEach((spot, i) => {
      const life = Math.max(0.001, Math.sin(t * spot.speed + spot.phase));
      rippleMatrix.makeScale(spot.length * life, 1, 1).setPosition(spot.x + Math.sin(t * 0.2 + spot.phase) * 0.4, 0.03, spot.z);
      ripples.setMatrixAt(i, rippleMatrix);
    });
    ripples.instanceMatrix.needsUpdate = true;
  }
  drawRipples(1);
  decor.add(ripples);

  function place(object, lonLat, size = PROP_SIZE) {
    const at = toScene(lonLat), spot = settle(at.x, at.z);
    object.position.set(spot.x, spot.y - 0.02, spot.z);
    object.scale.setScalar(size);
    decor.add(object);
    return object;
  }

  /** 실제 고도를 보고 모델을 흩뿌린다. 같은 모델은 InstancedMesh 하나로 그려 그리기 호출이 늘지 않는다. */
  function scatter(names, count, seed, accept, sizeRange = [0.5, 0.8]) {
    const kinds = names.map((name) => models[name]?.parts).filter(Boolean);
    if (!kinds.length) return;
    const next = random(seed), spots = kinds.map(() => []);
    for (let tries = 0, placed = 0; placed < count && tries < count * 300; tries++) {
      const x = (next() - 0.5) * 45, z = (next() - 0.5) * 29;
      if (!terrain.isLand(x, z) || !accept(terrain.meters(x, z), x, z)) continue;
      const size = sizeRange[0] + next() * (sizeRange[1] - sizeRange[0]);
      const spin = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), next() * Math.PI * 2);
      spots[placed++ % kinds.length].push(new THREE.Matrix4().compose(new THREE.Vector3(x, heightAt(x, z) - 0.02, z), spin, new THREE.Vector3(size, size, size)));
    }
    kinds.forEach((parts, index) => {
      for (const part of parts) {
        const instances = new THREE.InstancedMesh(part.geometry, part.material, spots[index].length);
        spots[index].forEach((spot, i) => instances.setMatrixAt(i, spot.clone().multiply(part.matrix)));
        instances.castShadow = instances.receiveShadow = true;
        decor.add(instances);
      }
    });
  }
  scatter(["forestPineRound", "forestPineTall"], 300, 21, (m) => m > 620 && m < 1320, [0.5, 0.78]);   // 한라산 숲
  scatter(["forestBroad", "forestOak"], 300, 22, (m) => m > 200 && m < 700, [0.42, 0.7]);             // 중산간 숲
  scatter(["bush", "bushDetailed"], 260, 23, (m) => m > 15 && m < 220, [0.2, 0.36]);                  // 낮은 들판
  scatter(["palm"], 26, 24, (m, x, z) => z > 5 && m > 3 && m < 70, [0.62, 0.9]);                       // 남쪽 해안 야자수
  scatter(["basaltLarge", "basaltWide", "basaltSmall"], 170, 25, (m) => m < 6, [0.06, 0.14]);          // 해안 현무암

  // ----- 제주다운 소품 -----
  const fruitGeometry = new THREE.SphereGeometry(0.065, 10, 8);
  const turbines = [[126.19, 33.33], [126.215, 33.375], [126.80, 33.548], [126.865, 33.53], [126.895, 33.405], [126.26, 33.215]].map((p) => place(prop("windTurbine", windTurbine), p));
  [[126.60, 33.275], [126.665, 33.285], [126.50, 33.262], [126.445, 33.275], [126.745, 33.30], [126.385, 33.275], [126.555, 33.285], [126.70, 33.32], [126.63, 33.30]].forEach((p) => {
    const tree = place(prop("tangerineTree", tangerineTree), p, PROP_SIZE * 1.15);
    if (!models.tangerineTree) return;   // 받은 나무 모델에 감귤을 단다
    for (const [x, y, z] of [[0.3, 0.55, 0.1], [-0.27, 0.6, 0.14], [0.08, 0.76, 0.24], [0.1, 0.5, 0.31], [-0.12, 0.42, 0.28], [0.2, 0.74, -0.14]]) {
      const fruit = mesh(fruitGeometry, COLORS.fruit, { cast: false, roughness: 0.5 });
      fruit.position.set(x, y, z);
      tree.add(fruit);
    }
  });
  [[126.49, 33.507], [126.60, 33.522]].forEach((p) => place(prop("stoneGrandfather", stoneGrandfather), p, PROP_SIZE));
  const lighthouse = place(mesh(new THREE.CylinderGeometry(0.06, 0.1, 0.6, 12).translate(0, 0.3, 0), COLORS.white, { roughness: 0.5 }), [126.965, 33.512]);
  const lamp = mesh(new THREE.ConeGeometry(0.1, 0.16, 12), COLORS.red, { cast: false });
  lamp.position.y = 0.68;
  lighthouse.add(lamp);

  const boats = [[126.30, 33.545, 0.4], [126.72, 33.205, -0.5], [127.03, 33.37, 1.2], [126.10, 33.25, 2.4]].map(([lon, lat, turn]) => {
    const vessel = prop("boat", boat);
    const at = toScene([lon, lat]);
    vessel.position.set(at.x, 0.05, at.z);
    vessel.rotation.y = turn;
    vessel.scale.setScalar(PROP_SIZE * 1.1);
    decor.add(vessel);
    return vessel;
  });
  const clouds = [[-20, 5, 16], [10, 5.5, 17], [26, 5, 15], [-8, 5, -9], [16, 5.5, -10]].map(([x, y, z], index) => {
    const puff = cloud();
    puff.position.set(x, y, z);
    puff.scale.setScalar(1.0 + (index % 3) * 0.2);
    decor.add(puff);
    return puff;
  });
  const cruisers = [[126.548, 33.54, 0.25], [126.47, 33.205, -0.15]].map(([lon, lat, turn]) => {
    const ship = cruiseShip();
    const at = toScene([lon, lat]);
    ship.position.set(at.x, 0.06, at.z);
    ship.rotation.y = turn;
    ship.scale.setScalar(PROP_SIZE * 0.85);
    decor.add(ship);
    return ship;
  });
  const plane = prop("airplane", airplane);
  plane.scale.setScalar(PROP_SIZE * 1.2);
  decor.add(plane);

  // ----- 도심의 실제 건물·도로 (OpenStreetMap). 섬이 먼저 뜨도록 나중에 불러온다 -----
  let city = null;
  fetch("/PLaiJeju/static/jeju_city.json").then((response) => (response.ok ? response.json() : null)).then((data) => {
    if (!data) return;
    city = buildCity(data, { scale: SCALE, latStretch: LAT_STRETCH, heightAt });
    world.add(city.group);
    assignOwners();
    tintCity();
    makeLandmarkPins(city.landmarks);
  }).catch(() => {});

  // ----- 랜드마크 이름표: 확대하면 보이고, 누르면 설명이 펼쳐지며 그곳으로 날아간다 -----
  let landmarkPins = [], openPin = null;
  const legend = document.createElement("div");
  legend.className = "landmark-legend";
  legend.append(...Object.values(LANDMARK_KINDS).map((kind) => {
    const item = document.createElement("span");
    item.style.setProperty("--c", kind.css);
    item.textContent = kind.label;
    return item;
  }));
  labels.append(legend);
  function makeLandmarkPins(list) {
    landmarkPins = list.map((landmark) => {
      const kind = LANDMARK_KINDS[landmark.kind];
      const element = document.createElement("button");
      element.type = "button";
      element.className = `landmark landmark--${landmark.kind}`;
      element.style.setProperty("--c", kind.css);
      const name = document.createElement("b"), note = document.createElement("span"), tag = document.createElement("small");
      name.textContent = landmark.name;
      tag.textContent = kind.label;
      note.textContent = landmark.note;
      note.className = "landmark__note";
      note.prepend(tag);
      element.append(name, note);
      labels.append(element);
      const pin = { element, landmark, at: new THREE.Vector3(landmark.x, landmark.y + 0.02, landmark.z) };
      element.addEventListener("click", () => {
        if (openPin === pin) { openPin = null; element.classList.remove("is-open"); return; }
        if (openPin) openPin.element.classList.remove("is-open");
        openPin = pin;
        element.classList.add("is-open");
        const ground = new THREE.Vector3(landmark.x, Math.max(0.05, heightAt(landmark.x, landmark.z)), landmark.z);
        const inCity = city.contains(landmark.x, landmark.z);
        zoomTo({ az: readCamera().az, polar: 0.74, zoom: inCity ? Math.max(camera.zoom, 17) : 9, target: ground }, landmark.name);
      });
      return pin;
    });
  }
  const cityShade = (zoom) => smoothstep(4.5, 9, zoom);   // 0 = 섬 전체, 1 = 도심 거리

  // ----- 지표 표시: 3D 기둥 + 지도 라벨 -----
  // 기둥의 높이와 색이 값의 크기와 방향이다. 라벨은 HTML 이라 글자가 선명하고, 겹치면 변화가 큰 지역부터 보인다.
  const pillarGeometry = new THREE.CylinderGeometry(1, 1, 1, 40, 1).translate(0, 0.5, 0);
  const capGeometry = new THREE.CylinderGeometry(1.1, 1.1, 0.07, 40).translate(0, 0.035, 0);
  const ringGeometry = new THREE.RingGeometry(1.25, 1.6, 48).rotateX(-Math.PI / 2);
  const stubGeometry = new THREE.CircleGeometry(1, 32).rotateX(-Math.PI / 2);
  const beaconGeometry = new THREE.RingGeometry(1.0, 1.16, 56).rotateX(-Math.PI / 2);   // 주의 지역: 땅에서 퍼져 나가는 신호
  const ALERT_COLOR = 0xe8573f;
  const markersGroup = new THREE.Group();
  world.add(markersGroup);
  const shareLow = new THREE.Color(COLORS.shareLow), shareHigh = new THREE.Color(COLORS.shareHigh), white = new THREE.Color(COLORS.white);
  const riskLow = new THREE.Color(COLORS.riskLow), riskHigh = new THREE.Color(COLORS.riskHigh);
  const COLUMN_MAX = 4.4;
  let markers = [], leaving = [], hovered = null, selected = null, onSelect = () => {};

  function toneColor(item) {
    if (item.tone === "share") return shareLow.clone().lerp(shareHigh, item.shade);
    if (item.tone === "risk") return riskLow.clone().lerp(riskHigh, item.shade);
    if (item.tone === "category") return new THREE.Color(item.color);   // 동네 유형: 유형마다 정한 색   // 주의 지역 종합: 걸린 지표가 많을수록 진한 산호색
    return new THREE.Color(COLORS[item.tone]);
  }

  function styleMarker(marker) {
    marker.element.classList.toggle("is-selected", marker.key === selected);
    marker.element.classList.toggle("is-hovered", marker.key === hovered && marker.key !== selected);
  }

  function makePillar(item, color, radius) {
    const group = new THREE.Group();
    if (item.tone === "hidden") {
      const stub = new THREE.Mesh(stubGeometry, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4 }));
      stub.scale.setScalar(radius * 0.9);
      stub.position.y = 0.05;
      group.add(stub);
      return { group, body: null, cap: null, ring: null };
    }
    const body = new THREE.Mesh(pillarGeometry, new THREE.MeshStandardMaterial({ color, roughness: 0.32, metalness: 0.06, emissive: color, emissiveIntensity: 0.1, transparent: true, opacity: 0.96 }));
    body.castShadow = body.receiveShadow = true;
    body.scale.set(radius, 0.001, radius);
    const cap = new THREE.Mesh(capGeometry, new THREE.MeshStandardMaterial({ color: color.clone().lerp(white, 0.5), roughness: 0.25, emissive: color, emissiveIntensity: 0.05, transparent: true }));
    cap.scale.set(radius, 1, radius);
    const ring = new THREE.Mesh(ringGeometry, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0, depthWrite: false }));
    ring.scale.setScalar(radius);
    ring.position.y = 0.06;
    ring.renderOrder = 2;
    group.add(body, cap, ring);
    const beacons = item.alert ? [0, 1].map((i) => {
      const beacon = new THREE.Mesh(beaconGeometry, new THREE.MeshBasicMaterial({ color: ALERT_COLOR, transparent: true, opacity: 0, depthWrite: false }));
      beacon.position.y = 0.08 + i * 0.01;
      beacon.renderOrder = 3;
      beacon.userData.phase = i * 0.5;
      group.add(beacon);
      return beacon;
    }) : [];
    return { group, body, cap, ring, beacons };
  }

  /** items: [{ key, name, valueText, lonLat, height(0~1), tone, shade(0~1), width }]
   *  options: { shot: 지표 탭 번호(카메라 구도), effect: "drops" | "flows" | null } */
  function setColumns(items, { shot: nextShot, effect = null } = {}) {
    const now = clock.elapsedTime;
    for (const marker of markers) { marker.element.remove(); marker.leaveAt = now; leaving.push(marker); }
    hovered = null;
    const tallest = Math.max(0.0001, ...items.filter((i) => i.tone !== "hidden").map((i) => i.height));
    markers = items.map((item) => {
      const at = toScene(item.lonLat), spot = settle(at.x, at.z);
      const color = toneColor(item);
      const radius = Math.max(0.2, item.width * (item.width >= 2 ? 0.27 : 0.32));
      const height = item.tone === "hidden" ? 0 : 0.25 + (item.height / tallest) * COLUMN_MAX * (item.width >= 2 ? 1 : 0.82);
      const parts = makePillar(item, color, radius);
      parts.group.position.copy(spot);
      if (parts.body) parts.body.userData.key = item.key;
      markersGroup.add(parts.group);

      const element = document.createElement("button");
      element.type = "button";
      element.className = `marker marker--${item.tone}${item.alert ? " is-alert" : ""}`;
      element.innerHTML = '<i class="marker__dot"></i><b></b><span></span>';
      if (item.alert) element.querySelector(".marker__dot").textContent = "!";
      else element.querySelector(".marker__dot").style.background = `#${color.getHexString()}`;
      element.querySelector("b").textContent = item.name;
      element.querySelector("span").textContent = item.valueText;
      element.addEventListener("pointerenter", () => { hovered = item.key; markers.forEach(styleMarker); });
      element.addEventListener("pointerleave", () => { hovered = null; markers.forEach(styleMarker); });
      element.addEventListener("click", () => select(item.key === selected ? null : item.key));
      labels.append(element);

      // 한라산에서 바깥으로 파도처럼 차례로 솟는다
      const delay = 0.12 + Math.hypot(spot.x - halla.x, spot.z - halla.z) * 0.045;
      const marker = { key: item.key, item, element, ...parts, radius, height, spot, born: now, delay, grown: 0, anchor: new THREE.Vector3(spot.x, spot.y + 0.1, spot.z), size: null, priority: item.tone === "hidden" ? -1 : item.alert ? 100 - item.alert : item.height };
      styleMarker(marker);
      return marker;
    });
    setEffect(effect);
    assignOwners();
    if (nextShot !== undefined && nextShot !== shot) {
      shot = nextShot;
      if (mode === "data" && !markers.some((m) => m.key === selected)) fly(SHOTS[shot % SHOTS.length], 1.5);
    }
    select(markers.some((m) => m.key === selected) ? selected : null, false, false);
  }

  /** 건물을 가까운 동네 기둥에 나눠 둔다 (법정동·행정동 단위일 때만). 동 경계가 아니라 가까운 동 중심 기준이라 대략이다. */
  function assignOwners() {
    if (!city) return;
    const local = markers.filter((m) => m.item.width < 2);   // 비공개 동도 넣어야 그 동 건물이 옆 동 색으로 물들지 않는다
    city.assign(local.map((m) => ({ key: m.key, x: m.spot.x, z: m.spot.z })), 0.5);
  }
  function tintCity() {
    if (!city) return;
    const marker = markers.find((m) => m.key === selected);
    const color = marker && marker.body ? marker.body.material.color : null;
    city.tint((owner) => (color && owner === selected ? color : null));
  }

  function focusGoal(marker) {
    const current = readCamera();
    const downtown = city && marker.item.width < 2 && city.contains(marker.spot.x, marker.spot.z);
    if (downtown) return { az: current.az, polar: 0.64, zoom: 15, target: marker.spot.clone() };   // 도심이면 거리가 보일 만큼 내려가되, 한라산이 앞을 가리지 않게 위에서 본다
    return { az: current.az, polar: 0.92, zoom: marker.item.width >= 2 ? 1.75 : 2.7, target: marker.spot.clone().add(new THREE.Vector3(0, Math.max(0.8, marker.height * 0.5), 0)) };
  }

  /** 지정한 구도로 날아가 머문다. 시작 화면의 흔들림도 멈춘다. name 은 되돌아가기 단추에 쓴다. */
  function zoomTo(goal, name) {
    zoomedTo = name;
    fly(goal, 1.35);
    onZoom(name);
  }
  function resetZoom() {
    if (!zoomedTo) return;
    zoomedTo = null;
    if (openPin) { openPin.element.classList.remove("is-open"); openPin = null; }
    fly(mode === "intro" ? INTRO : SHOTS[shot % SHOTS.length], 1.4);
    onZoom(null);
  }
  window.addEventListener("keydown", (event) => { if (event.key === "Escape") resetZoom(); });

  function select(key, notify = true, move = true) {
    const changed = key !== selected;
    if (key && zoomedTo) { zoomedTo = null; onZoom(null); }
    selected = key;
    markers.forEach(styleMarker);
    if (changed) tintCity();
    if (move && changed && mode === "data") {
      const marker = markers.find((m) => m.key === key);
      fly(marker ? focusGoal(marker) : SHOTS[shot % SHOTS.length], 1.25);
    }
    if (notify) onSelect(key);
  }

  // ----- 지표별 효과: 떨어지는 동전(낙수 지수), 항구에서 흐르는 빛(크루즈) -----
  const coins = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.13, 0.13, 0.035, 20), new THREE.MeshStandardMaterial({ color: 0xcfe7ff, metalness: 0.55, roughness: 0.25, emissive: 0x2a6fdb, emissiveIntensity: 0.25 }), 160);
  coins.count = 0;
  coins.castShadow = true;
  world.add(coins);
  const flowsGroup = new THREE.Group();
  world.add(flowsGroup);
  const sparks = new THREE.InstancedMesh(new THREE.SphereGeometry(0.16, 14, 12), new THREE.MeshBasicMaterial({ color: 0x8fe0ff }), 200);
  sparks.count = 0;
  world.add(sparks);
  const ports = Object.entries(PORTS).map(([name, lonLat]) => { const at = toScene(lonLat); return { name, at: new THREE.Vector3(at.x, 0.4, at.z) }; });
  let coinSpots = [], streams = [];
  const effectMatrix = new THREE.Matrix4(), effectSpin = new THREE.Quaternion(), effectScale = new THREE.Vector3(), effectAt = new THREE.Vector3(), upAxis = new THREE.Vector3(1, 0, 0);

  function topMarkers(limit) {
    return markers.filter((m) => m.body && m.item.tone !== "down").sort((a, b) => b.height - a.height).slice(0, limit);
  }

  function setEffect(effect) {
    duskGoal = effect === "dusk" ? 1 : 0;
    if (reducedMotion) dusk = duskGoal;
    coinSpots = [];
    streams = [];
    flowsGroup.clear();
    coins.count = sparks.count = 0;
    if (reducedMotion || !effect) return;
    const next = random(7);
    if (effect === "drops") {
      const targets = topMarkers(12), total = targets.reduce((sum, m) => sum + m.height ** 1.5, 0) || 1;
      for (const marker of targets) {
        const share = Math.max(2, Math.round((marker.height ** 1.5 / total) * coins.instanceMatrix.count));
        for (let i = 0; i < share && coinSpots.length < coins.instanceMatrix.count; i++) {
          coinSpots.push({ marker, phase: next(), speed: 0.32 + next() * 0.22, dx: (next() - 0.5) * marker.radius * 1.2, dz: (next() - 0.5) * marker.radius * 1.2, spin: next() * 6 });
        }
      }
      coins.count = coinSpots.length;
    }
    if (effect === "flows") {
      const targets = topMarkers(14);
      for (const marker of targets) {
        const port = ports.reduce((best, p) => (p.at.distanceTo(marker.spot) < best.at.distanceTo(marker.spot) ? p : best));
        const end = marker.spot.clone().add(new THREE.Vector3(0, marker.height + 0.1, 0));
        const middle = port.at.clone().lerp(end, 0.5).add(new THREE.Vector3(0, 2.2 + port.at.distanceTo(end) * 0.28, 0));
        const curve = new THREE.QuadraticBezierCurve3(port.at.clone(), middle, end);
        const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 56, 0.06, 8), new THREE.MeshBasicMaterial({ color: 0x2a78d6, transparent: true, opacity: 0, depthWrite: false }));
        flowsGroup.add(tube);
        const dots = Math.max(3, Math.round(3 + (marker.height / COLUMN_MAX) * 8));
        for (let i = 0; i < dots && streams.length < sparks.instanceMatrix.count; i++) streams.push({ curve, tube, marker, phase: i / dots + next() * 0.05, speed: 0.22 + next() * 0.08 });
      }
      sparks.count = streams.length;
    }
  }

  function drawEffects(t) {
    for (let i = 0; i < coinSpots.length; i++) {
      const c = coinSpots[i], top = c.marker.spot.y + c.marker.body.scale.y + 0.1;
      const cycle = (t * c.speed + c.phase) % 1, ready = c.marker.grown > 0.95;
      const landing = cycle > 0.9 ? (1 - cycle) / 0.1 : 1;
      effectAt.set(c.marker.spot.x + c.dx, top + (1 - cycle) * 7, c.marker.spot.z + c.dz);
      effectSpin.setFromAxisAngle(upAxis, t * 3 + c.spin);
      effectScale.setScalar(ready ? landing : 0);
      coins.setMatrixAt(i, effectMatrix.compose(effectAt, effectSpin, effectScale));
    }
    if (coinSpots.length) coins.instanceMatrix.needsUpdate = true;
    for (let i = 0; i < streams.length; i++) {
      const s = streams[i], ready = s.marker.grown > 0.95;
      s.tube.material.opacity += ((ready ? 0.55 : 0) - s.tube.material.opacity) * 0.08;
      s.curve.getPoint((t * s.speed + s.phase) % 1, effectAt);
      effectScale.setScalar(ready ? 1 : 0);
      sparks.setMatrixAt(i, effectMatrix.compose(effectAt, effectSpin.identity(), effectScale));
    }
    if (streams.length) sparks.instanceMatrix.needsUpdate = true;
  }

  // ----- 기둥 고르기: 마우스를 올리면 강조, 누르면 선택 -----
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
  let pointerDown = null, pick = false;
  canvas.addEventListener("pointermove", (event) => {
    const rect = canvas.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    pick = true;
  });
  canvas.addEventListener("pointerleave", () => { if (hovered) { hovered = null; markers.forEach(styleMarker); } });
  canvas.addEventListener("pointerdown", (event) => { pointerDown = [event.clientX, event.clientY]; });
  canvas.addEventListener("pointerup", (event) => {
    const moved = pointerDown ? Math.hypot(event.clientX - pointerDown[0], event.clientY - pointerDown[1]) : 99;
    if (moved >= 5) return;
    if (hovered) select(hovered === selected ? null : hovered);
    else if (selected) select(null);
    else if (mode === "intro") zoomAtPointer(event);   // 시작 화면: 섬을 누르면 그곳으로 당겨 본다
  });
  canvas.addEventListener("dblclick", (event) => { if (!hovered) zoomAtPointer(event); });   // 진단 화면: 두 번 누르면 당겨 본다

  /** 화면의 한 점 아래 땅으로 확 당겨 들어간다. 도심이면 실제 건물이 보일 만큼, 아니면 지형이 보일 만큼. */
  function zoomAtPointer(event) {
    const rect = canvas.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObject(terrain.land, false)[0];
    if (!hit) return;
    const at = world.worldToLocal(hit.point.clone());
    if (!terrain.isLand(at.x, at.z)) return;
    const current = readCamera(), downtown = city && city.contains(at.x, at.z);
    const zoom = downtown ? Math.max(11, Math.min(18, current.zoom * 2.6)) : Math.min(8, Math.max(3.2, current.zoom * 2.6));
    zoomTo({ az: current.az, polar: downtown ? 0.72 : 0.8, zoom, target: at }, downtown ? "도심" : "이곳");
  }
  function pickPillar() {
    if (!pick) return;
    pick = false;
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(markers.filter((m) => m.body).map((m) => m.body), false)[0];
    const key = hit ? hit.object.userData.key : null;
    if (key !== hovered) { hovered = key; markers.forEach(styleMarker); }
    canvas.style.cursor = hovered ? "pointer" : "";
  }

  // ----- 화면 좌표 -----
  const inset = [0, 0], insetGoal = [0, 0];
  const projected = new THREE.Vector3();
  function toScreen(local) {
    projected.copy(local).applyMatrix4(world.matrixWorld).project(camera);
    return [(projected.x * 0.5 + 0.5) * canvas.clientWidth, (-projected.y * 0.5 + 0.5) * canvas.clientHeight];
  }

  /** 라벨 배치: 선택·마우스·변화가 큰 순서로 놓고, 이미 놓인 라벨과 겹치면 숨긴다. 확대할수록 더 많이 보인다. */
  function layoutLabels() {
    const limit = Math.round(16 * camera.zoom * camera.zoom);
    const order = [...markers].sort((a, b) => (b.key === selected) - (a.key === selected) || (b.key === hovered) - (a.key === hovered) || b.priority - a.priority);
    const placed = [];
    let shown = 0;
    for (const marker of order) {
      const [x, y] = toScreen(marker.anchor);
      marker.size ??= [marker.element.offsetWidth, marker.element.offsetHeight];
      const [w, h] = marker.size;
      const box = [x - w / 2 - 3, y - h - 10, x + w / 2 + 3, y + 1];
      const pinned = marker.key === selected || marker.key === hovered;
      const inside = x > 0 && x < canvas.clientWidth && y > 0 && y < canvas.clientHeight;
      const free = !placed.some((o) => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1]);
      const visible = marker.grown > 0.6 && inside && (pinned || (free && shown < limit));
      if (visible) { placed.push(box); shown += 1; }
      marker.element.style.transform = `translate(${x}px, ${y}px) translate(-50%, -100%)`;
      marker.element.classList.toggle("is-hidden", !visible);
    }
    return placed;
  }

  // [이름, 라벨 위치, 종류, 눌렀을 때 볼 곳(없으면 라벨 위치), 확대, 내려다보는 각도]
  const placeNames = [
    ["제주시", [126.53, 33.525], "city", [126.522, 33.502], 11, 0.72], ["서귀포시", [126.56, 33.232], "city", [126.562, 33.250], 13, 0.72],
    ["한라산", HALLASAN, "peak", null, 3.4, 0.98],
    ["성산일출봉", [126.942, 33.458], "spot", null, 5.5, 0.82], ["우도", ISLETS["우도"], "spot", null, 5.5, 0.8], ["산방산", [126.313, 33.241], "spot", null, 5.5, 0.86],
    ["추자도", ISLETS["추자도"], "spot", null, 4.5, 0.8], ["제주국제공항", [126.493, 33.507], "spot", null, 8, 0.66],
    ["함덕해수욕장", [126.669, 33.543], "spot", null, 6, 0.8], ["협재해수욕장", [126.240, 33.394], "spot", null, 6, 0.8],
    ["중문관광단지", [126.412, 33.248], "spot", [126.420, 33.251], 13, 0.72], ["표선해수욕장", [126.840, 33.325], "spot", null, 6, 0.8],
    ["제주항 크루즈터미널", [126.548, 33.548], "port", [126.5446, 33.5258], 16, 0.72], ["강정항 크루즈터미널", [126.4774, 33.2245], "port", [126.4774, 33.2284], 16, 0.72],
  ].map(([text, lonLat, kind, lookAt, zoom, polar]) => {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = text;
    element.className = `place place--${kind}`;
    element.title = `${text} 가까이 보기`;
    labels.append(element);
    const at = toScene(lonLat), look = toScene(lookAt || lonLat);
    const target = new THREE.Vector3(look.x, Math.max(0.05, heightAt(look.x, look.z)), look.z);
    element.addEventListener("click", () => {
      if (zoomedTo === text) { resetZoom(); return; }
      zoomTo({ az: readCamera().az, polar, zoom, target }, text);
    });
    return { element, at: new THREE.Vector3(at.x, Math.max(0.3, heightAt(at.x, at.z)) + 0.25, at.z) };
  });

  function project() {
    const width = canvas.clientWidth, height = canvas.clientHeight;
    const aspect = width / height, free = Math.max(0.35, (width - inset[0] - inset[1]) / width);
    const half = Math.max(MIN_VIEW_HEIGHT, ISLAND_SPAN / (free * aspect)) / 2;   // 패널이 가리지 않는 폭에 섬이 꽉 차게
    Object.assign(camera, { left: -half * aspect, right: half * aspect, top: half, bottom: -half });
    camera.setViewOffset(width, height, (inset[1] - inset[0]) / 2, -height * 0.035, width, height);
    camera.updateProjectionMatrix();
  }
  function resize() {
    renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
    project();
  }
  new ResizeObserver(resize).observe(canvas);

  // ----- 매 프레임 -----
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
    if (!reducedMotion) {
      turbines.forEach((turbine, i) => { if (turbine.userData.rotor) turbine.userData.rotor.rotation.z -= dt * (1.1 + i * 0.15); });
      boats.forEach((vessel, i) => { vessel.position.y = 0.05 + Math.sin(t * 1.3 + i * 2) * 0.05; vessel.rotation.z = Math.sin(t * 1.1 + i) * 0.06; });
      cruisers.forEach((ship, i) => { ship.position.y = 0.06 + Math.sin(t * 0.8 + i * 3) * 0.03; ship.rotation.x = Math.sin(t * 0.7 + i) * 0.015; });
      clouds.forEach((puff, i) => { puff.position.x += dt * (0.22 + i * 0.04); if (puff.position.x > 34) puff.position.x = -34; });
      drawRipples(t);
      const angle = t * 0.15;
      plane.position.set(Math.cos(angle) * 21, 6.5 + Math.sin(t * 0.5) * 0.3, Math.sin(angle) * 17 + 3);
      plane.rotation.y = -angle - Math.PI / 2;
    } else {
      plane.position.set(-12, 6.5, -12);
    }

    // 도심까지 확대하면 소품·효과를 숨기고 기둥을 낮춰 실제 건물이 보이게 한다
    const near = cityShade(camera.zoom);
    decor.visible = near < 0.35;
    coins.visible = sparks.visible = flowsGroup.visible = near < 0.4;
    // 기둥: 솟아오르고, 고른 기둥 말고는 흐리게
    for (const marker of markers) {
      const k = reducedMotion ? 1 : clamp01((t - marker.born - marker.delay) / 0.95);
      marker.grown = k;
      if (!marker.body) { marker.group.visible = near < 0.5; continue; }
      const h = Math.max(0.001, marker.height * (reducedMotion ? 1 : easeOutBack(k)) * (1 - 0.93 * near));
      const active = marker.key === selected || marker.key === hovered;
      const dim = selected && marker.key !== selected;
      marker.body.scale.y = h;
      marker.cap.position.y = h;
      const inCity = near > 0.5;   // 도시 모드에서는 기둥 대신 라벨과 건물 색으로 보여준다
      marker.body.visible = marker.cap.visible = marker.ring.visible = !inCity;
      marker.body.material.opacity += ((dim ? 0.32 : 0.96) * (1 - 0.55 * near) - marker.body.material.opacity) * 0.15;
      marker.cap.material.opacity = marker.body.material.opacity;
      marker.body.material.emissiveIntensity += ((active ? 0.42 : 0.1 + 0.3 * dusk) - marker.body.material.emissiveIntensity) * 0.15;
      if (dusk > 0 || marker.cap.material.emissiveIntensity > 0.05) {   // 해 질 녘에는 기둥 머리가 등불처럼 켜진다
        marker.cap.material.emissive.copy(marker.body.material.color).lerp(LAMP, dusk);
        marker.cap.material.emissiveIntensity = 0.05 + 1.3 * dusk;
      }
      const pulse = marker.key === selected ? 0.55 + Math.sin(t * 4) * 0.25 : active ? 0.5 : 0;
      marker.ring.material.opacity += (pulse - marker.ring.material.opacity) * 0.2;
      marker.ring.scale.setScalar(marker.radius * (marker.key === selected ? 1 + ((t * 0.8) % 1) * 0.6 : 1));
      marker.anchor.set(marker.spot.x, marker.spot.y + h + 0.18, marker.spot.z);
      for (const beacon of marker.beacons) {   // 주의 지역: 두 겹의 고리가 번갈아 퍼지며 옅어진다
        const phase = reducedMotion ? 0.35 : (t * 0.55 + beacon.userData.phase) % 1;
        beacon.visible = !inCity;
        beacon.scale.setScalar(marker.radius * (1.05 + phase * 2.2));
        beacon.material.opacity = (reducedMotion ? 0.5 : (1 - phase) * 0.75) * k * (dim ? 0.4 : 1);
      }
    }
    // 바뀌기 전 기둥은 가라앉아 사라진다
    leaving = leaving.filter((marker) => {
      const k = reducedMotion ? 1 : clamp01((t - marker.leaveAt) / 0.35);
      if (marker.body) { marker.body.scale.y = Math.max(0.001, marker.body.scale.y * (1 - k)); marker.cap.position.y = marker.body.scale.y; marker.body.material.opacity *= 1 - k * 0.5; }
      if (k >= 1) { markersGroup.remove(marker.group); return false; }
      return true;
    });
    drawEffects(t);
    if (dusk !== duskGoal || duskShown !== dusk) {
      dusk += (duskGoal - dusk) * Math.min(1, dt * 2.5);
      if (Math.abs(dusk - duskGoal) < 0.002) dusk = duskGoal;
      scene.background.copy(DAY.sky).lerp(DUSK.sky, dusk);
      sea.material.color.copy(DAY.sky).lerp(DUSK.sea, dusk);
      sun.color.copy(DAY.sun).lerp(DUSK.sun, dusk);
      sun.intensity = 2.0 - 1.15 * dusk;
      hemi.color.copy(DAY.hemiSky).lerp(DUSK.hemiSky, dusk);
      hemi.groundColor.copy(DAY.hemiGround).lerp(DUSK.hemiGround, dusk);
      hemi.intensity = 1.2 - 0.7 * dusk;
      duskShown = dusk;
    }

    // 카메라: 연출 비행, 시작 화면에서는 천천히 흔들리며 돈다
    stepFlight(t);
    if (mode === "intro" && !flight && !userHolds && !zoomedTo && !reducedMotion) {
      const drift = t - driftFrom;
      applyCamera(toView({ ...INTRO, az: INTRO.az + Math.sin(drift * 0.11) * 0.42, polar: INTRO.polar - Math.sin(drift * 0.07) * 0.08 }));
    }
    // 패널 폭이 바뀌면 섬이 부드럽게 옮겨 간다
    if (Math.abs(inset[0] - insetGoal[0]) + Math.abs(inset[1] - insetGoal[1]) > 0.5) {
      const k = reducedMotion ? 1 : Math.min(1, dt * 4);
      inset[0] += (insetGoal[0] - inset[0]) * k;
      inset[1] += (insetGoal[1] - inset[1]) * k;
      project();
    }
    controls.update();
    // 그림자: 확대할수록 바라보는 곳 둘레만 촘촘하게 그린다
    const half = Math.min(26, Math.max(0.8, 34 / camera.zoom));
    sun.target.position.copy(controls.target);
    sun.position.copy(controls.target).add(SUN_OFFSET);
    if (Math.abs(half - shadowHalf) / shadowHalf > 0.04) {
      shadowHalf = half;
      Object.assign(sun.shadow.camera, { left: -half, right: half, top: half * 0.7, bottom: -half * 0.7 });
      sun.shadow.camera.updateProjectionMatrix();
    }
    world.updateMatrixWorld();
    pickPillar();
    const taken = layoutLabels();
    for (const place of placeNames) {   // 지명은 지역 라벨에 가리면 숨긴다
      const [x, y] = toScreen(place.at);
      place.size ??= [place.element.offsetWidth, place.element.offsetHeight];
      const [w, h] = place.size;
      const box = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
      const covered = taken.some((o) => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1]);
      place.element.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
      const duplicate = near > 0.25 && landmarkPins.some((pin) => pin.landmark.name === place.element.textContent);   // 같은 이름의 랜드마크 이름표가 뜨면 지명은 숨긴다
      place.element.style.opacity = covered || duplicate ? "0" : "1";
      place.element.style.pointerEvents = covered || duplicate ? "none" : "";
      place.element.classList.toggle("is-current", place.element.textContent === zoomedTo);
    }
    const showPins = near > 0.25;
    legend.classList.toggle("is-shown", showPins && landmarkPins.length > 0);
    const pinBoxes = [];
    for (const pin of [...landmarkPins].sort((a, b) => (b === openPin) - (a === openPin))) {
      const [x, y] = toScreen(pin.at);
      const w = pin.element.offsetWidth || 120, h = pin.element.offsetHeight || 26;
      const box = [x - w / 2, y - h - 14, x + w / 2, y];
      const inside = x > 0 && x < canvas.clientWidth && y > 0 && y < canvas.clientHeight;
      const free = pin === openPin || !pinBoxes.some((o) => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1]);
      const visible = showPins && inside && free;
      if (visible) pinBoxes.push(box);
      pin.element.style.transform = `translate(${x}px, ${y}px) translate(-50%, -100%)`;
      pin.element.classList.toggle("is-hidden", !visible);
    }
    renderer.render(scene, camera);
    if (!canvas.classList.contains("is-ready")) { canvas.classList.add("is-ready"); labels.classList.add("is-ready"); }
    requestAnimationFrame(frame);
  }
  resize();
  requestAnimationFrame(frame);

  return {
    setColumns,
    select: (key) => select(key, false),
    /** 지명이나 섬을 눌러 당겨 볼 때 (이름) / 전체로 돌아올 때 (null) 알려 준다 */
    onZoom: (handler) => { onZoom = handler; },
    resetZoom,
    onSelect: (handler) => { onSelect = handler; },
    /** "intro": 시작 화면(섬을 천천히 돎), "data": 진단 화면(지표 구도로 내려옴) */
    setMode(next) {
      if (next === mode && opened) return;
      mode = next;
      if (zoomedTo) { zoomedTo = null; onZoom(null); }
      if (next === "intro") fly(INTRO, opened ? 2 : 3.4);
      else fly(SHOTS[shot % SHOTS.length], opened ? 2.2 : 2.6);
      opened = true;
    },
    /** 왼쪽·오른쪽 패널이 가리는 폭을 알려 주면 섬을 남은 공간에 맞춰 키우고 가운데로 옮긴다. */
    setInsets(left, right) {
      insetGoal[0] = left;
      insetGoal[1] = right;
      if (!opened) { inset[0] = left; inset[1] = right; project(); }
    },
  };
}
