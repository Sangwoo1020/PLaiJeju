"""제주 도심의 실제 건물·도로를 3D 화면용 파일로 만든다.

사용법:
    python tools/build_city.py            # OpenStreetMap 에서 내려받아(처음 한 번) 변환
    python tools/build_city.py --offline  # data/osm/ 에 받아 둔 파일로만 변환

입력:  OpenStreetMap (Overpass API, https://overpass-api.de) 의 건물 윤곽과 주요 도로.
       © OpenStreetMap 기여자, ODbL 1.0 — 화면과 문서에 출처를 적어야 한다.
       받은 원본은 data/osm/{도심}.json 에 둔다 (제출물에는 넣지 않는다).
출력:  src/golmok/static/jeju_city.json
       {
         "areas": [{"name", "west", "east", "south", "north"}],
         "buildings": [[높이(m), 경도·위도 꼭짓점...]],   꼭짓점은 (경도 - 126.555, 위도 - 33.385) × 1e6 정수
         "roads": [[폭(m), 꼭짓점...]],
         "source": "..."
       }
높이: building:levels × 3.2m, height 태그, 둘 다 없으면 건물 종류와 면적으로 추정한다(추정이라는 점을 화면 설명에 적는다).
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "osm"
OUT = ROOT / "src" / "golmok" / "static" / "jeju_city.json"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
CENTER_LON, CENTER_LAT = 126.555, 33.385     # scene.js 의 CENTER 와 같아야 한다
Q = 1e6

# 도심 범위 (서, 남, 동, 북). 제주시는 커서 네 조각으로 나눠 받는다.
AREAS = {
    "제주시": (126.43, 33.468, 126.60, 33.525),
    "서귀포": (126.535, 33.236, 126.585, 33.268),
    "중문": (126.40, 33.240, 126.445, 33.262),
    "제주항": (126.525, 33.515, 126.560, 33.537),   # 국제여객터미널(크루즈)이 제주시 범위 북쪽 끝 밖에 있다
    "강정": (126.460, 33.215, 126.500, 33.242),     # 서귀포 강정 크루즈터미널
}
# OSM 에 위치 점만 있고 건물 윤곽이 없는 곳은 단순한 상자 모형으로 세운다: (이름, 경도, 위도, 길이 m, 폭 m, 방향 도, 높이 m)
# 강정 크루즈터미널은 OSM 노드(ferry_terminal) 위치에 2층 규모로 세웠다. 모양과 방향은 실제와 다를 수 있다.
MODELS = [
    ("강정 크루즈터미널", 126.47737, 33.22843, 110, 38, 25, 14),
]
# 주제와 관련해 도시 모드에서 강조할 곳: (이름, 분류, 설명, 경도, 위도, OSM 건물 id 목록, 반경 m)
#   분류 tourist = 관광 소비가 몰리는 곳, gateway = 관광객이 들어오는 관문, local = 도민 상권·전통시장
#   건물 id 가 있으면 그 건물을, 없으면 반경 안의 건물을 칠한다. 위치는 OSM 건물 윤곽과 Nominatim 지명 검색(2026-11-12)으로 확인했다.
LANDMARKS = [
    ("제주드림타워", "tourist", "도내 최고층 복합리조트(38층). 호텔·카지노·쇼핑몰이 모여 외국인 관광객 소비가 몰리는 곳입니다.", 126.48135, 33.48534, ["way/842319126"], 0),
    ("신라면세점 제주점", "tourist", "연동 시내면세점. 도의회 행정사무감사에서 '소비가 대기업 면세점에 몰린다'고 지적한 업종입니다.", 126.48750, 33.48624, ["way/374039423"], 0),
    ("누웨마루거리", "tourist", "연동의 외국인 관광객 쇼핑 거리(옛 바오젠거리). 화장품·잡화점이 모여 있습니다.", 126.49088, 33.48893, [], 190),
    ("중문 리조트 호텔", "tourist", "호텔신라·롯데호텔 등 대형 리조트가 모인 중문관광단지. 소비가 단지 밖 동네로 이어지는지 확인할 곳입니다.", 126.40917, 33.24800, ["way/250718035", "way/250718036", "way/250718037"], 0),
    ("제주국제공항", "gateway", "관광객 대부분이 들어오는 관문입니다. 내국인 지정면세점이 있습니다.", 126.49233, 33.50657, ["way/75615560"], 0),
    ("제주항 크루즈터미널", "gateway", "크루즈선이 기항하는 제주항. 내린 관광객이 버스로 면세점·관광지를 도는 동선의 출발점입니다.", 126.54456, 33.52610, ["relation/14745474"], 0),
    ("강정항 크루즈터미널", "gateway", "서귀포 크루즈 기항지. 크루즈 관광객 소비가 강정·서귀포 동네로 이어지는지가 쟁점입니다. 건물은 위치만 맞춘 단순 모형입니다.", 126.47737, 33.22843, ["model/강정 크루즈터미널"], 0),
    ("동문재래시장", "local", "제주시 원도심의 대표 전통시장. 면세점 밖 골목으로 외국인 소비가 흘러오는지 볼 수 있는 곳입니다.", 126.52758, 33.51223, [], 90),
    ("칠성로·중앙지하상가", "local", "원도심 상점가. 신제주(연동)로 옮겨 간 관광 소비와 대비되는 옛 중심 상권입니다.", 126.52400, 33.51400, [], 80),
    ("서귀포매일올레시장", "local", "서귀포 원도심 전통시장. 강정항 크루즈 관광객이 찾는지 볼 곳입니다.", 126.56354, 33.25019, ["way/375049019"], 90),
    ("제주민속오일시장", "local", "닷새마다 서는 도민 장터. 관광객과 도민 소비가 함께 만나는 곳입니다.", 126.47500, 33.49650, ["way/375324661"], 100),
]
ROAD_WIDTH = {"motorway": 24, "trunk": 22, "primary": 20, "secondary": 15, "tertiary": 11, "unclassified": 7, "residential": 6, "living_street": 5}
METERS_PER_DEG_LAT = 110_950


def query(box: tuple[float, float, float, float]) -> str:
    west, south, east, north = box
    bbox = f"{south},{west},{north},{east}"
    roads = "|".join(ROAD_WIDTH)
    return f"""[out:json][timeout:170];
