"""실제 고도 데이터로 3D 화면용 제주 지형 파일을 만든다.

사용법:
    python tools/build_terrain.py

입력:  data/terrain_tiles/10_{x}_{y}.png
       AWS 공개 데이터 "Terrain Tiles"(Mapzen/Tilezen, terrarium 형식, 줌 10) 12장.
       https://s3.amazonaws.com/elevation-tiles-prod/terrarium/10/{x}/{y}.png
       원자료는 NASA SRTM 등이며 출처 표기가 필요하다.
출력:  src/golmok/static/jeju_terrain.bin   Int16 격자 (북→남, 서→동)
           땅:  고도(m), 1 이상
           바다: -(해안까지 거리 × 100), 거리 단위는 화면 좌표
       src/golmok/static/jeju_terrain.json  격자 크기와 범위, 출처

격자는 3D 화면(scene.js)의 좌표계에 맞춘다: 경도 1도 = 46 단위, 한 칸 = 0.1 단위(약 200m).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
TILE_DIR = ROOT / "data" / "terrain_tiles"
OUT_BIN = ROOT / "src" / "golmok" / "static" / "jeju_terrain.bin"
OUT_META = ROOT / "src" / "golmok" / "static" / "jeju_terrain.json"

ZOOM, TILE = 10, 256
TILES_X, TILES_Y = range(870, 874), range(410, 413)

# scene.js 와 같은 좌표계
CENTER_LON, CENTER_LAT = 126.555, 33.385
SCALE, LAT_STRETCH = 46, 1.19
WIDTH_UNITS, DEPTH_UNITS, CELL = 46, 30, 0.1
MIN_ISLAND_CELLS = 8          # 이보다 작은 땅 조각은 잡음으로 보고 바다로 돌린다
MAX_SEA_DISTANCE = 300        # 바다 값의 하한(-30000) 을 넘지 않게


def load_mosaic() -> np.ndarray:
    """타일을 이어 붙여 고도(m) 배열을 만든다. terrarium: 고도 = R*256 + G + B/256 - 32768."""
    rows = []
    for y in TILES_Y:
        row = []
        for x in TILES_X:
            rgb = np.asarray(Image.open(TILE_DIR / f"{ZOOM}_{x}_{y}.png").convert("RGB"), dtype=np.float64)
            row.append(rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768)
        rows.append(np.hstack(row))
    return np.vstack(rows)


def pixel(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """경위도 → 이어 붙인 타일 안의 픽셀 좌표 (웹 메르카토르)."""
    world = TILE * 2**ZOOM
    px = (lon + 180) / 360 * world - TILES_X.start * TILE
    py = (1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * world - TILES_Y.start * TILE
    return px, py


def main() -> None:
    elevation = ndimage.uniform_filter(load_mosaic(), size=3)   # 격자가 타일보다 성기므로 살짝 뭉갠다

    columns, rows = round(WIDTH_UNITS / CELL) + 1, round(DEPTH_UNITS / CELL) + 1
    x = np.linspace(-WIDTH_UNITS / 2, WIDTH_UNITS / 2, columns)
    z = np.linspace(-DEPTH_UNITS / 2, DEPTH_UNITS / 2, rows)     # 북쪽(-)에서 남쪽(+)으로
    lon = CENTER_LON + x / SCALE
    lat = CENTER_LAT - z / (SCALE * LAT_STRETCH)
    px, py = pixel(*np.meshgrid(lon, lat))
    if px.min() < 0 or py.min() < 0 or px.max() > elevation.shape[1] - 1 or py.max() > elevation.shape[0] - 1:
        raise SystemExit("격자가 내려받은 타일 범위를 벗어납니다. 타일을 더 받아야 합니다.")
    meters = ndimage.map_coordinates(elevation, [py, px], order=1)

    land = meters >= 1
    labels, count = ndimage.label(land)
    sizes = ndimage.sum(land, labels, range(1, count + 1))
    for index, size in enumerate(sizes, start=1):
        if size < MIN_ISLAND_CELLS:
            land[labels == index] = False

    sea_distance = np.minimum(ndimage.distance_transform_edt(~land) * CELL, MAX_SEA_DISTANCE)
    grid = np.where(land, np.clip(np.round(meters), 1, 32000), -np.round(sea_distance * 100)).astype("<i2")

    OUT_BIN.write_bytes(grid.tobytes())
    OUT_META.write_text(json.dumps({
        "columns": columns, "rows": rows, "cell": CELL,
        "west": float(lon[0]), "east": float(lon[-1]), "north": float(lat[0]), "south": float(lat[-1]),
        "source": "Terrain Tiles (Mapzen/Tilezen), AWS Open Data — 원자료 NASA SRTM 등",
        "source_url": "https://registry.opendata.aws/terrain-tiles/",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    peak = np.unravel_index(np.argmax(meters * land), meters.shape)
    print(f"격자 {columns}x{rows}, 땅 {land.mean():.1%}, 섬 조각 {int((sizes >= MIN_ISLAND_CELLS).sum())}개")
    print(f"최고점 {meters[peak]:.0f}m @ 경도 {lon[peak[1]]:.3f}, 위도 {lat[peak[0]]:.3f}")
    print(f"{OUT_BIN.relative_to(ROOT)} {OUT_BIN.stat().st_size / 1024:.0f}KB")


if __name__ == "__main__":
    main()
