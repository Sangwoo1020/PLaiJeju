"""제주 관광객 입도통계(월별·국적별 확정치)를 CSV 로 만든다.

사용법:
    python tools/fetch_foreign_arrivals.py          # 2023-01 ~ 게시된 마지막 달, src/golmok/reference/foreign_arrivals_public.csv 갱신
    python tools/fetch_foreign_arrivals.py 2024     # 시작 연도 지정

출처
    1) 제주특별자치도관광협회 누리집 '월별통계' (https://www.visitjeju.or.kr/issue/stats/monthly.htm?year=YYYY&month=MM)
       '목적별 관광객입도현황(확정치)' 표에서 내국인 소계와 외국인 국적 12개의 월계를 읽는다. 2026-11-09 확인 기준 2026년 8월까지 게시.
    2) 공공데이터포털 '제주관광공사_제주방문관광객_통계' (https://www.data.go.kr/data/15007317/fileData.do)
       같은 협회 입도통계에 한국관광공사 입국통계를 붙인 엑셀. 한국 입국자(korea_arrivals)를 이 파일에서 가져온다(2024~2025).
       이용조건: 공공저작물 제2유형(출처 표시, 상업적 이용 금지).

결과 컬럼(P-02 관광객 입도(월별·국적별)):
    month, country, segment, jeju_arrivals, korea_arrivals
    segment 는 카드 이용자구분에 맞춘 묶음이다: 중국, 일본, 동남아(싱가포르·말레이시아·인도네시아·베트남·태국), 기타외국(나머지), 내국인관광객.
    엑셀의 '아시아 기타'에 필리핀 등이 섞여 있어 기타외국으로 둔다(카드의 동남아와 조금 다르다).
    korea_arrivals 는 엑셀에 있는 외국인 달만 채우고 나머지는 비워 둔다.
"""
from __future__ import annotations

import csv
import html
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "hub_public" / "jeju_foreign_visitors.xlsx"
OUT = ROOT / "src" / "golmok" / "reference" / "foreign_arrivals_public.csv"
MONTHLY = "https://www.visitjeju.or.kr/issue/stats/monthly.htm?year={year}&month={month:02d}"
PUBLIC_DATA_PK, DETAIL_PK = "15007317", "uddi:0b292ed0-312e-4a03-b911-f3208d418f72"
META = "https://www.data.go.kr/tcs/dss/selectFileDataDownload.do?publicDataPk={pk}&fileDetailSn=1&publicDataDetailPk={detail}"
DOWNLOAD = "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId={file}&fileDetailSn=1"
HEADERS = {"User-Agent": "Mozilla/5.0"}
COUNTRIES = ["일본", "중국", "홍콩", "대만", "싱가포르", "말레이시아", "인도네시아", "베트남", "태국", "기타", "미국", "기타"]
SEGMENT = {"중국": "중국", "일본": "일본", "싱가포르": "동남아", "말레이시아": "동남아", "인도네시아": "동남아", "베트남": "동남아", "태국": "동남아", "내국인": "내국인관광객"}
NUMBER = r"([\d,]+)"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as response:
        return response.read()


def fetch_month(year: int, month: int) -> list[dict] | None:
    """협회 월별통계 한 달. 게시 전이면 None."""
    text = _get(MONTHLY.format(year=year, month=month)).decode("utf-8", "ignore")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text)))
    if "목적별 관광객입도현황" not in text:
        return None
    part = text[text.index("목적별 관광객입도현황"):]
    domestic = int(re.search(r"내국인 소계 월계 " + NUMBER, part).group(1).replace(",", ""))
    foreign_part = part[part.index("외국인 소계"):]
    foreign_total = int(re.search(r"외국인 소계 월계 " + NUMBER, foreign_part).group(1).replace(",", ""))
    rows, cursor = [], 0
    for index, name in enumerate(COUNTRIES):
        found = re.compile(re.escape(name) + r" 월계 " + NUMBER).search(foreign_part, cursor)
        cursor = found.end()
        country = ("아시아 기타" if index == 9 else "서구 기타") if name == "기타" else name
        rows.append({"country": country, "jeju_arrivals": int(found.group(1).replace(",", ""))})
    if sum(r["jeju_arrivals"] for r in rows) != foreign_total:
        raise ValueError(f"{year}-{month:02d} 국적별 합이 외국인 소계 {foreign_total:,} 와 다릅니다")
    rows.append({"country": "내국인", "jeju_arrivals": domestic})
    month_key = f"{year}{month:02d}"
    return [{"month": month_key, "segment": SEGMENT.get(r["country"], "기타외국"), "korea_arrivals": "", **r} for r in rows]


def korea_arrivals() -> dict[tuple[str, str], int]:
    """공공데이터포털 엑셀에서 (달, 국적) → 한국 입국자. 엑셀이 없으면 받는다."""
    import openpyxl

    if not RAW.exists():
        meta = json.loads(_get(META.format(pk=PUBLIC_DATA_PK, detail=DETAIL_PK)))
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_bytes(_get(DOWNLOAD.format(file=meta["atchFileId"])))
    book = openpyxl.load_workbook(RAW, read_only=True, data_only=True)
    found = {}
    for sheet in book.worksheets:
        name = re.fullmatch(r"(\d+)월", sheet.title.strip())
        if not name:
            continue
        years, group = None, None
        for row in sheet.iter_rows(values_only=True):
            if row[3] and str(row[3]).endswith("년") and years is None:
                years = int(str(row[3])[:4]), int(str(row[4])[:4])
                continue
            if row[0]:
                group = str(row[0]).strip()
            label = str(row[1] or "").strip()
            if row[2] != "월계" or years is None or group == "총계" or label in ("", "소계"):
                continue
            country = f"{'아시아' if group == '아시아' else '서구'} 기타" if label == "기타" else label
            for year, value in ((years[0], row[6]), (years[1], row[7])):
                found[(f"{year}{int(name.group(1)):02d}", country)] = int(value or 0)
    return found


def main() -> None:
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    today = date.today()
    rows = []
    for year in range(start, today.year + 1):
        for month in range(1, 13):
            if (year, month) > (today.year, today.month):
                break
            got = fetch_month(year, month)
            time.sleep(0.4)
            if got is None:
                print(f"{year}-{month:02d}: 아직 게시되지 않음", file=sys.stderr)
                break
            rows += got
    korea = korea_arrivals()
    for row in rows:
        row["korea_arrivals"] = korea.get((row["month"], row["country"]), "")
    with open(OUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["month", "country", "segment", "jeju_arrivals", "korea_arrivals"])
        writer.writeheader()
        writer.writerows(rows)
    months = sorted({r["month"] for r in rows})
    print(f"{len(rows)}행, {months[0]} ~ {months[-1]} ({len(months)}개월) -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