(
  way["building"]({bbox});
  way["highway"~"^({roads})$"]({bbox});
  relation["building"]({bbox});
);
out body geom;"""


def tiles(box, split: int):
    west, south, east, north = box
    dx, dy = (east - west) / split, (north - south) / split
    return [(west + i * dx, south + j * dy, west + (i + 1) * dx, south + (j + 1) * dy) for i in range(split) for j in range(split)]


def download(box, text: str | None = None) -> dict:
    """Overpass 서버가 바쁘면 다른 서버로, 그래도 안 되면 기다렸다가 다시 받는다."""
    body = urllib.parse.urlencode({"data": text or query(box)}).encode()
    for attempt in range(12):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            request = urllib.request.Request(url, data=body, headers={"User-Agent": "jeju-tourism-agent-build/1.0"})
            with urllib.request.urlopen(request, timeout=200) as response:
                data = json.loads(response.read())
            if "elements" in data:
                return data
            raise ValueError(data.get("remark", "응답에 elements 가 없음"))
        except Exception as error:
            print(f"  {url.split('/')[2]}: {str(error)[:80]}, {20 + attempt * 10}초 뒤 다시", file=sys.stderr, flush=True)
            time.sleep(20 + attempt * 10)
    sys.exit("Overpass 서버에서 받지 못했습니다. 나중에 다시 실행하세요.")


def relations_query(box) -> str:
    west, south, east, north = box
    return f"""[out:json][timeout:170];
