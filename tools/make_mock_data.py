"""센터 반출 불가 데이터와 같은 양식의 가상 CSV 를 만든다.

사용법:
    python tools/make_mock_data.py            # data/mock_center/ 에 생성

실제 센터 데이터는 제주빅데이터센터 방문자만 볼 수 있으므로, 개발·시연·재현용으로
명세서(v12)와 같은 컬럼·코드값을 가진 가상 데이터를 만든다. 수치는 전부 난수이며 실제 통계가 아니다.

생성 대상
    1-08 카드 이용 현황(읍면동)_v02            2025-05 ~ 2026-10
    4-15 단기체류 외국인 유동인구(읍면동)_v02   2025-05 ~ 2026-10, 월 2일(평일 1, 토요일 1), 국적 8개
    4-11 내국인 유동인구(읍면동)_v02           2025-05 ~ 2026-10, 월 2일
    5-03 종량제봉투판매실적                     2025-05 ~ 2026-10, 일별
    P-01 크루즈 입항 실적(월별)                 2025-06 ~ 2026-10 (2025-05 까지는 프로그램에 든 실제 공개 자료를 쓴다)
    1-14 카드 이용 현황(시간대)_v02            2025-05 ~ 2026-10, 1-08 을 시간대 5개로 나눈 것 (연령·성별은 합쳐 NA)
    1-19 외식업 포스 이용 현황(읍면동)          2025-05 ~ 2026-10
    1-17 제주도 내 업종별 매장 수               2025-05 ~ 2026-10
    P-02 관광객 입도(월별·국적별)               프로그램에 든 실제 자료(2026-09 까지) 다음 달 ~ 2026-10

가상 데이터에 심어 둔 모양 (보도된 흐름을 흉내 낸 것이며 실제 수치가 아니다)
    - 내국인 관광객 소비는 2026-05 부터 전년 대비 감소(5~10월 -7~-13%).
    - 중국 관광객 소비는 늘지만 면세·대형소매와 제주항·강정항 주변 동네에 몰리고, 크루즈가 많이 온 달에 커진다.
    - 우도면·성산읍은 외국인 방문이 2배 이상 늘지만 소비는 그만큼 늘지 않는다(낙수 공백).
    - 외국인 방문이 급증한 읍면은 종량제봉투 판매도 늘어난다(쓰레기 부담).
    - 크루즈·단체 관광객은 낮에만 쓰고(저녁·밤 비중이 낮다), 개별 관광객은 저녁·밤에도 쓴다. 우도·성산은 당일치기라 저녁·밤 비중이 낮다.
    - 동네 식당(POS) 가게당 매출은 내국인 감소를 따라 줄고, 외국인 음식점 소비가 몰리는 도심 동네만 버틴다.
    - 면세점·대형 소매는 법인 매장이 대부분이고, 음식점은 제주도민 개인 매장이 많다(도민 가게 몫).
    - 실제 입도통계가 없는 2026년 달은 2025년 같은 달 실제값에 중국 +18%, 대만·홍콩 +15%, 그 밖 +8%, 내국인은 감소율을 곱한다(난수).

가정 (명세서에 없어서 임의로 정한 것 — 실제 데이터에는 각 행에 값이 들어 있으므로 Agent 는 이 가정에 의존하지 않는다)
    - 법정동·행정동이 어느 권역(dstrct_se)에 속하는지: 아래 REGION_OF 규칙
"""
from __future__ import annotations

import calendar
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from golmok.schemas import get_dataset  # noqa: E402

OUT_DIR = ROOT / "data" / "mock_center"
SEED = 20261120
MONTHS = [f"{y}{m:02d}" for y, m in [(2025, m) for m in range(5, 13)] + [(2026, m) for m in range(1, 11)]]
BIZ_PER_AREA = 8

