"""센터 PC 에서 실제 CSV 를 처음 넣을 때 돌리는 점검 도구.

사용법 (센터 PC, 프로그램 폴더에서):
    .venv\\Scripts\\python tools\\center_check.py 내려받은CSV폴더
    python tools/center_check.py data/mock_center          # 개발 PC 에서 가상 데이터로 시험

하는 일
    1. 폴더의 CSV 를 하나씩 식별·적재한다 (프로그램과 같은 loader). 실패하면 이유를 적는다.
    2. 숫자여야 하는 컬럼이 숫자로 읽히는지, 지표가 기대하는 코드값(시간대, 매장주 구분, 권역 등)이 실제로 있는지 센다.
    3. 데이터끼리 이어지는지 본다: 외식업 POS 의 (시, 법정동)이 카드 데이터에 있는지, 종량제봉투 행정동이 유동인구에 있는지.
    4. 가장 최근 달로 지표 11종을 계산해 본다.

결과는 화면에만 출력하고 파일로 남기지 않는다. 출력에는 행 수·비율·코드값 이름만 있고 원본 행이나 금액은 없다.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from golmok import metrics  # noqa: E402
from golmok.loader import load_center_csv  # noqa: E402
from golmok.schemas import get_dataset  # noqa: E402

REGIONS = {"제주시 동부", "제주시 중부(동)", "제주시 중부(서)", "제주시 서부", "서귀포시 동부", "서귀포시 중부", "서귀포시 서부"}
USER_TYPES = {"내국인", "제주도민", "법인", "중국", "일본", "동남아", "기타외국"}
# 데이터셋 → {컬럼: 지표가 기대하는 값}. 여기 없는 값이 나오면 알려주고, 기대하는 값이 하나도 없으면 경고한다.
EXPECTED = {
    "1-08": {"dstrct_se": REGIONS, "user_type": USER_TYPES},
    "1-14": {"dstrct_se": REGIONS, "user_type": USER_TYPES},
    "1-17": {"city": {"제주시", "서귀포시"}, "owner_gb": {"내국인 개인", "제주도민 개인", "법인", "외국인", "NA"}},
    "1-19": {"city_nm": {"제주시", "서귀포시"}},
    "4-11": {"dstrct_se": REGIONS, "resd": {"제주", "그외"}},
    "4-15": {"dstrct_se": REGIONS},
    "5-03": {"city_name": {"제주시", "서귀포시"}, "wastebag_type": {"가연성", "불연성", "음식물", "재사용", "공공용"}},
    "P-02": {"segment": {"중국", "일본", "동남아", "기타외국", "내국인관광객"}},
}
MUST_HAVE = {("1-17", "owner_gb"): {"제주도민 개인"}, ("4-11", "resd"): {"그외"}}


def say(level: str, text: str) -> None:
    print(f"  [{level}] {text}")


def check_file(con, path: Path) -> str | None:
    print(f"\n■ {path.name}")
    try:
        result = load_center_csv(con, path)
    except Exception as error:   # 어떤 파일이든 점검은 계속한다
        say("실패", str(error))
        return None
    dataset = result.dataset
    say("확인", f"{dataset.id} {dataset.title}, {result.rows_added:,}행" + (" (같은 파일이 이미 들어 있음)" if result.skipped_duplicate else ""))
    raw = con.execute("SELECT * FROM read_csv(?, header = true, all_varchar = true)", [str(path)]).df()
    for column in dataset.columns:
        if column.type not in ("INT", "BIGINT", "DOUBLE") or column.name not in raw:
            continue
        filled = raw[column.name].fillna("").str.strip()
        filled = filled[filled != ""]
        if filled.empty:
            continue
        numbers = filled.str.replace(",", "", regex=False).str.replace("L", "", regex=False)
        bad = int((~numbers.str.fullmatch(r"-?\d+(\.\d+)?")).sum())
        if bad:
            say("주의", f"{column.name}({column.ko}) 값 {bad:,}개가 숫자가 아닙니다. 예: {filled[~numbers.str.fullmatch(r'-?\d+(\.\d+)?')].iloc[0]!r}")
        if "," in "".join(filled.head(200)):
            say("주의", f"{column.name}({column.ko}) 에 천 단위 쉼표가 있습니다. 외식업 POS 는 계산할 때 지우지만, 다른 데이터라면 개발팀에 알려 주세요")
    for column, expected in EXPECTED.get(dataset.id, {}).items():
        if column not in raw:
            continue
        values = set(raw[column].dropna().str.strip())
        unknown = sorted(values - expected)
        if unknown:
            say("주의", f"{column} 에 예상 밖 값 {len(unknown)}개: {', '.join(unknown[:8])}")
        if not values & expected:
            say("경고", f"{column} 에 지표가 쓰는 값({', '.join(sorted(expected)[:5])} 등)이 하나도 없습니다")
        missing = MUST_HAVE.get((dataset.id, column), set()) - values
        if missing:
            say("경고", f"{column} 에 '{', '.join(sorted(missing))}' 이 없어 관련 지표가 비게 됩니다")
    if dataset.id == metrics.CARD_TIME:
        values = sorted(set(raw["time"].dropna().str.strip()))
        night = con.execute(f'SELECT count(*) FILTER (WHERE {metrics.NIGHT_CONDITION}), count(*) FROM "{dataset.table}"').fetchone()
        say("확인" if night[0] else "경고", f"시간대 값 {', '.join(values[:8])} 중 저녁·밤으로 읽은 행 {night[0]:,} / {night[1]:,}")
    months = metrics.loaded_months(con, dataset.id)
    say("확인", f"들어 있는 달: {months[0]} ~ {months[-1]} ({len(months)}개월)" if months else "달을 읽지 못했습니다")
    return dataset.id


def share(con, sql: str) -> float | None:
    row = con.execute(sql).fetchone()
    return None if row is None or row[1] in (None, 0) else row[0] / row[1] * 100


def check_links(con, loaded: set[str]) -> None:
    print("\n■ 데이터끼리 이어지는지")
    table = lambda dataset_id: get_dataset(dataset_id).table   # noqa: E731
    if {"1-19", "1-08"} <= loaded:
        value = share(con, f"""SELECT count(*) FILTER (WHERE c.bjd IS NOT NULL), count(*) FROM (SELECT DISTINCT city_nm, bjd_nm FROM "{table('1-19')}") p
                               LEFT JOIN (SELECT DISTINCT city, bjd FROM "{table('1-08')}") c ON p.city_nm = c.city AND p.bjd_nm = c.bjd""")
        say("확인" if value and value >= 90 else "경고", f"외식업 POS 법정동 중 카드 데이터에서 권역을 찾은 비율 {value:.1f}% (못 찾은 동은 동네 식당 체감에서 빠집니다)" if value is not None else "외식업 POS 법정동을 읽지 못했습니다")
    if {"5-03", "4-15"} <= loaded:
        value = share(con, f"""SELECT count(*) FILTER (WHERE f.emd_nm IS NOT NULL), count(*) FROM (SELECT DISTINCT city_name || ' ' || emd_name AS emd_nm FROM "{table('5-03')}") w
                               LEFT JOIN (SELECT DISTINCT emd_nm FROM "{table('4-15')}") f USING (emd_nm)""")
        say("확인" if value and value >= 90 else "경고", f"종량제봉투 행정동 중 유동인구에서 찾은 비율 {value:.1f}%" if value is not None else "종량제봉투 행정동을 읽지 못했습니다")
    if "1-17" in loaded and "1-08" in loaded:
        value = share(con, f"""SELECT count(*) FILTER (WHERE s.biz_cd IS NOT NULL), count(*) FROM (SELECT DISTINCT biz_cd FROM "{table('1-08')}") c
                               LEFT JOIN (SELECT DISTINCT biz_cd FROM "{table('1-17')}") s USING (biz_cd)""")
        say("확인" if value and value >= 90 else "경고", f"카드 업종 중 매장 수 데이터에 있는 비율 {value:.1f}%")


def check_metrics(con) -> None:
    months = metrics.loaded_months(con, metrics.CARD_MONTHLY)
    if not months:
        print("\n■ 지표 계산: 카드 이용 현황(읍면동)_v02 가 없어 건너뜁니다")
        return
    month = months[-1]
    print(f"\n■ 지표 계산 ({month[:4]}년 {int(month[4:])}월 기준)")
    runs = [
        ("매출 증감률", metrics.sales_yoy, (month, "bjd", None, "외국인")),
        ("객단가 증감률", metrics.ticket_size_yoy, (month, "bjd", None, "외국인")),
        ("소비 비중", metrics.spend_share, (month, "bjd", None, "외국인")),
        ("내국인 공백 메우기", metrics.gap_fill, (month, "bjd", None)),
        ("낙수 지수", metrics.conversion_index, (month, None, "외국인")),
        ("쓰레기 부담", metrics.waste_burden, (month, "emd", None)),
        ("크루즈 기항 효과", metrics.cruise_lift, (month, "bjd", None, "중국")),
        ("저녁·밤 소비", metrics.night_share, (month, "bjd", None, "외국인")),
        ("입도객 1인당 소비", metrics.per_visitor, (month, "bjd", None, "외국인")),
        ("동네 식당 체감", metrics.restaurant_pulse, (month, "bjd")),
        ("도민 가게 몫", metrics.local_owner, (month, "city", None, "외국인")),
    ]
    for name, function, args in runs:
        try:
            frame = function(con, *args).table.frame
            hidden = int(frame["비공개"].sum())
            say("확인" if len(frame) > hidden else "경고", f"{name}: {len(frame)}개 지역, 비공개 {hidden}개")
        except metrics.MissingData as error:
            say("없음", f"{name}: {error}")
        except ValueError as error:
            say("경고", f"{name}: {error}")
        except Exception as error:
            say("오류", f"{name}: {type(error).__name__}: {error}")
            traceback.print_exc(limit=1)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("사용법: python tools/center_check.py <CSV 폴더 또는 파일...>")
    paths = []
    for argument in sys.argv[1:]:
        path = Path(argument)
        paths += sorted(path.glob("*.csv")) if path.is_dir() else [path]
    con = duckdb.connect(":memory:")
    for bundled in ("cruise_monthly_public.csv", "foreign_arrivals_public.csv"):   # 프로그램과 같게 공개 데이터를 먼저 넣는다
        load_center_csv(con, ROOT / "src" / "golmok" / "reference" / bundled)
    loaded = {"P-01", "P-02"} | {dataset_id for path in paths if (dataset_id := check_file(con, path))}
    check_links(con, loaded)
    check_metrics(con)
    print("\n[경고]·[오류] 줄을 개발팀에 알려 주세요. 이 출력에는 원본 행이나 금액이 없습니다.")


if __name__ == "__main__":
    main()
