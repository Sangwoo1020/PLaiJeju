"""내려받은 Kenney 팩(zip)에서 .glb 모델만 꺼내 정리한다.

사용법:
    python tools/import_props.py [zip 이 있는 폴더 ...]

폴더를 주지 않으면 ~/Downloads 와 data/props_src 에서 kenney_*.zip 을 찾는다.
모델은 data/props_src/<팩>/glb/ 에 넣고, 파일명 목록을 출력한다.
실제로 쓸 모델만 src/golmok/static/props/ 로 복사해 manifest.json 에 적는다.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "props_src"
PACK_NAME = re.compile(r"^kenney_(.+?)(?:_\d+(?:\.\d+)*)?(?: \(\d+\))?\.zip$", re.IGNORECASE)
KEEP_LICENSE = re.compile(r"licen[sc]e", re.IGNORECASE)


def find_zips(folders: list[Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for folder in folders:
        for path in sorted(folder.rglob("kenney_*.zip") if folder == OUT_DIR else folder.glob("kenney_*.zip")):
            found.setdefault(path.name, path)
    return list(found.values())


def extract(path: Path) -> list[str]:
    """zip 안의 경로는 믿지 않고 파일명만 써서 꺼낸다."""
    match = PACK_NAME.match(path.name)
    pack = match.group(1) if match else path.stem
    target = OUT_DIR / pack / "glb"
    target.mkdir(parents=True, exist_ok=True)
    models = []
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        for name in names:
            base = PurePosixPath(name).name
            if name.lower().endswith(".glb"):
                (target / base).write_bytes(archive.read(name))
                models.append(base)
            elif KEEP_LICENSE.search(base):
                (target.parent / base).write_bytes(archive.read(name))
    if not models:
        kinds = sorted({PurePosixPath(n).suffix.lower() for n in names})
        print(f"{pack}: .glb 가 없습니다. 들어 있는 형식: {', '.join(kinds)}")
    else:
        print(f"{pack}: .glb {len(models)}개 -> {target.relative_to(ROOT)}")
        print("  " + ", ".join(sorted(models)))
    return models


def main() -> None:
    folders = [Path(a).expanduser() for a in sys.argv[1:]] or [Path.home() / "Downloads", OUT_DIR]
    zips = find_zips(folders)
    if not zips:
        raise SystemExit("kenney_*.zip 을 찾지 못했습니다: " + ", ".join(map(str, folders)))
    for path in zips:
        try:
            extract(path)
        except zipfile.BadZipFile:
            print(f"{path.name}: zip 이 온전하지 않습니다. 아직 내려받는 중이거나 끊긴 파일입니다. 건너뜁니다.")


if __name__ == "__main__":
    main()
