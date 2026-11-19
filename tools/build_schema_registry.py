"""제주빅데이터센터 데이터 명세서(xlsx)를 스키마 레지스트리(JSON)로 변환한다.

사용법:
    python tools/build_schema_registry.py

입력:  data/center_spec/데이터명세서_kr.xlsx   (https://jejudatahub.net/center/data 에서 공개)
       data/center_spec/center_tables.json    (센터 제공데이터 목록: 테이블명, 반출 가능 여부)
출력:  src/golmok/center_schemas.json

명세서가 개정되면 파일을 교체하고 이 스크립트를 다시 실행하면 된다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "data" / "center_spec" / "데이터명세서_kr.xlsx"
TABLES_PATH = ROOT / "data" / "center_spec" / "center_tables.json"
OUT_PATH = ROOT / "src" / "golmok" / "center_schemas.json"

SHEET_ID = re.compile(r"^(\d-\d\d)\s+(.*)$")
PERIOD = re.compile(r"기간\s*:\s*([^/]+)")

# 명세서 시트의 고정 열 위치 (모든 데이터 시트가 같은 양식을 쓴다)
COL_NO, COL_NAME, COL_KO, COL_TYPE, COL_EXAMPLE, COL_NOTE = 0, 1, 2, 3, 4, 6
META_VALUE, EXPORT_VALUE, PROVIDER_VALUE = 2, 4, 7


def text(value) -> str:
    return "" if value is None else str(value).strip()


def normalize_title(title: str) -> str:
    """시트명과 목록 API의 표기 차이(공백, 쉼표/슬래시)를 흡수한다."""
    return re.sub(r"[\s,/]", "", title)


def cell(row: tuple, index: int) -> str:
    return text(row[index]) if index < len(row) else ""


def parse_references(rows: list[tuple]) -> list[dict]:
    """컬럼 표 아래의 [참고N] 코드표를 읽는다.

    코드표는 가로로 나란히 놓인다: 제목 행([참고1] ..., [참고2] ...) → 필드명 행(No, ...) → 값 행.
    각 코드표는 자기 제목 열부터 다음 제목 열 직전까지를 차지한다.
    """
    references: list[dict] = []
    blocks: list[dict] = []  # 현재 제목 행에서 시작된 코드표들
    expect_fields = False
    for row in rows:
        starts = [i for i, v in enumerate(row) if text(v).startswith("[참고")]
        if starts:
            blocks = []
            for position, start in enumerate(starts):
                end = starts[position + 1] if position + 1 < len(starts) else len(row)
                block = {"title": text(row[start]), "fields": [], "rows": [], "_span": (start, end)}
                blocks.append(block)
                references.append(block)
            expect_fields = True
            continue
        for block in blocks:
            start, end = block["_span"]
            values = [text(v) for v in row[start + 1 : end]]
            if expect_fields:
                block["fields"] = [v for v in values if v]
            elif cell(row, start).isdigit():
                block["rows"].append(values[: len(block["fields"])])
        expect_fields = False
    for block in references:
        del block["_span"]
    return [b for b in references if b["rows"]]


def parse_sheet(ws) -> dict | None:
    match = SHEET_ID.match(ws.title.strip())
    if not match:
        return None  # 목록, 참고표 시트

    schema = {
        "id": match.group(1),
        "title": match.group(2).strip(),
        "category": "",
        "table": "",
        "description": "",
        "period": "",
        "exportable": False,
        "provider": "",
        "columns": [],
        "references": [],
    }
    in_columns = False
    rows = list(ws.iter_rows(values_only=True))
    for index, row in enumerate(rows):
        first = cell(row, COL_NO)
        if in_columns:
            if not first.isdigit():
                schema["references"] = parse_references(rows[index:])
                break
            schema["columns"].append(
                {
                    "name": cell(row, COL_NAME),
                    "ko": cell(row, COL_KO),
                    "type": cell(row, COL_TYPE).upper(),
                    "example": cell(row, COL_EXAMPLE),
                    "note": cell(row, COL_NOTE),
                }
            )
        elif first == "카테고리":
            schema["category"] = cell(row, META_VALUE)
        elif first == "데이터명(영문)":
            schema["table"] = cell(row, META_VALUE)
        elif first == "설명":
            description = re.sub(r"\s*\n\s*", " / ", cell(row, META_VALUE))
            schema["description"] = description
            period = PERIOD.search(description)
            schema["period"] = period.group(1).strip() if period else ""
        elif first == "등록일자":
            schema["exportable"] = "불가능" not in cell(row, EXPORT_VALUE)
            schema["provider"] = cell(row, PROVIDER_VALUE)
        elif first == "No":
            in_columns = True
    return schema


def main() -> None:
    tables = json.loads(TABLES_PATH.read_text(encoding="utf-8"))["data"]
    table_by_title = {normalize_title(t["title"]): t["tableName"] for t in tables}

    workbook = openpyxl.load_workbook(SPEC_PATH, read_only=True, data_only=True)
    schemas = [s for s in map(parse_sheet, workbook.worksheets) if s]

    missing_table = []
    for schema in schemas:
        if not schema["table"]:
            schema["table"] = table_by_title.get(normalize_title(schema["title"]), "")
        if not schema["table"]:
            missing_table.append(f'{schema["id"]} {schema["title"]}')

    OUT_PATH.write_text(
        json.dumps({"source": SPEC_PATH.name, "schemas": schemas}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"{len(schemas)}개 데이터셋, 컬럼 {sum(len(s['columns']) for s in schemas)}개 -> {OUT_PATH}")
    if missing_table:
        print("테이블명 매칭 실패:", ", ".join(missing_table))
    empty = [s["id"] for s in schemas if not s["columns"]]
    if empty:
        print("컬럼을 읽지 못한 시트:", ", ".join(empty))


if __name__ == "__main__":
    main()