# 읍면과 서귀포시 동 지역. 여기 없는 법정동은 제주시 동 지역이다. 월평동은 두 시에 모두 있다.
EUPMYEON = {
    "한림읍": "제주시 서부", "애월읍": "제주시 서부", "한경면": "제주시 서부", "추자면": "제주시 서부",
    "구좌읍": "제주시 동부", "조천읍": "제주시 동부", "우도면": "제주시 동부",
    "대정읍": "서귀포시 서부", "안덕면": "서귀포시 서부",
    "남원읍": "서귀포시 동부", "성산읍": "서귀포시 동부", "표선면": "서귀포시 동부",
}
SEOGWIPO_EUPMYEON = {"대정읍", "안덕면", "남원읍", "성산읍", "표선면"}
SEOGWIPO_DONG = {
    "서귀동", "법환동", "서호동", "호근동", "동홍동", "서홍동", "상효동", "하효동", "신효동", "보목동", "토평동",
    "중문동", "회수동", "대포동", "월평동", "강정동", "도순동", "하원동", "색달동", "상예동", "하예동", "영남동",
}
JEJU_WEST_PREFIXES = ("연동", "노형", "외도", "이호", "도두", "도평", "해안", "내도", "용담", "오라")
TOURIST_AREAS = {"애월읍", "한림읍", "구좌읍", "성산읍", "안덕면", "표선면", "중문동", "색달동", "서귀동", "연동", "우도면"}
# 중국 관광객 소비가 몰리는 동네(면세점·크루즈 항구 주변)와, 사람은 많이 오는데 소비는 적은 동네
CHINA_SPEND_AREAS = {"연동": 3.0, "노형동": 2.2, "건입동": 2.5, "일도일동": 1.8, "서귀동": 1.6, "강정동": 1.4, "중문동": 1.5}
FOREIGN_SURGE_AREAS = {"우도면": 2.6, "성산읍": 1.9, "조천읍": 1.4}   # 2026년 외국인 방문 증가 배수(전년 대비)
CRUISE_AREAS = {"건입동", "일도일동", "용담일동", "용담이동", "강정동", "서귀동", "법환동"}   # 항구 주변
DUTY_FREE_BIZ = ("G47130",)   # 면세점

# 내국인 관광객 전년 동월 대비 감소율 (2026년 5~10월, 언론 보도 수치)
TOURIST_DECLINE = {"202605": 0.071, "202606": 0.102, "202607": 0.055, "202608": 0.023, "202609": 0.081, "202610": 0.127}
SEASONALITY = {1: 0.92, 2: 0.9, 3: 0.97, 4: 1.03, 5: 1.08, 6: 1.0, 7: 1.1, 8: 1.15, 9: 1.0, 10: 1.07, 11: 0.95, 12: 0.93}
# 가상 크루즈 입항(2025-06 이후): (입항 횟수, 관광객, 중국발 횟수, 중국발 관광객, 제주항, 강정항). 2025년 75만·2026년 80만 명 보도 규모에 맞춘 난수 기준값
CRUISE_BASE = {
    "202506": (30, 72000, 20, 55000, 12, 18), "202507": (26, 60000, 19, 46000, 11, 15), "202508": (28, 68000, 22, 54000, 12, 16),
    "202509": (27, 62000, 22, 50000, 11, 16), "202510": (34, 84000, 24, 62000, 14, 20), "202511": (25, 55000, 19, 42000, 10, 15),
    "202512": (20, 42000, 17, 36000, 8, 12), "202601": (22, 46000, 18, 39000, 8, 14), "202602": (26, 58000, 21, 48000, 9, 17),
    "202603": (30, 66000, 22, 52000, 11, 19), "202604": (33, 78000, 24, 60000, 12, 21), "202605": (36, 90000, 27, 70000, 14, 22),
    "202606": (33, 76000, 26, 62000, 12, 21), "202607": (29, 64000, 23, 52000, 11, 18), "202608": (31, 72000, 25, 60000, 12, 19),
    "202609": (30, 68000, 24, 56000, 12, 18), "202610": (38, 96000, 29, 76000, 15, 23),
}

# (user_type, tour_type, 연령대 목록, 성별 목록, 규모, 관광 여부)
SEGMENTS = [
    ("제주도민", "도민", ["20", "40", "60"], ["남성", "여성"], 1.0, False),
    ("내국인", "개별", ["20", "40", "60"], ["남성", "여성"], 0.8, True),
    ("내국인", "단체", ["NA"], ["남성", "여성"], 0.15, True),
    ("법인", "NA", ["NA"], ["알수없음"], 0.5, False),
    ("중국", "개별", ["NA"], ["알수없음"], 0.20, True),
    ("중국", "단체", ["NA"], ["알수없음"], 0.14, True),
    ("일본", "개별", ["NA"], ["알수없음"], 0.05, True),
    ("동남아", "개별", ["NA"], ["알수없음"], 0.06, True),
    ("기타외국", "개별", ["NA"], ["알수없음"], 0.04, True),
]
FOREIGN_TYPES = ("중국", "일본", "동남아", "기타외국")
# 단기체류 외국인 유동인구 국적과 비중
NATIONALITIES = {"CHN": 0.62, "TWN": 0.10, "JPN": 0.06, "USA": 0.05, "VNM": 0.04, "THA": 0.04, "MYS": 0.04, "SGP": 0.05}
BAG_TYPES = {"가연성": {"10L": 0.25, "20L": 0.45, "50L": 0.30}, "음식물": {"2L": 0.5, "5L": 0.5}, "재사용": {"20L": 1.0}, "공공용": {"50L": 1.0}}
BAG_TYPE_SHARE = {"가연성": 0.62, "음식물": 0.22, "재사용": 0.10, "공공용": 0.06}
BAG_PRICE_PER_LITER = 24


