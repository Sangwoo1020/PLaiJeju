"""제주관광빅데이터플랫폼에서 받은 CSV 를 프로그램에 넣을 공개 데이터로 정리한다.

사용법:
    python tools/import_ijto.py [CSV 파일 ...]

파일을 주지 않으면 ~/Downloads 에서 플랫폼 CSV 를 찾는다.
    - 제주 지역별 소비(regSn=27): 'BC카드 개인_업종별 데이터_YYYY-MM~YYYY-MM.csv'
    - 제주 지역별 방문객(읍면동)(regSn=21): 'SKT 읍면동별 유동인구 (통합)_YYYY-MM~YYYY-MM.csv'
    파일 종류는 이름이 아니라 머리글로 구분한다.
소비 파일은 같은 (달, 관광객구분, 읍면동, 업종)이 여러 줄로 나뉘어 있다(파일에 없는 세부 구분).
달별 합계가 줄 수와 상관없이 고르게 이어져 서로 겹치지 않는 조각으로 보고, 더해서 한 줄로 만든다.
결과:
    src/golmok/reference/ijto_spend_public.csv     제주 지역별 소비 (P-03)
    src/golmok/reference/ijto_visitors_public.csv  제주 지역별 방문객(읍면동) (P-04)

출처: 제주관광공사 제주관광빅데이터플랫폼 (data.ijto.or.kr). 공공누리 제1유형, 출처 표시.
"""
from __future__ import annotations

import csv
import io
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "src" / "golmok" / "reference"

# 원본 머리글(공백·괄호 차이를 흡수해 비교) → 정리한 컬럼
SPEND_COLUMNS = {
    "기준연월": "month", "관광객구분": "tourist_type", "읍면동코드": "emd_cd", "읍면동명": "emd_nm",
    "지역명": "region", "업종명": "industry", "이용건수": "use_cnt", "소비금액": "use_amt", "소비금액원": "use_amt",
}
VISITOR_COLUMNS = {
    "기준연월": "month", "읍면동코드": "emd_cd", "시군구명": "city", "읍면동명": "emd_nm",
    "내국인방문객수": "domestic_visitors", "외국인방문객수": "foreign_visitors",
    "총방문객수": "total_visitors", "총방문객수내국인외국인": "total_visitors",
}
TIME_COLUMNS = {
    "기준연월": "month", "관광객구분": "tourist_type", "읍면동코드": "emd_cd", "읍면동명": "emd_nm", "업종명": "industry",
    "카드매출액원": "use_amt", "카드매출액": "use_amt", "이용건수": "use_cnt", "시간대": "hour",
}
NATION_COLUMNS = {
    "기준연월": "month", "관광객구분": "tourist_type", "업종명": "industry", "국가": "country",
    "이용건수": "use_cnt", "소비금액원": "use_amt", "소비금액": "use_amt",
}
CRUISE_COLUMNS = {"기준연월": "month", "기준연월YYYYMM": "month", "국가": "country", "크루즈입국자수": "cruise_arrivals"}
SPEND_ORDER = ["month", "tourist_type", "emd_cd", "emd_nm", "region", "industry", "use_cnt", "use_amt"]
TIME_ORDER = ["month", "tourist_type", "emd_cd", "emd_nm", "industry", "hour", "use_cnt", "use_amt"]
NATION_ORDER = ["month", "tourist_type", "industry", "country", "use_cnt", "use_amt"]
CRUISE_ORDER = ["month", "country", "cruise_arrivals"]
# 종류 → (머리글 표, 컬럼 순서, 합칠 때의 열쇠 개수, 더할 값, 결과 파일)
KINDS = {
    "spend": (None, SPEND_ORDER, 6, ["use_cnt", "use_amt"], "ijto_spend_public.csv"),
    "visitors": (None, None, 0, [], "ijto_visitors_public.csv"),
    "time": (TIME_COLUMNS, TIME_ORDER, 6, ["use_cnt", "use_amt"], "ijto_spend_time_public.csv"),
    "nation": (NATION_COLUMNS, NATION_ORDER, 4, ["use_cnt", "use_amt"], "ijto_spend_nation_public.csv"),
    "cruise": (CRUISE_COLUMNS, CRUISE_ORDER, 2, ["cruise_arrivals"], "ijto_cruise_nation_public.csv"),
}
VISITOR_ORDER = ["month", "emd_cd", "city", "emd_nm", "domestic_visitors", "foreign_visitors", "total_visitors"]
NUMERIC = {"use_cnt", "use_amt", "domestic_visitors", "foreign_visitors", "total_visitors", "cruise_arrivals"}


