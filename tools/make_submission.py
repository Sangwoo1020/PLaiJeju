"""대회 제출용 zip 을 만든다.

사용법:
    python tools/make_submission.py

정해진 파일만 담고, 비밀 정보가 섞이지 않았는지 검사한다. 결과: dist/DataPlayLabs_제출물.zip

평가용 API 키 (선택): 운영진 안내(대회 규칙 2026-11-10 개정)에 따라 평가 전용 키를 zip 에 넣을 수 있다.
프로젝트 맨 위에 `.env.eval` 파일을 만들고 `ANTHROPIC_API_KEY=평가전용키` 한 줄을 넣으면, 그 파일이 zip 안의 `.env` 로 들어가고
zip 안 README 의 키 안내 문장이 "평가 전용 키가 들어 있다"로 바뀐다. `.env.eval` 이 없으면 키 없이 만든다.
개발에 쓰는 `.env` 는 어떤 경우에도 넣지 않는다. 평가 전용 키는 사용 한도를 걸고, 평가가 끝나면 폐기할 것.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "DataPlayLabs_제출물.zip"
TOP_FILES = ["README.md", "requirements.txt", "run.bat", ".env.example", "pyproject.toml", ".gitignore"]
TOP_FOLDERS = ["src", "tools", "tests", "docs", "wheels"]   # wheels: Windows 64비트·Python 3.12용 오프라인 설치 패키지
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".egg-info"}
SKIP_NAMES = {"주제후보_3선_2026-11-09.md", ".DS_Store"}   # 내부 검토 자료, macOS 메타파일
SECRET = re.compile(rb"sk-ant-[A-Za-z0-9_-]{8,}")
EVAL_ENV = ROOT / ".env.eval"   # 평가 전용 키 (선택). 있으면 zip 안의 .env 가 된다
KEY_NOTE = re.compile(r"<!-- EVAL_KEY -->.*?<!-- /EVAL_KEY -->", re.S)
KEY_INCLUDED = ("**이 제출본에는 평가 전용 API 키가 들어 있습니다.** 따로 입력할 것 없이 `run.bat` 만 실행하면 Claude 답변이 나옵니다. "
                "키에는 사용 한도를 걸어 두었고, 평가 기간이 끝나면 폐기합니다. 한도에 걸리면 답 위에 이유가 표시되고 기본 모드로 답합니다.")


def collect() -> list[Path]:
    files = [ROOT / name for name in TOP_FILES]
    for folder in TOP_FOLDERS:
        files += [p for p in sorted((ROOT / folder).rglob("*")) if p.is_file()]
    return [p for p in files if not SKIP_PARTS & set(p.parts) and p.suffix not in SKIP_SUFFIXES and p.name not in SKIP_NAMES and not any(part.endswith(".egg-info") for part in p.parts)]


def main() -> None:
    files = collect()
    bad = [p for p in files if p.name == ".env" or SECRET.search(p.read_bytes())]
    if bad:
        sys.exit("비밀 정보가 들어 있어 중단합니다: " + ", ".join(str(p.relative_to(ROOT)) for p in bad))
    with_key = EVAL_ENV.is_file()
    if with_key and not re.search(rb"^ANTHROPIC_API_KEY=\S+", EVAL_ENV.read_bytes(), re.M):
        sys.exit(".env.eval 에 'ANTHROPIC_API_KEY=키' 줄이 없습니다. 고치거나 파일을 지우고 다시 실행하세요.")
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = Path("DataPlayLabs") / path.relative_to(ROOT)
            if path.name == "README.md" and path.parent == ROOT and with_key:
                archive.writestr(str(name), KEY_NOTE.sub(KEY_INCLUDED, path.read_text(encoding="utf-8")))
            else:
                archive.write(path, name)
        if with_key:
            archive.write(EVAL_ENV, Path("DataPlayLabs") / ".env")
    print(f"{len(files) + with_key}개 파일, {OUT.stat().st_size / 1e6:.1f}MB -> {OUT.relative_to(ROOT)}")
    print("평가 전용 키: 포함 (.env.eval -> zip 안의 .env). 사용 한도를 걸었는지 확인하고, 평가가 끝나면 폐기하세요." if with_key
          else "평가 전용 키: 없음. 심사위원이 Claude 답변을 보려면 .env.eval 을 만들고 다시 실행하세요 (파일 맨 위 설명 참고).")


if __name__ == "__main__":
    main()
