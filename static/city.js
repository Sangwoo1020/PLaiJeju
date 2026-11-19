// 도심의 실제 건물과 도로 (OpenStreetMap). 확대하면 보이는 "도시 모드"의 재료.
// jeju_city.json 은 tools/build_city.py 가 만든다. 꼭짓점은 (경도 - 중심, 위도 - 중심) × 1e6 정수다.
import * as THREE from "three";

const Q = 1e6;
const METERS_TO_UNITS = 0.000495;   // 가로 1m 가 화면 단위로 얼마인지 (경도 1도 = 46 단위, 북위 33.4도)
const HEIGHT_STRETCH = 1.8;         // 건물은 실제보다 조금 높게 세운다 (지형은 5배라 그대로 쓰면 납작해 보인다)
const COLORS = {
  wall: new THREE.Color(0xe8e3d8), roof: new THREE.Color(0xfbfaf6),
  tallWall: new THREE.Color(0xd5e0ec), tallRoof: new THREE.Color(0xeef3f8),
  road: 0xdfe2e6, mainRoad: 0xf6f7f9,
};
// 주제와 관련된 곳(tools/build_city.py 의 LANDMARKS): 관광 소비 집중, 관문, 동네 상권
export const LANDMARK_KINDS = {
  tourist: { label: "관광 소비가 몰리는 곳", wall: new THREE.Color(0x4b4fd1), roof: new THREE.Color(0xa9abf3), css: "#4b4fd1" },
  gateway: { label: "관광객이 들어오는 관문", wall: new THREE.Color(0x2a6fdb), roof: new THREE.Color(0x9cc3f6), css: "#2a6fdb" },
  local: { label: "도민 상권·전통시장", wall: new THREE.Color(0x22a6c8), roof: new THREE.Color(0x9fe0ef), css: "#1593b5" },
};

