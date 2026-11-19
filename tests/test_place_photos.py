"""장소 카드 사진: 목록의 파일이 모두 있고, 재배포할 수 있는 라이선스이며, 카드에 있는 장소 이름과 맞는지."""
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "golmok" / "static"
PHOTOS = json.loads((STATIC / "places" / "photos.json").read_text(encoding="utf-8"))
FREE = re.compile(r"^(CC BY(-SA)? [0-9.]+|CC0|Public domain|공공누리 제1유형)$")


def test_every_photo_file_exists_and_is_small():
    for place, photo in PHOTOS.items():
        path = STATIC / "places" / photo["file"]
        assert path.is_file(), place
        assert path.read_bytes()[:3] == b"\xff\xd8\xff" and path.stat().st_size < 150_000, place


def test_every_photo_is_freely_licensed_and_credited():
    credits = (STATIC / "places" / "CREDITS.md").read_text(encoding="utf-8")
    for place, photo in PHOTOS.items():
        assert FREE.match(photo["license"]), (place, photo["license"])
        assert photo["author"] and photo["source"].startswith("https://commons.wikimedia.org/"), place
        assert photo["file"] in credits, place


def test_photo_names_match_place_cards():
    places = set(re.findall(r'^\s+"([^"]+)": \{ names:', (STATIC / "app.js").read_text(encoding="utf-8"), re.M))
    assert set(PHOTOS) <= places