relation["building"]({south},{west},{north},{east});
out body geom;"""


def fetch(name: str, box) -> dict:
    path = RAW_DIR / f"{name}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        # 예전에 받은 파일에는 여러 조각으로 된 건물(relation)이 없거나 윤곽 조각(members)이 빠져 있다
        broken = any(e["type"] == "relation" and "members" not in e for e in data["elements"])
        if (not data.get("relations") or broken) and "--offline" not in sys.argv:
            data["elements"] = [e for e in data["elements"] if e["type"] != "relation"]
            for part in tiles(box, 2 if name == "제주시" else 1):
                data["elements"] += download(part, relations_query(part))["elements"]
            data["relations"] = True
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    if "--offline" in sys.argv:
        sys.exit(f"{path} 이 없습니다. --offline 없이 실행해 내려받으세요.")
    elements = []
    for part in tiles(box, 2 if name == "제주시" else 1):
        elements += download(part)["elements"]
        print(f"  {name}: 지금까지 {len(elements):,}개", file=sys.stderr, flush=True)
    data = {"elements": elements, "relations": True, "source": "OpenStreetMap via Overpass API", "fetched": time.strftime("%Y-%m-%d")}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def footprint_area(points: list[tuple[float, float]]) -> float:
    """경위도 꼭짓점의 면적(㎡). 작은 면적이라 평면으로 계산한다."""
    k = math.cos(math.radians(CENTER_LAT)) * METERS_PER_DEG_LAT
    xy = [(lon * k, lat * METERS_PER_DEG_LAT) for lon, lat in points]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2


def number(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(str(text).split(";")[0].replace("m", "").strip())
    except ValueError:
        return None


def height_of(tags: dict, area: float, seed: int) -> float:
    height = number(tags.get("height"))
    if height and 2 < height < 250:
        return height
    levels = number(tags.get("building:levels"))
    if levels and 0 < levels < 70:
        return levels * 3.2 + 1
    kind = tags.get("building", "yes")
    jitter = (seed % 7) / 6   # 같은 건물은 늘 같은 값
    if kind in ("apartments",):
        return 24 + jitter * 18
    if kind in ("hotel",):
        return 20 + jitter * 20
    if kind in ("commercial", "office", "retail", "public", "hospital", "school", "university", "civic", "government"):
        return 10 + jitter * 8 + min(area, 4000) / 600
    if kind in ("house", "detached", "residential", "semidetached_house", "bungalow"):
        return 6 + jitter * 3
    if kind in ("garage", "garages", "shed", "roof", "carport", "greenhouse", "hut", "kiosk"):
        return 3 + jitter
    if kind in ("industrial", "warehouse", "church", "temple"):
        return 9 + jitter * 4
    return (5 if area < 120 else 8 if area < 400 else 12 if area < 1500 else 16) + jitter * 4


def encode(points: list[tuple[float, float]]) -> list[int]:
    out = []
    for lon, lat in points:
        out += [round((lon - CENTER_LON) * Q), round((lat - CENTER_LAT) * Q)]
    return out


def outer_rings(element: dict) -> list[list[tuple[float, float]]]:
    """여러 조각으로 된 건물(relation)의 바깥 윤곽을 이어 닫힌 고리로 만든다. 안쪽 구멍은 버린다."""
    pieces = [[(p["lon"], p["lat"]) for p in m.get("geometry", [])] for m in element.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
    rings = []
    while pieces:
        ring = pieces.pop(0)
        while ring[0] != ring[-1]:
            for i, piece in enumerate(pieces):
                if piece[0] == ring[-1]:
                    ring += piece[1:]
                elif piece[-1] == ring[-1]:
                    ring += piece[-2::-1]
                else:
                    continue
                pieces.pop(i)
                break
            else:
                break   # 이어지지 않는 조각은 버린다
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def box_model(lon: float, lat: float, length: float, width: float, angle: float) -> list[tuple[float, float]]:
    """위치·크기·방향으로 직사각형 꼭짓점(경위도)을 만든다."""
    k = math.cos(math.radians(CENTER_LAT)) * METERS_PER_DEG_LAT
    a = math.radians(angle)
    corners = [(-length / 2, -width / 2), (length / 2, -width / 2), (length / 2, width / 2), (-length / 2, width / 2)]
    return [(lon + (x * math.cos(a) - y * math.sin(a)) / k, lat + (x * math.sin(a) + y * math.cos(a)) / METERS_PER_DEG_LAT) for x, y in corners]


def main() -> None:
    buildings, roads, seen, ids, centers = [], [], set(), [], []
    for name, box in AREAS.items():
        data = fetch(name, box)
        added = [0, 0]
        for element in data.get("elements", []):
            key = f'{element.get("type")}/{element["id"]}'
            if key in seen:
                continue
            seen.add(key)
            tags = element.get("tags", {})
            if element.get("type") == "relation" and "building" in tags:
                for ring in outer_rings(element):
                    points = ring[:-1]
                    area = footprint_area(points)
                    if area < 12:
                        continue
                    buildings.append([round(height_of(tags, area, element["id"]), 1), *encode(points)])
                    ids.append(key)
                    centers.append((sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points)))
                    added[0] += 1
                continue
            if element.get("type") != "way" or "geometry" not in element:
                continue
            points = [(p["lon"], p["lat"]) for p in element["geometry"]]
            if "building" in tags:
                if len(points) < 4 or points[0] != points[-1]:
                    continue
                points = points[:-1]
                area = footprint_area(points)
                if area < 12:
                    continue
                buildings.append([round(height_of(tags, area, element["id"]), 1), *encode(points)])
                ids.append(key)
                centers.append((sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points)))
                added[0] += 1
            elif tags.get("highway") in ROAD_WIDTH and len(points) >= 2:
                roads.append([ROAD_WIDTH[tags["highway"]], *encode(points)])
                added[1] += 1
        print(f"{name}: 건물 {added[0]:,}동, 도로 {added[1]:,}구간")
    for name, lon, lat, length, width, angle, height in MODELS:
        points = box_model(lon, lat, length, width, angle)
        buildings.append([height, *encode(points)])
        ids.append(f"model/{name}")
        centers.append((lon, lat))
        print(f"모형: {name} ({length}×{width}m, 높이 {height}m)")
    landmarks = []
    index_of = {}
    for i, key in enumerate(ids):
        index_of.setdefault(key, []).append(i)
    k = math.cos(math.radians(CENTER_LAT)) * METERS_PER_DEG_LAT
    for name, kind, note, lon, lat, osm_ids, radius in LANDMARKS:
        chosen = {i for key in osm_ids for i in index_of.get(key, [])}
        if radius:
            chosen |= {i for i, (x, y) in enumerate(centers) if math.hypot((x - lon) * k, (y - lat) * METERS_PER_DEG_LAT) <= radius}
        landmarks.append({"name": name, "kind": kind, "note": note, "at": encode([(lon, lat)]), "buildings": sorted(chosen)})
        print(f"  랜드마크 {name}: 건물 {len(chosen)}동")
    out = {
        "areas": [{"name": name, "west": w, "south": s, "east": e, "north": n} for name, (w, s, e, n) in AREAS.items()],
        "buildings": buildings,
        "roads": roads,
        "landmarks": landmarks,
        "source": "© OpenStreetMap 기여자 (ODbL 1.0), Overpass API 로 받음. 층수가 없는 건물의 높이는 종류와 면적으로 추정",
        "built": time.strftime("%Y-%m-%d"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"건물 {len(buildings):,}동, 도로 {len(roads):,}구간 -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f}MB)")


if __name__ == "__main__":
    main()