/** data → { group, buildings: [{ start, count, x, z, base: Float32Array }], roads, areas(장면 좌표 범위), tint(owner→color) } */
export function buildCity(data, { scale, latStretch, heightAt }) {
  const toXZ = (lonQ, latQ) => [(lonQ / Q) * scale, -(latQ / Q) * scale * latStretch];
  const group = new THREE.Group();

  // ----- 건물: 지붕 + 벽을 하나의 지오메트리로 합친다 -----
  const positions = [], colors = [], buildings = [], byRecord = [];
  const landmarkOf = new Map();
  for (const landmark of data.landmarks || []) for (const index of landmark.buildings) landmarkOf.set(index, landmark.kind);
  const point = (x, y, z, color) => { positions.push(x, y, z); colors.push(color.r, color.g, color.b); };
  data.buildings.forEach((record, index) => {
    const meters = record[0], ring = [];
    for (let i = 1; i < record.length; i += 2) ring.push(toXZ(record[i], record[i + 1]));
    if (ring.length < 3) return;
    let cx = 0, cz = 0, signed = 0;
    ring.forEach(([x, z], i) => { const [nx, nz] = ring[(i + 1) % ring.length]; cx += x; cz += z; signed += x * nz - nx * z; });
    cx /= ring.length; cz /= ring.length;
    const ground = heightAt(cx, cz), bottom = ground - 0.012, top = ground + meters * METERS_TO_UNITS * HEIGHT_STRETCH;
    const kind = LANDMARK_KINDS[landmarkOf.get(index)];
    const tall = meters >= 30, wall = kind ? kind.wall : tall ? COLORS.tallWall : COLORS.wall, roof = kind ? kind.roof : tall ? COLORS.tallRoof : COLORS.roof;
    const shade = 0.94 + ((record[1] * 7 + record[2]) % 13) / 100;   // 같은 색만 반복되지 않게 조금씩 다르게
    const wallColor = wall.clone().multiplyScalar(shade), roofColor = roof.clone().multiplyScalar(Math.min(1, shade + 0.04));
    const start = positions.length / 3;

    const triangles = THREE.ShapeUtils.triangulateShape(ring.map(([x, z]) => new THREE.Vector2(x, z)), []);
    for (const [a, b, c] of triangles) {
      const [ax, az] = ring[a], [bx, bz] = ring[b], [qx, qz] = ring[c];
      const up = (bz - az) * (qx - ax) - (bx - ax) * (qz - az) > 0;   // 지붕 면이 위를 보게
      for (const [x, z] of up ? [ring[a], ring[b], ring[c]] : [ring[a], ring[c], ring[b]]) point(x, top, z, roofColor);
    }
    const leftIsOut = signed < 0;
    ring.forEach(([ax, az], i) => {
      const [bx, bz] = ring[(i + 1) % ring.length];
      const quad = leftIsOut
        ? [[ax, bottom, az], [bx, bottom, bz], [bx, top, bz], [ax, bottom, az], [bx, top, bz], [ax, top, az]]
        : [[ax, bottom, az], [bx, top, bz], [bx, bottom, bz], [ax, bottom, az], [ax, top, az], [bx, top, bz]];
      for (const [x, y, z] of quad) point(x, y, z, wallColor);
    });
    const count = positions.length / 3 - start;
    const building = { start, count, x: cx, z: cz, top, base: Float32Array.from(colors.slice(start * 3, (start + count) * 3)) };
    buildings.push(building);
    byRecord[index] = building;
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  const blocks = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.78, metalness: 0 }));
  blocks.castShadow = blocks.receiveShadow = true;
  group.add(blocks);

  // ----- 도로: 길을 따라 땅 위에 깐 띠 -----
  const roadGroups = { minor: [], major: [] };
  for (const record of data.roads) {
    const width = record[0] * METERS_TO_UNITS * 1.4, line = [];
    for (let i = 1; i < record.length; i += 2) line.push(toXZ(record[i], record[i + 1]));
    const target = record[0] >= 15 ? roadGroups.major : roadGroups.minor;
    const side = line.map((p, i) => {   // 꼭짓점마다 양옆 방향 (앞뒤 구간 방향의 평균)
      const [px, pz] = line[Math.max(0, i - 1)], [nx, nz] = line[Math.min(line.length - 1, i + 1)];
      const dx = nx - px, dz = nz - pz, length = Math.hypot(dx, dz) || 1;
      return [(-dz / length) * width / 2, (dx / length) * width / 2];
    });
    for (let i = 0; i < line.length - 1; i++) {
      const [ax, az] = line[i], [bx, bz] = line[i + 1], [asx, asz] = side[i], [bsx, bsz] = side[i + 1];
      const ay = heightAt(ax, az) + 0.004, by = heightAt(bx, bz) + 0.004;
      target.push(ax - asx, ay, az - asz, bx - bsx, by, bz - bsz, bx + bsx, by, bz + bsz, ax - asx, ay, az - asz, bx + bsx, by, bz + bsz, ax + asx, ay, az + asz);
    }
  }
  for (const [kind, list] of Object.entries(roadGroups)) {
    if (!list.length) continue;
    const roadGeometry = new THREE.BufferGeometry();
    roadGeometry.setAttribute("position", new THREE.Float32BufferAttribute(list, 3));
    roadGeometry.computeVertexNormals();
    const road = new THREE.Mesh(roadGeometry, new THREE.MeshStandardMaterial({
      color: kind === "major" ? COLORS.mainRoad : COLORS.road, roughness: 1, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
    }));
    road.receiveShadow = true;
    road.renderOrder = 1;
    group.add(road);
  }

  const areas = data.areas.map((area) => {
    const [west, north] = toXZ((area.west - 126.555) * Q, (area.north - 33.385) * Q);
    const [east, south] = toXZ((area.east - 126.555) * Q, (area.south - 33.385) * Q);
    return { name: area.name, west, east, north, south };
  });
  const colorAttribute = geometry.attributes.color, tinted = new THREE.Color();

  // 랜드마크 이름표 자리: 건물 하나면 그 지붕 위, 여럿이면 지정 위치에서 가장 높은 지붕 위, 건물이 없으면 땅 위
  const landmarks = (data.landmarks || []).map((landmark) => {
    const list = landmark.buildings.map((i) => byRecord[i]).filter(Boolean);
    const [px, pz] = toXZ(landmark.at[0], landmark.at[1]);
    const x = list.length === 1 ? list[0].x : px, z = list.length === 1 ? list[0].z : pz;
    const y = list.length ? Math.max(...list.map((b) => b.top)) : Math.max(0.05, heightAt(x, z));
    return { name: landmark.name, kind: landmark.kind, note: landmark.note, x, y, z, buildings: list.length };
  });

  return {
    group,
    count: buildings.length,
    landmarks,
    roads: data.roads.length,
    /** 장면 좌표가 도심 범위 안인가 */
    contains: (x, z) => areas.some((a) => x >= a.west && x <= a.east && z >= a.north && z <= a.south),
    /** 건물마다 가장 가까운 지역 열쇠를 정한다. spots: [{ key, x, z }], 가장 가까운 지역이 limit 보다 멀면 null */
    assign(spots, limit = 0.6) {
      for (const building of buildings) {
        let best = null, distance = limit;
        for (const spot of spots) {
          const d = Math.hypot(spot.x - building.x, spot.z - building.z);
          if (d < distance) { distance = d; best = spot.key; }
        }
        building.owner = best;
      }
    },
    /** owner → 색. 정해진 지역의 건물을 그 색으로 물들인다. 나머지는 원래 색. */
    tint(colorOf) {
      for (const building of buildings) {
        const color = building.owner ? colorOf(building.owner) : null;
        for (let i = 0; i < building.count; i++) {
          const k = (building.start + i) * 3;
          tinted.setRGB(building.base[i * 3], building.base[i * 3 + 1], building.base[i * 3 + 2]);
          if (color) tinted.lerp(color, 0.62);
          colorAttribute.array[k] = tinted.r; colorAttribute.array[k + 1] = tinted.g; colorAttribute.array[k + 2] = tinted.b;
        }
      }
      colorAttribute.needsUpdate = true;
    },
  };
}
