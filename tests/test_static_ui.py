"""화면 파일 점검: 로고가 올바른 SVG 이고, 시작 화면의 '바로 해 보기' 단추가 모두 화면 코드에서 처리되는지."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "golmok" / "static"


def test_logo_is_valid_svg_and_used():
    root = ET.parse(STATIC / "logo.svg").getroot()
    assert root.tag.endswith("svg") and root.get("viewBox") == "0 0 48 48"
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'href="/static/logo.svg"' in page and 'src="/static/logo.svg"' in page and "PL<em>ai</em>Jeju" in page


def test_feature_buttons_are_handled():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    kinds = re.findall(r'data-feature="(\w+)"', page)
    assert kinds == ["ask", "brief", "dong", "compare"]
    for kind in ("ask", "brief", "compare"):
        assert f'kind === "{kind}"' in script
    assert 'id="dong-card"' in page and "function showDong" in script and "function compareView" in script
