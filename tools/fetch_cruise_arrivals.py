"""제주특별자치도 누리집의 '크루즈선 입항실적' 표를 긁어 월별 CSV 로 만든다.

사용법:
    python tools/fetch_cruise_arrivals.py                 # 2023~올해, src/golmok/reference/cruise_monthly_public.csv 갱신
    python tools/fetch_cruise_arrivals.py 2024 2025 out.csv

출처: 제주특별자치도 경제/투자 > 크루즈선 입항실적 (https://www.jeju.go.kr/industry/economicinfo/logistics/cruiseship.htm)
      입항일시, 선명, 항로(중국발·일본발·국내발 등), 관광객수, 입항항(제주항·강정항) 을 제공한다.
      2026-11-09 확인 기준 2025년 5월 30일까지 올라와 있다. 그 뒤 달은 이용자가 같은 형식으로 넣는다
      (제주관광빅데이터플랫폼 '크루즈 입도객(월별)' CSV 는 회원 로그인 후 내려받을 수 있다).

결과 컬럼(P-01 크루즈 입항 실적(월별)):
    month, calls, passengers, china_calls, china_passengers, jeju_port_calls, gangjeong_calls
"""
from __future__ import annotations

import csv
import html
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

URL = "https://www.jeju.go.kr/industry/economicinfo/logistics/cruiseship.htm?year={year}&page={page}"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "src" / "golmok" / "reference" / "cruise_monthly_public.csv"
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def fetch(year: int, page: int) -> tuple[list[tuple], int]:
    request = urllib.request.Request(URL.format(year=year, page=page), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=40) as response:
        text = response.read().decode("utf-8", "ignore")
    rows = []
    for row in ROW.findall(text):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in CELL.findall(row)]
        if len(cells) == 5 and re.match(r"\d{4}년\d{2}월\d{2}일", cells[0]):
            day = re.sub(r"(\d{4})년(\d{2})월(\d{2})일", r"\1\2\3", cells[0])
            rows.append((day, cells[1], cells[2], int(cells[3].replace(",", "") or 0), cells[4]))
    pages = [int(p) for p in re.findall(r"page=(\d+)", text)]
    return rows, max(pages, default=1)


def scrape(years: list[int]) -> list[tuple]:
    arrivals: list[tuple] = []
    for year in years:
        rows, last = fetch(year, 1)
        page = 2
        while rows and page <= last:
            rows, more = fetch(year, page)
            arrivals += rows
            last, page = max(last, more), page + 1
            time.sleep(0.3)
        arrivals += fetch(year, 1)[0]
        print(f"{year}: {sum(1 for a in arrivals if a[0].startswith(str(year)))}회", file=sys.stderr)
    return sorted(set(arrivals))


def monthly(arrivals: list[tuple]) -> list[list]:
    totals: dict[str, list[int]] = defaultdict(lambda: [0] * 6)
    for day, _ship, route, passengers, port in arrivals:
        total = totals[day[:6]]
        china = route.startswith("중국")
        total[0] += 1
        total[1] += passengers
        total[2] += china
        total[3] += passengers if china else 0
        total[4] += port == "제주항"
        total[5] += port == "강정항"
    return [[month, *values] for month, values in sorted(totals.items())]


def main() -> None:
    args = sys.argv[1:]
    out = Path(args.pop()) if args and args[-1].endswith(".csv") else DEFAULT_OUT
    years = [int(a) for a in args] or list(range(2023, date.today().year + 1))
    rows = monthly(scrape(years))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["month", "calls", "passengers", "china_calls", "china_passengers", "jeju_port_calls", "gangjeong_calls"])
        writer.writerows(rows)
    print(f"{len(rows)}개월 -> {out}")


if __name__ == "__main__":
    main()
