"""소상공인시장진흥공단 상가(상권)정보에서 제주 매장 수를 행정동·업종별로 센다.

사용법:
    python tools/import_sangga.py [제주 CSV 경로]

원본: 공공데이터포털 '소상공인시장진흥공단_상가(상권)정보' (https://www.data.go.kr/data/15083033/fileData.do)
      분기마다 갱신되는 zip 안의 '..._제주_YYYYMM.csv' (UTF-8). 이용허락범위 제한 없음.
      경로를 주지 않으면 data/sangga_src/jeju/ 에서 찾는다.
결과: src/golmok/reference/sangga_stores_public.csv (P-05 제주 상가 매장 수)
      가게 하나하나(상호·주소)는 넣지 않고 (행정동, 법정동, 표준산업분류) 별 매장 수만 남긴다.
      카드 업종코드가 표준산업분류라 업종 앞자리(I56, G47130 등)로 같이 거를 수 있다.
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data" / "sangga_src" / "jeju"
OUT = ROOT / "src" / "golmok" / "reference" / "sangga_stores_public.csv"
ORDER = ["snapshot", "city", "emd_cd", "emd_nm", "bjd_nm", "category", "ksic_cd", "stores"]


def main() -> None:
    paths = [Path(a).expanduser() for a in sys.argv[1:]] or sorted(SOURCE_DIR.glob("*제주*.csv"))
    if not paths:
        raise SystemExit(f"제주 상가정보 CSV 를 찾지 못했습니다: {SOURCE_DIR}")
    path = paths[-1]
    found = re.search(r"(\d{6})\.csv$", path.name)
    snapshot = found.group(1) if found else ""
    counts: Counter = Counter()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["시도명"] != "제주특별자치도":
                continue
            counts[(row["시군구명"], row["행정동코드"], row["행정동명"], row["법정동명"], row["상권업종대분류명"], row["표준산업분류코드"])] += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(ORDER)
        for key, stores in sorted(counts.items()):
            writer.writerow([snapshot, *key, stores])
    print(f"{path.name}: 매장 {sum(counts.values()):,}곳, 행정동 {len({k[2] for k in counts})}개, {len(counts):,}행 -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
