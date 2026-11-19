"""동봉한 Windows 패키지(wheels/) 안의 확장 모듈(.pyd, .dll)을 모아 docs/확장모듈_목록.md 로 적는다.

사용법: python tools/list_native_modules.py
센터 PC 보안 정책이 서명 없는 확장 모듈의 실행을 막을 때, 운영자가 허용 등록할 파일을 바로 볼 수 있게 하기 위한 목록이다.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    lines = ["# 확장 모듈 목록 (허용 등록용)", "",
             "센터 PC 보안 정책(스마트 앱 컨트롤, AppLocker, WDAC 등)이 디지털 서명 없는 확장 모듈의 실행을 막으면 프로그램이 시작되지 않습니다. "
             "아래는 `run.bat` 이 설치하는 패키지 가운데 확장 모듈(`.pyd`, `.dll`)이 든 패키지와 파일입니다. 모두 PyPI 에 공개된 오픈소스 패키지의 공식 배포 파일이며, `wheels/` 폴더에 그대로 들어 있습니다.", "",
             "**폴더 단위로 등록할 때**: `<프로그램 폴더>\\.venv\\` 한 곳만 허용하면 됩니다(`run.bat` 첫 실행 뒤에 생깁니다).", "",
             "**파일 단위로 등록할 때**: 아래 경로는 모두 `<프로그램 폴더>\\.venv\\Lib\\site-packages\\` 아래입니다.", "",
             "Python 실행 파일(`python.exe`)은 python.org 가 서명해 배포한 설치 파일로 설치합니다.", ""]
    total = 0
    summary = ["| 패키지 | 버전 | 확장 모듈 수 | 쓰임 |", "|---|---|---|---|"]
    uses = {"duckdb": "메모리 DB", "numpy": "수치 계산", "pandas": "집계", "matplotlib": "보고서 차트", "contourpy": "matplotlib 의존", "kiwisolver": "matplotlib 의존",
            "fonttools": "matplotlib 의존(글꼴)", "pillow": "matplotlib·PDF 의존(이미지)", "pydantic_core": "요청 검증(FastAPI)", "jiter": "Claude SDK 의존(JSON)", "charset_normalizer": "ReportLab 의존(글자 인코딩)"}
    detail = []
    for wheel in sorted((ROOT / "wheels").glob("*win_amd64.whl")):
        name, version = wheel.name.split("-")[:2]
        with zipfile.ZipFile(wheel) as archive:
            files = sorted(n for n in archive.namelist() if n.lower().endswith((".pyd", ".dll")))
        if not files:
            continue
        total += len(files)
        summary.append(f"| {name} | {version} | {len(files)} | {uses.get(name, '')} |")
        detail += [f"### {name} {version}", "", *[f"- `{f.replace('/', chr(92))}`" for f in files], ""]
    lines += [*summary, "", f"합계 {total}개 파일.", "", "## 파일별 경로", "", *detail]
    (ROOT / "docs" / "확장모듈_목록.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"docs/확장모듈_목록.md ({total}개 파일)")


if __name__ == "__main__":
    main()