def ticket_price(biz_cd: str) -> int:
    """업종별 건당 평균 결제액(원) 가정."""
    if biz_cd.startswith("I56"):
        return 25_000
    if biz_cd.startswith("I55"):
        return 120_000
    if biz_cd in ("R91121", "L69110"):
        return 160_000
    if biz_cd in DUTY_FREE_BIZ:
        return 90_000
    return 35_000


def region_of(name: str, seogwipo: bool) -> str:
    return EUPMYEON.get(name) or ("서귀포시 중부" if seogwipo else "제주시 중부(서)" if name.startswith(JEJU_WEST_PREFIXES) else "제주시 중부(동)")


def card_areas() -> pd.DataFrame:
    """카드 데이터의 지역 목록: (city, bjd) 와 권역·도심구분."""
    names = [row[0] for row in get_dataset("1-08").reference("법정동").rows]
    areas = []
    for bjd in names:
        if bjd in EUPMYEON:
            city = "서귀포시" if bjd in SEOGWIPO_EUPMYEON else "제주시"
            areas.append((city, bjd, EUPMYEON[bjd], "읍면"))
            continue
        if bjd in SEOGWIPO_DONG:
            areas.append(("서귀포시", bjd, "서귀포시 중부", "도시"))
        if bjd not in SEOGWIPO_DONG or bjd == "월평동":
            areas.append(("제주시", bjd, region_of(bjd, False), "도시"))
    return pd.DataFrame(areas, columns=["city", "bjd", "dstrct_se", "area_type"])


def card_cells(rng: np.random.Generator) -> pd.DataFrame:
    """(지역 × 업종 × 이용자 구분 × 연령 × 성별) 칸과 월평균 이용자수."""
    businesses = pd.DataFrame(get_dataset("1-08").reference("업종코드").rows, columns=["biz_cd", "biz_nm"])
    rows = []
    for area in card_areas().itertuples(index=False):
        area_size = rng.lognormal(mean=3.0, sigma=0.9) * (2.0 if area.area_type == "도시" else 1.0)
        tourist_pull = 2.5 if area.bjd in TOURIST_AREAS else 0.6
        china_pull = CHINA_SPEND_AREAS.get(area.bjd, 0.35 if area.bjd in FOREIGN_SURGE_AREAS else 0.5)
        picked = businesses.sample(BIZ_PER_AREA, random_state=int(rng.integers(1 << 31)))
        if area.bjd in CHINA_SPEND_AREAS and not picked["biz_cd"].isin(DUTY_FREE_BIZ).any():
            picked = pd.concat([picked.iloc[1:], businesses[businesses["biz_cd"].isin(DUTY_FREE_BIZ)]])
        for biz in picked.itertuples(index=False):
            biz_size = rng.lognormal(mean=0.0, sigma=0.7)
            for user_type, tour_type, ages, sexes, scale, is_tourist in SEGMENTS:
                pull = tourist_pull if is_tourist else 1.0
                if user_type == "중국":
                    pull = china_pull * (2.5 if biz.biz_cd in DUTY_FREE_BIZ else 1.0) * (1.6 if tour_type == "단체" and area.bjd in CRUISE_AREAS else 1.0)
                for age in ages:
                    for sex in sexes:
                        mean_users = area_size * biz_size * scale * pull / len(ages)
                        rows.append((*area, biz.biz_cd, biz.biz_nm, user_type, tour_type, age, sex, is_tourist, mean_users))
    columns = ["city", "bjd", "dstrct_se", "area_type", "biz_cd", "biz_nm", "user_type", "tour_type", "age", "sex", "is_tourist", "mean_users"]
    return pd.DataFrame(rows, columns=columns)


def cruise_month_factor(month: str, cruise: pd.DataFrame) -> float:
    """그 달 중국발 크루즈 관광객이 평균보다 얼마나 많은지 (1 = 평균)."""
    row = cruise[cruise["month"] == month]
    if row.empty:
        return 1.0
    return float(row["china_passengers"].iloc[0] / cruise["china_passengers"].mean())