def key(header: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFC", header) if ch.isalnum())


def read_rows(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"{path.name}: 글자 인코딩을 알 수 없습니다")
    return list(csv.DictReader(io.StringIO(text)))


def kind_of(rows: list[dict]) -> str | None:
    headers = {key(h) for h in rows[0]} if rows else set()
    if "크루즈입국자수" in headers:
        return "cruise"
    if "시간대" in headers and "업종명" in headers:
        return "time"
    if "국가" in headers and "업종명" in headers and "읍면동명" not in headers:
        return "nation"
    if {"관광객구분", "업종명"} <= headers:
        return "spend"
    if {"내국인방문객수", "외국인방문객수"} <= headers:
        return "visitors"
    return None


def tidy(rows: list[dict], mapping: dict[str, str], order: list[str]) -> list[dict]:
    out = []
    for row in rows:
        item = {mapping[key(h)]: (v or "").strip() for h, v in row.items() if h and key(h) in mapping}
        missing = [c for c in order if c not in item]
        if missing:
            raise SystemExit(f"없는 컬럼: {missing}. 머리글: {list(row)}")
        item["month"] = "".join(ch for ch in item["month"] if ch.isdigit())[:6]
        if "hour" in item:   # '23', '23시', '23~24시' → '23'
            digits = "".join(ch for ch in item["hour"].split("~")[0] if ch.isdigit())
            item["hour"] = str(int(digits)) if digits else item["hour"]
        for column in NUMERIC & item.keys():
            item[column] = item[column].replace(",", "") or "0"
        out.append({c: item[c] for c in order})
    return sorted(out, key=lambda r: tuple(r[c] for c in order[:4]))


def merge_parts(rows: list[dict], keys: list[str], values: list[str]) -> list[dict]:
    """같은 열쇠의 조각 줄을 더해 한 줄로 만든다."""
    merged: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row[k] for k in keys)
        if key in merged:
            for v in values:
                merged[key][v] = str(int(merged[key][v]) + int(row[v]))
        else:
            merged[key] = dict(row)
    return sorted(merged.values(), key=lambda r: tuple(r[k] for k in keys))


def write(rows: list[dict], order: list[str], name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=order)
        writer.writeheader()
        writer.writerows(rows)
    months = sorted({r["month"] for r in rows})
    places = f", 읍면동 {len({r['emd_nm'] for r in rows})}개" if "emd_nm" in order else ""
    print(f"{name}: {len(rows):,}행, {months[0]}~{months[-1]} ({len(months)}개월){places}")


def find_downloads() -> list[Path]:
    folder = Path.home() / "Downloads"
    names = ("지역별", "BC카드", "SKT", "유동인구", "시간대", "국가별", "크루즈")
    return [p for p in folder.glob("*.csv") if any(n in unicodedata.normalize("NFC", p.name) for n in names)]


def main() -> None:
    paths = [Path(a).expanduser() for a in sys.argv[1:]] or find_downloads()
    if not paths:
        raise SystemExit("관광빅데이터 CSV 를 찾지 못했습니다. 파일 경로를 직접 주세요.")
    spend, visitors = [], []
    extra: dict[str, list[dict]] = {}
    for path in paths:
        rows = read_rows(path)
        kind = kind_of(rows)
        print(f"{path.name}: {len(rows):,}행 -> {kind or '알 수 없는 형식, 건너뜀'}")
        if kind == "spend":
            spend += tidy(rows, SPEND_COLUMNS, SPEND_ORDER)
        elif kind == "visitors":
            visitors += tidy(rows, VISITOR_COLUMNS, VISITOR_ORDER)
        elif kind in ("time", "nation", "cruise"):
            mapping, order, _, _, _ = KINDS[kind]
            extra.setdefault(kind, []).extend(tidy(rows, mapping, order))
    for kind, rows in extra.items():
        _, order, keys, values, name = KINDS[kind]
        merged = merge_parts(rows, order[:keys], values)
        print(f"{kind}: 조각 {len(rows):,}줄 -> {len(merged):,}줄로 합침")
        write(merged, order, name)
    if spend:
        before = len(spend)
        spend = merge_parts(spend, SPEND_ORDER[:6], ["use_cnt", "use_amt"])
        print(f"소비: 조각 {before:,}줄 -> {len(spend):,}줄로 합침")
        write(spend, SPEND_ORDER, "ijto_spend_public.csv")
    if visitors:
        write(visitors, VISITOR_ORDER, "ijto_visitors_public.csv")


if __name__ == "__main__":
    main()
