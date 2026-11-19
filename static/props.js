// 소품 3D 모델(.glb) 불러오기.
// static/props/manifest.json 에 적힌 모델만 쓰고, 없거나 실패하면 scene.js 의 직접 만든 소품을 그대로 쓴다.
//
// manifest.json 형식:
//   { "tangerineTree": { "file": "tree.glb", "height": 1.0, "rotationY": 0, "rotorNode": "", "tint": "" } }
//   height:    장면에서의 높이. 모델 크기가 제각각이라 이 값에 맞춰 줄이거나 키운다.
//   rotationY: 모델이 바라보는 방향 보정(라디안).
//   rotorNode: 풍력발전기의 회전 부품 이름. 있으면 날개가 돌아간다.
//   tint:      모델 전체를 이 색으로 칠한다 (돌을 제주 현무암 색으로 바꿀 때).
import * as THREE from "three";
import { GLTFLoader } from "/static/vendor/loaders/GLTFLoader.js";

const BASE = "/static/props/";
// Kenney 모델의 청록빛 잎을 제주 풍경에 맞는 초록으로 바꾼다 (재질 이름 기준)
const RECOLOR = { leafsGreen: "#5aa55c", leafsDark: "#3f8a58", grass: "#78b868", woodBark: "#8a6a50", woodBarkDark: "#6f5646" };

/** 바닥 가운데가 원점이고 높이가 height 인 소품으로 맞춘다. */
function normalize(scene, { height = 1, rotationY = 0, tint = "" }) {
  const box = new THREE.Box3().setFromObject(scene);
  const size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
  const scale = height / (size.y || 1);
  scene.position.set(-center.x, -box.min.y, -center.z);
  // 보정은 안쪽 층에 둔다. scene.js 가 바깥 층의 크기·방향을 다시 지정해도 지워지지 않도록.
  const fitted = new THREE.Group();
  fitted.add(scene);
  fitted.scale.setScalar(scale);
  fitted.rotation.y = rotationY;
  const holder = new THREE.Group();
  holder.add(fitted);
  scene.traverse((node) => {
    if (!node.isMesh) return;
    node.castShadow = node.receiveShadow = true;
    if (tint) node.material = new THREE.MeshStandardMaterial({ color: tint, roughness: 0.95, flatShading: true });
    // 금속 값이 비어 있는 모델은 glTF 규칙상 금속으로 읽혀, 주변 반사가 없는 이 장면에서 검게 나온다.
    node.material.metalness = 0;
    if (RECOLOR[node.material.name]) node.material.color.set(RECOLOR[node.material.name]);
    node.material.roughness = Math.max(node.material.roughness, 0.75);
  });
  return holder;
}

/** 인스턴싱에 쓸 부품 목록: 모델을 이루는 메시마다 형상·재질·위치 */
function partsOf(model) {
  model.updateMatrixWorld(true);
  const parts = [];
  model.traverse((node) => {
    if (node.isMesh) parts.push({ geometry: node.geometry, material: node.material, matrix: node.matrixWorld.clone() });
  });
  return parts;
}

/** 쓸 수 있는 모델을 읽어 { 이름: () => 새 복제본 } 으로 돌려준다. 함수의 .parts 는 인스턴싱용 부품이다. */
export async function loadProps() {
  const response = await fetch(`${BASE}manifest.json`).catch(() => null);
  if (!response?.ok) return {};
  const manifest = await response.json();
  const loader = new GLTFLoader();
  const makers = {};
  await Promise.all(Object.entries(manifest).map(async ([name, spec]) => {
    try {
      const model = normalize((await loader.loadAsync(BASE + spec.file)).scene, spec);
      makers[name] = () => {
        const copy = model.clone(true);
        if (spec.rotorNode) {
          const rotor = copy.getObjectByName(spec.rotorNode);
          if (rotor) copy.userData.rotor = rotor;
        }
        return copy;
      };
      makers[name].parts = partsOf(model);
    } catch (error) {
      console.warn(`소품 모델을 읽지 못해 기본 소품을 씁니다: ${spec.file}`, error);
    }
  }));
  return makers;
}