def month_factor(month: str, cells: pd.DataFrame, cruise: pd.DataFrame) -> np.ndarray:
    """계절성 × 이용자 구분별 추세 × 크루즈 효과."""
    user_type, tour_type, bjd = cells["user_type"], cells["tour_type"], cells["bjd"]
    factor = np.full(len(cells), SEASONALITY[int(month[4:])])
    years_on = (int(month[:4]) - 2025) + (int(month[4:]) - 5) / 12   # 2025-05 를 0 으로 둔 경과 연수
    if month >= "202605":
        factor = np.where(user_type == "내국인", factor * (1 - TOURIST_DECLINE[month]), factor)
        factor = np.where(user_type == "제주도민", factor * 0.98, factor)
    factor = np.where(user_type == "중국", factor * (1 + 0.30 * years_on), factor)
    factor = np.where(user_type.isin(["동남아", "기타외국"]), factor * (1 + 0.25 * years_on), factor)
    factor = np.where(user_type == "일본", factor * (1 + 0.05 * years_on), factor)
    # 방문은 급증하지만 소비는 조금만 느는 동네
    surge = bjd.map(FOREIGN_SURGE_AREAS).fillna(1.0).to_numpy()
    factor = np.where(user_type.isin(FOREIGN_TYPES) & (month >= "202601"), factor * (1 + 0.25 * (surge - 1)), factor)
    # 크루즈가 많이 온 달에는 항구 주변 중국 단체 소비가 함께 움직인다
    cruise_boost = cruise_month_factor(month, cruise)
    on_cruise = (user_type == "중국") & (tour_type == "단체")
    factor = np.where(on_cruise & bjd.isin(CRUISE_AREAS), factor * cruise_boost, factor)
    factor = np.where(on_cruise & ~bjd.isin(CRUISE_AREAS), factor * (0.6 + 0.4 * cruise_boost), factor)
    return factor


def add_measures(frame: pd.DataFrame, users: np.ndarray, rng: np.random.Generator) -> pd.DataFrame:
    """이용자수에서 이용건수·이용금액을 만들고, 이용자가 0명인 칸은 뺀다(실제 데이터에도 없는 칸)."""
    frame = frame.assign(user_cnt=users)
    frame = frame[frame["user_cnt"] > 0].copy()
    frame["use_cnt"] = frame["user_cnt"] + rng.poisson(0.15 * frame["user_cnt"])
    price = frame["biz_cd"].map(ticket_price) * rng.lognormal(0.0, 0.25, len(frame))
    # 중국 관광객 1인당 지출이 줄어드는 흐름(188→122달러 보도)을 흉내 낸다
    price = np.where((frame["user_type"] == "중국") & (frame["base_year_month"] >= "202601"), price * 0.88, price) if "base_year_month" in frame else price
    frame["use_amt"] = (frame["use_cnt"] * price // 10 * 10).astype("int64")
    return frame


def make_card_monthly(cells: pd.DataFrame, cruise: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    parts = []
    for month in MONTHS:
        users = rng.poisson(cells["mean_users"].to_numpy() * month_factor(month, cells, cruise))
        part = cells.assign(base_year_month=month, pt_year=month[:4], pt_month=str(int(month[4:])))
        parts.append(add_measures(part, users, rng))
    return pd.concat(parts, ignore_index=True)


def districts_table(rng: np.random.Generator) -> pd.DataFrame:
    """행정동 43개와 권역·거주 규모·관광 흡인력."""
    spec = get_dataset("4-11")
    districts = pd.DataFrame(spec.reference("행정동").rows, columns=["emd_cd", "emd_nm"])
    short_name = districts["emd_nm"].str.split().str[-1]
    is_seogwipo = districts["emd_nm"].str.startswith("서귀포시")
    districts["city"] = np.where(is_seogwipo, "서귀포시", "제주시")
    districts["short"] = short_name
    districts["dstrct_se"] = [region_of(name, seogwipo) for name, seogwipo in zip(short_name, is_seogwipo)]
    districts["residents"] = rng.lognormal(8.3, 0.7, len(districts))
    districts["visit_pull"] = np.where(short_name.isin(TOURIST_AREAS), 0.45, 0.12)
    districts["foreign_pull"] = [CHINA_SPEND_AREAS.get(n, 0.0) * 0.08 + (0.25 if n in TOURIST_AREAS else 0.05) for n in short_name]
    districts["foreign_surge"] = short_name.map(FOREIGN_SURGE_AREAS).fillna(1.0)
    return districts


def sample_days(month: str) -> list[tuple[date, float]]:
    """월마다 평일 하루와 토요일 하루."""
    year, month_number = int(month[:4]), int(month[4:])
    first = date(year, month_number, 1)
    saturday = next(first + timedelta(days=d) for d in range(7) if (first + timedelta(days=d)).weekday() == 5)
    return [(saturday + timedelta(days=4), 1.0), (saturday, 1.3)]


def make_population(districts: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """내국인 유동인구: 월마다 평일 하루와 토요일 하루, 01~24시."""
    hours = np.arange(1, 25)
    daytime = np.exp(-((hours - 14) ** 2) / 30)  # 낮에 방문·근무 인구가 늘어나는 모양
    parts = []
    for month in MONTHS:
        decline = 1 - TOURIST_DECLINE.get(month, 0.0)
        for day, weekend_boost in sample_days(month):
            grid = districts.merge(pd.DataFrame({"time": hours, "daytime": daytime}), how="cross")
            grid = grid.merge(pd.DataFrame({"sex": ["남성", "여성"]}), how="cross")
            grid = grid.merge(pd.DataFrame({"age": ["2024", "4044"]}), how="cross")
            base = grid["residents"] / 4 * rng.lognormal(0, 0.05, len(grid))
            for resd in ("제주", "그외"):
                part = grid[["time", "dstrct_se", "emd_cd", "emd_nm", "sex", "age"]].copy()
                if resd == "제주":
                    part["resd_pop_cnt"] = base * (1 - 0.35 * grid["daytime"])
                    part["work_pop_cnt"] = base * 0.3 * grid["daytime"]
                    part["visit_pop_cnt"] = base * 0.1 * grid["daytime"] * weekend_boost
                else:  # 제주 외 거주자 = 관광객 중심
                    part["resd_pop_cnt"] = 0.0
                    part["work_pop_cnt"] = base * 0.01 * grid["daytime"]
                    part["visit_pop_cnt"] = base * grid["visit_pull"] * (0.4 + grid["daytime"]) * weekend_boost * SEASONALITY[int(month[4:])] * decline
                part["resd"] = resd
                part["base_date"] = day.strftime("%Y%m%d")
                part["pt_year"], part["pt_month"] = month[:4], str(int(month[4:]))
                parts.append(part)
    population = pd.concat(parts, ignore_index=True)
    for column in ("resd_pop_cnt", "work_pop_cnt", "visit_pop_cnt"):
        population[column] = population[column].round(4)
    population["time"] = population["time"].astype(str)
    return population


def make_foreign_population(districts: pd.DataFrame, cruise: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """단기체류 외국인 유동인구: 월마다 평일·토요일 하루씩, 01~24시, 국적 8개."""
    hours = np.arange(1, 25)
    daytime = np.exp(-((hours - 13) ** 2) / 22)
    nationality = pd.DataFrame({"nationality": list(NATIONALITIES), "share": list(NATIONALITIES.values())})
    parts = []
    for month in MONTHS:
        years_on = (int(month[:4]) - 2025) + (int(month[4:]) - 5) / 12
        cruise_boost = cruise_month_factor(month, cruise)
        for day, weekend_boost in sample_days(month):
            grid = districts.merge(pd.DataFrame({"time": hours, "daytime": daytime}), how="cross").merge(nationality, how="cross")
            level = grid["residents"] * grid["foreign_pull"] * grid["share"] * (0.3 + grid["daytime"]) * weekend_boost * SEASONALITY[int(month[4:])]
            level = level * (1 + 0.15 * years_on)                                   # 외국인 전반 증가
            level = np.where((grid["nationality"] == "CHN") & (month >= "202601"), level * grid["foreign_surge"], level)   # 우도·성산 급증
            level = np.where(grid["nationality"] == "CHN", level * (1 + 0.5 * (cruise_boost - 1)), level)                    # 크루즈 달
            part = grid[["time", "dstrct_se", "nationality", "emd_cd", "emd_nm"]].copy()
            part["visit_pop_cnt"] = (level * rng.lognormal(0, 0.08, len(grid))).round(4)
            part["base_date"] = day.strftime("%Y%m%d")
            part["pt_year"], part["pt_month"] = month[:4], str(int(month[4:]))
            parts.append(part)
    frame = pd.concat(parts, ignore_index=True)
    frame["time"] = frame["time"].astype(str)
    return frame


def make_waste_bags(districts: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """종량제봉투 판매실적: 행정동별 일별, 봉투 종류·용량별 판매건수와 금액."""
    rows = []
    for month in MONTHS:
        year, month_number = int(month[:4]), int(month[4:])
        surge_on = month >= "202601"
        for district in districts.itertuples(index=False):
            visitors = district.visit_pull * 1800 * SEASONALITY[month_number] * (1 - TOURIST_DECLINE.get(month, 0.0))
            foreign = district.foreign_pull * 2200 * SEASONALITY[month_number] * (district.foreign_surge if surge_on else 1.0)
            daily_liters = (district.residents * 0.9 + visitors * 1.3 + foreign * 1.6)   # 하루 판매 리터 규모
            for day in range(1, calendar.monthrange(year, month_number)[1] + 1):
                weekend = date(year, month_number, day).weekday() >= 5
                total = daily_liters * (1.25 if weekend else 0.95) * rng.lognormal(0, 0.1)
                for bag_type, liters in BAG_TYPES.items():
                    for liter, share in liters.items():
                        volume = int(liter[:-1])
                        count = int(rng.poisson(total * BAG_TYPE_SHARE[bag_type] * share / volume))
                        if count:
                            rows.append((month[:4], str(month_number), f"{year}{month_number:02d}{day:02d}", district.city, district.short, bag_type, liter, count, count * volume * BAG_PRICE_PER_LITER))
    return pd.DataFrame(rows, columns=["pt_year", "pt_month", "base_date", "city_name", "emd_name", "wastebag_type", "wastebag_liter", "sale_count", "sale_amount"])


def make_cruise(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for month, base in CRUISE_BASE.items():
        noise = rng.normal(1.0, 0.06)
        calls, passengers, china_calls, china_passengers, jeju, gangjeong = base
        rows.append((month, calls, int(passengers * noise), china_calls, int(china_passengers * noise), jeju, gangjeong))
    return pd.DataFrame(rows, columns=["month", "calls", "passengers", "china_calls", "china_passengers", "jeju_port_calls", "gangjeong_calls"])


# ---------- 시간대·POS·매장 수·입도객 ----------
TIMES = ["오전", "점심", "오후", "저녁", "밤"]
# 이용자 구분·관광구분별 시간대 비중 (오전, 점심, 오후, 저녁, 밤)
TIME_PROFILE = {
    "단체": [0.30, 0.30, 0.30, 0.07, 0.03],
    "개별": [0.15, 0.24, 0.27, 0.22, 0.12],
    "도민": [0.17, 0.25, 0.24, 0.22, 0.12],
    "NA": [0.25, 0.25, 0.35, 0.12, 0.03],
}
NIGHT_BIZ_PREFIXES = ("I5621", "I5622")   # 주점·카페는 저녁·밤 비중이 높다
DAY_TRIP_AREAS = {"우도면", "성산읍"}
POS_BIZ = [("Q01", "간이주점", "Q01001", "호프/맥주"), ("Q02", "고기요리", "Q02003", "돼지고기구이"), ("Q05", "분식", "Q05001", "김밥/분식"),
           ("Q07", "일식/수산물", "Q07004", "횟집"), ("Q09", "중식", "Q09001", "중국음식"), ("Q10", "한식", "Q10011", "한식백반"), ("Q12", "제과/커피", "Q12002", "커피전문점")]
# 업종별 매장주 구분 비중 (내국인 개인, 제주도민 개인, 법인, 외국인, NA)
OWNER_TYPES = ["내국인 개인", "제주도민 개인", "법인", "외국인", "NA"]


def owner_profile(biz_cd: str) -> list[float]:
    if biz_cd == "G47130" or biz_cd.startswith(("G4711", "G4719")):   # 면세점·대형 종합소매
        return [0.04, 0.06, 0.86, 0.02, 0.02]
    if biz_cd.startswith("I55"):
        return [0.25, 0.35, 0.33, 0.04, 0.03]
    if biz_cd.startswith("I56"):
        return [0.22, 0.66, 0.08, 0.01, 0.03]
    if biz_cd.startswith("G47"):
        return [0.18, 0.47, 0.30, 0.02, 0.03]
    return [0.2, 0.45, 0.30, 0.02, 0.03]


def make_card_time(card: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """1-08 을 (달·지역·업종·이용자 구분·관광구분) 으로 합친 뒤 시간대 5개로 나눈다."""
    keys = ["base_year_month", "pt_year", "pt_month", "dstrct_se", "bjd", "biz_cd", "biz_nm", "user_type", "tour_type"]
    base = card.groupby(keys, as_index=False)[["user_cnt", "use_cnt", "use_amt"]].sum()
    profile = np.array([TIME_PROFILE.get(t, TIME_PROFILE["NA"]) for t in base["tour_type"]])
    night = base["biz_cd"].str.startswith(NIGHT_BIZ_PREFIXES).to_numpy()
    profile[night] = profile[night] * [0.6, 0.8, 0.9, 1.8, 2.6]
    # 개별 외국인 관광객은 해가 갈수록 저녁·밤에 조금 더 쓴다. 당일치기 동네는 저녁·밤이 거의 없다
    later = (base["base_year_month"] >= "202601").to_numpy() & base["user_type"].isin(FOREIGN_TYPES).to_numpy() & (base["tour_type"] == "개별").to_numpy()
    profile[later] = profile[later] * [0.95, 0.95, 0.95, 1.15, 1.2]
    day_trip = base["bjd"].isin(DAY_TRIP_AREAS).to_numpy()
    profile[day_trip] = profile[day_trip] * [1.2, 1.2, 1.2, 0.35, 0.2]
    profile = profile / profile.sum(axis=1, keepdims=True)
    parts = []
    for index, time in enumerate(TIMES):
        share = profile[:, index] * rng.lognormal(0, 0.08, len(base))
        users = rng.binomial(base["user_cnt"].to_numpy(), np.clip(share, 0, 1))
        part = base[keys].assign(time=time, age="NA", sex="알수없음", user_cnt=users)
        ratio = np.divide(users, base["user_cnt"].to_numpy(), out=np.zeros(len(base)), where=base["user_cnt"].to_numpy() > 0)
        part["use_cnt"] = np.maximum(users, np.round(base["use_cnt"].to_numpy() * ratio)).astype("int64")
        part["use_amt"] = (base["use_amt"].to_numpy() * ratio * rng.lognormal(0, 0.05, len(base)) // 10 * 10).astype("int64")
        parts.append(part[part["user_cnt"] > 0])
    return pd.concat(parts, ignore_index=True)


def make_pos_food(rng: np.random.Generator) -> pd.DataFrame:
    """외식업 POS: (시, 법정동) × 음식 업종 7개, 달별 총매출·표본 가게 수·이용건수."""
    areas = card_areas()
    rows = []
    for index, area in enumerate(areas.itertuples(index=False)):
        city_cd = "5013" if area.city == "서귀포시" else "5011"
        bjd_cd = f"{city_cd}{index:04d}"
        size = rng.lognormal(2.6, 0.7) * (1.6 if area.area_type == "도시" else 0.8)
        urban_hold = 1.0 + 0.10 * (CHINA_SPEND_AREAS.get(area.bjd, 0) > 0)   # 외국인 음식 소비가 몰리는 도심
        surge = FOREIGN_SURGE_AREAS.get(area.bjd, 1.0)
        for mid_cd, mid_nm, small_cd, small_nm in POS_BIZ:
            stores = max(1, int(size * rng.lognormal(0, 0.5)))
            per_store = rng.lognormal(16.6, 0.3)   # 가게당 월 매출 1,600만 원 안팎
            for month in MONTHS:
                factor = SEASONALITY[int(month[4:])] * rng.lognormal(0, 0.04)
                if month >= "202605":
                    factor *= (1 - 0.7 * TOURIST_DECLINE[month]) * urban_hold
                if month >= "202601":
                    factor *= 1 + 0.03 * (surge - 1)   # 방문 급증 동네도 식당 매출은 조금만 는다
                count = max(1, int(stores * rng.uniform(0.95, 1.05)))
                sale = int(count * per_store * factor)
                rows.append((month[:4], str(int(month[4:])), month, city_cd, area.city, bjd_cd, area.bjd, mid_cd, mid_nm, small_cd, small_nm, sale, count, int(sale / 21_000)))
    columns = ["pt_year", "pt_month", "base_year_month", "city_cd", "city_nm", "bjd_cd", "bjd_nm", "biz_mid_cd", "biz_mid_nm", "biz_small_cd", "biz_small_nm", "sale_amt", "store_cnt", "use_cnt"]
    return pd.DataFrame(rows, columns=columns)


def make_store_count(rng: np.random.Generator) -> pd.DataFrame:
    """업종별 매장 수: 시 × 업종 42개 × 매장주 구분 5개."""
    businesses = get_dataset("1-17").reference("업종코드").rows
    rows = []
    for city, scale in (("제주시", 1.0), ("서귀포시", 0.45)):
        for biz_cd, biz_nm in businesses:
            total = rng.lognormal(5.2, 0.9) * scale
            shares = np.array(owner_profile(biz_cd)) * rng.lognormal(0, 0.1, len(OWNER_TYPES))
            for month in MONTHS:
                years_on = (int(month[:4]) - 2025) + (int(month[4:]) - 5) / 12
                drift = np.array([1.0, 1 - 0.02 * years_on, 1 + 0.03 * years_on, 1.0, 1.0])   # 도민 개인 매장 비중이 조금씩 준다
                counts = rng.poisson(total * shares / shares.sum() * drift)
                for owner, count in zip(OWNER_TYPES, counts):
                    if count:
                        rows.append((month, month[:4], str(int(month[4:])), city, owner, biz_cd, biz_nm, int(count)))
    return pd.DataFrame(rows, columns=["base_year_month", "pt_year", "pt_month", "city", "owner_gb", "biz_cd", "biz_nm", "store_cnt"])


def make_arrivals(rng: np.random.Generator) -> pd.DataFrame:
    """실제 입도통계가 아직 없는 달의 가상 입도객: 2025년 같은 달 실제 값에 증가율과 난수를 곱한다."""
    real = pd.read_csv(ROOT / "src" / "golmok" / "reference" / "foreign_arrivals_public.csv", dtype={"month": str}, keep_default_na=False)
    growth = {"중국": 1.18, "대만": 1.15, "홍콩": 1.15, "내국인": 1.0}
    rows = []
    for month in [m for m in MONTHS if m > real["month"].max()]:
        last_year = real[real["month"] == f"2025{month[4:]}"]
        cruise_boost = 1.0 + 0.1 * (CRUISE_BASE[month][3] / 55000 - 1)
        for row in last_year.itertuples(index=False):
            factor = growth.get(row.country, 1.08) * rng.normal(1.0, 0.04) * (cruise_boost if row.country == "중국" else 1.0)
            if row.country == "내국인":
                factor *= 1 - TOURIST_DECLINE.get(month, 0.0)
            korea = "" if row.korea_arrivals == "" else int(int(row.korea_arrivals) * growth.get(row.country, 1.06) * rng.normal(1.0, 0.03))
            rows.append((month, row.country, row.segment, int(row.jeju_arrivals * factor), korea))
    return pd.DataFrame(rows, columns=["month", "country", "segment", "jeju_arrivals", "korea_arrivals"])


def save(frame: pd.DataFrame, dataset_id: str, file_name: str) -> None:
    """명세서의 컬럼 순서대로 UTF-8 CSV 로 저장한다 (센터 다운로드 파일과 같은 형식)."""
    columns = list(get_dataset(dataset_id).column_names)
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{dataset_id} 가상 데이터에 명세서 컬럼이 없습니다: {sorted(missing)}")
    path = OUT_DIR / file_name
    frame[columns].to_csv(path, index=False, encoding="utf-8")
    print(f"{dataset_id}  {len(frame):>9,}행  {path.stat().st_size / 1e6:6.1f}MB  {path.relative_to(ROOT)}")


def main() -> None:
    rng = np.random.default_rng(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_DIR.glob("*.csv"):
        stale.unlink()
    cruise = make_cruise(rng)
    save(cruise, "P-01", "P-01_cruise_monthly_MOCK.csv")
    districts = districts_table(rng)
    card = make_card_monthly(card_cells(rng), cruise, rng)
    save(card, "1-08", "1-08_card_emd_v02.csv")
    save(make_foreign_population(districts, cruise, rng), "4-15", "4-15_population_foreign_t_emd_v02.csv")
    save(make_population(districts, rng), "4-11", "4-11_population_korean_emd_v02.csv")
    save(make_waste_bags(districts, rng), "5-03", "5-03_waste_bag_sales.csv")
    # 아래는 나중에 더한 데이터. 위 데이터의 난수가 바뀌지 않도록 뒤에서 만든다
    save(make_card_time(card, rng), "1-14", "1-14_card_time_v02.csv")
    save(make_pos_food(rng), "1-19", "1-19_pos_food_emd.csv")
    save(make_store_count(rng), "1-17", "1-17_store_count.csv")
    save(make_arrivals(rng), "P-02", "P-02_foreign_arrivals_MOCK.csv")


if __name__ == "__main__":
    main()
