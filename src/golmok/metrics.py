"""관광 낙수 진단 지표.

"배는 오는데 돈은 안 온다" — 외국인·크루즈 관광객이 늘어도 그 소비가 어느 동네에 떨어지는지를 센터 데이터로 계산한다.

모든 지표는 응용집계(증감률·비율·지수)이며 반출 가드를 거친 GuardedTable 로만 나간다.
필요한 센터 데이터가 적재돼 있지 않으면 MissingData 를 던져, 무엇을 내려받아야 하는지 알려준다.

지역 키
- 카드(1-08)는 법정동. 이름이 시가 달라도 겹칠 수 있어(월평동) 항상 (권역, 법정동)으로 묶는다.
- 유동인구(4-11, 4-15)와 종량제봉투(5-03)는 행정동. 카드와 섞어 볼 때는 두 데이터가 공유하는 권역(7개) 단위로 계산한다.
- 카드 시간대(1-14)는 카드와 같은 (권역, 법정동). 외식업 POS(1-19)는 (시, 법정동)이라 카드의 (시, 법정동, 권역) 짝으로 권역을 붙인다.
- 업종별 매장 수(1-17)는 시 단위뿐이라 도민 가게 몫은 시·전체 단위로만 계산한다.
- 외국인 입도(P-02)는 제주 전체 값이라 지역마다 같은 분모로 쓴다.
- 상가 매장 수(P-05)는 행정동·법정동이 함께 있어, 센터 카드(법정동)와도 관광공사 소비(행정동)와도 (시, 동 이름)으로 붙는다.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from golmok.guard import GuardedTable, annotate, citation, release_derived
from golmok.schemas import Dataset, get_dataset, is_public

CARD_MONTHLY, POPULATION, FOREIGN_POPULATION, WASTE_BAGS, CRUISE = "1-08", "4-11", "4-15", "5-03", "P-01"
CARD_TIME, POS_FOOD, STORE_COUNT, ARRIVALS = "1-14", "1-19", "1-17", "P-02"
SPEND_PUBLIC, VISITORS_PUBLIC = "P-03", "P-04"   # 제주관광공사 공개 소비·방문객 (센터 카드·유동인구가 없을 때 쓴다)
STORES_PUBLIC = "P-05"                           # 소상공인 상가정보 매장 수 (읍면동 × 표준산업분류)
TIME_PUBLIC, NATION_PUBLIC, CRUISE_NATION = "P-06", "P-07", "P-08"   # 관광공사 시간대별 소비, 국가별 외국인 소비, 국적별 크루즈 입도객
PUBLIC_TIME_VIEW, PUBLIC_NATION_VIEW = "card_time_from_public", "card_nation_from_public"
TOURIST_BIZ = ("G47", "I55", "I56", "R91")       # 관광객이 주로 쓰는 업종: 소매, 숙박, 음식점·카페·주점, 여가
PUBLIC_CARD_VIEW = "card_from_public"
# 1-14 시간대: 오전(07~11시), 점심, 오후, 저녁(18~22시), 밤(22시~07시). "18~22시"처럼 시각 범위로 와도 시작 시각으로 가른다
_START_HOUR = r"TRY_CAST(regexp_extract(time, '^\s*(\d{1,2})', 1) AS INTEGER)"
NIGHT_CONDITION = f"(time IN ('저녁', '밤') OR {_START_HOUR} >= 18 OR {_START_HOUR} < 7)"
LOCAL_OWNER = "제주도민 개인"           # 1-17 매장주 구분

# 카드 데이터 집계 단위 → {결과 컬럼: SQL 식}
LEVELS = {
    "bjd": {"dstrct_se": "dstrct_se", "bjd": "bjd"},
    "dstrct_se": {"dstrct_se": "dstrct_se"},
    "city": {"city": "split_part(dstrct_se, ' ', 1)"},
    "all": {},
}
# 행정동 데이터(유동인구·종량제봉투) 집계 단위
EMD_LEVELS = {
    "emd": {"dstrct_se": "dstrct_se", "emd_nm": "emd_nm"},
    "dstrct_se": {"dstrct_se": "dstrct_se"},
    "city": {"city": "split_part(emd_nm, ' ', 1)"},
    "all": {},
}
FOREIGN_TYPES = ("중국", "일본", "동남아", "기타외국")
FOREIGN = "('중국', '일본', '동남아', '기타외국', '외국인')"   # '외국인'은 국적을 나누지 않는 공개 소비(P-03)의 외국인
SEGMENTS = {
    "전체": "TRUE",
    "도민": "user_type = '제주도민'",
    "내국인관광객": "user_type = '내국인'",
    "외국인": f"user_type IN {FOREIGN}",
    "관광객": f"(user_type = '내국인' OR user_type IN {FOREIGN})",
    "중국": "user_type = '중국'",
    "일본": "user_type = '일본'",
    "동남아": "user_type = '동남아'",
    "기타외국": "user_type = '기타외국'",
}
# 카드 이용자구분 ↔ 단기체류 외국인 유동인구 국적코드. 기타외국은 아래에 없는 모든 국적.
SOUTHEAST_ASIA = ("VNM", "THA", "MYS", "SGP", "PHL", "IDN", "MMR", "KHM", "LAO", "BRN")
SEGMENT_NATIONALITIES: dict[str, tuple[str, ...] | None] = {"외국인": None, "중국": ("CHN",), "일본": ("JPN",), "동남아": SOUTHEAST_ASIA}
# 쓰레기 부담 지표에 넣는 봉투 종류 (재사용·공공용은 생활 배출이 아니라 뺀다)
WASTE_TYPES = "('가연성', '불연성', '음식물')"
CRUISE_MIN_MONTHS = 4
# 근거 이용자수에 따른 신뢰 수준 (우리 기준: 100명 이상 높음, 30명 이상 보통)
RELIABILITY_BINS = [(100, "높음"), (30, "보통"), (0, "낮음")]


class MissingData(LookupError):
    """지표 계산에 필요한 데이터가 없다."""

    def __init__(self, dataset: Dataset, months: list[str]):
        self.dataset, self.months = dataset, months
        when = "의 " + ", ".join(f"{m[:4]}년 {int(m[4:])}월" for m in months) if months else ""
        where = "같은 컬럼의 CSV 로 만들어" if is_public(dataset.id) else "센터에서 CSV 로 내려받아"
        super().__init__(f"'{dataset.title}'({dataset.id}){when} 데이터를 {where} 넣어 주세요.")


@dataclass(frozen=True)
class MetricResult:
    title: str
    definition: str  # 계산식 한 줄
    table: GuardedTable
    sources: tuple[str, ...]  # 반출 정책의 출처 표기


def previous_year(month: str) -> str:
    return f"{int(month[:4]) - 1}{month[4:]}"


def month_expr(dataset_id: str) -> str:
    columns = get_dataset(dataset_id).column_names
    if "base_year_month" in columns:
        return "base_year_month"
    if "snapshot" in columns:   # 상가 매장 수처럼 한 시점 목록
        return "snapshot"
    return "month" if "month" in columns else "substr(base_date, 1, 6)"


def loaded_months(con: duckdb.DuckDBPyConnection, dataset_id: str) -> list[str]:
    dataset = get_dataset(dataset_id)
    exists = con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [dataset.table]).fetchone()
    if not exists:
        return []
    return sorted(row[0] for row in con.execute(f'SELECT DISTINCT {month_expr(dataset_id)} FROM "{dataset.table}"').fetchall())


# ---------- 카드 데이터 출처: 센터(1-08) 우선, 없으면 관광공사 공개 소비(P-03) ----------
# 관광공사 지역명은 '제주시 동지역' 하나라, 센터 권역(중부 동·서)에 맞춰 동 이름으로 나눈다 (우리 기준)
_JEJU_WEST_CITY = ("연동", "노형동", "외도동", "이호동", "도두동", "용담1동", "용담2동", "오라동", "삼도1동", "삼도2동")
_SEOGWIPO_CITY = ("송산동", "정방동", "중앙동", "천지동", "효돈동", "영천동", "동홍동", "서홍동", "대륜동", "대천동", "중문동", "예래동")
_PUBLIC_REGION = f"""CASE
    WHEN emd_nm IN ('구좌읍', '조천읍', '우도면') THEN '제주시 동부'
    WHEN emd_nm IN ('한림읍', '애월읍', '한경면', '추자면') THEN '제주시 서부'
    WHEN emd_nm IN ('남원읍', '성산읍', '표선면') THEN '서귀포시 동부'
    WHEN emd_nm IN ('대정읍', '안덕면') THEN '서귀포시 서부'
    WHEN emd_nm IN ({", ".join(f"'{name}'" for name in _SEOGWIPO_CITY)}) THEN '서귀포시 중부'
    WHEN emd_nm IN ({", ".join(f"'{name}'" for name in _JEJU_WEST_CITY)}) THEN '제주시 중부(서)'
    ELSE '제주시 중부(동)' END"""
_PUBLIC_BIZ = """CASE industry WHEN '음식점업' THEN 'I56000' WHEN '숙박업' THEN 'I55000' WHEN '소매업' THEN 'G47000'
    WHEN '예술스포츠여가업' THEN 'R91000' ELSE 'S96000' END"""
_PUBLIC_USER = """CASE tourist_type WHEN '도민' THEN '제주도민' WHEN '내국인 관광객' THEN '내국인' WHEN '외국인 관광객' THEN '외국인' ELSE tourist_type END"""
# 국가별 외국인 소비(P-07)의 국가 → 카드 이용자구분 (동남아는 4-15 국적 묶음과 같은 나라들)
_SOUTHEAST_NAMES = ("베트남", "태국", "말레이시아", "싱가포르", "필리핀", "인도네시아", "미얀마", "캄보디아", "라오스", "브루나이")
_NATION_USER = f"""CASE WHEN country IN ('중국') THEN '중국' WHEN country IN ('일본') THEN '일본'
    WHEN country IN ({", ".join(f"'{n}'" for n in _SOUTHEAST_NAMES)}) THEN '동남아' ELSE '기타외국' END"""
_NATION_USED: ContextVar[bool] = ContextVar("nation_used", default=False)   # 방금 계산이 국가별 외국인 소비를 썼는가 (출처 표기용)


def card_source(con: duckdb.DuckDBPyConnection, months: list[str]) -> str:
    """이 달들의 카드 지표를 계산할 데이터: 센터 1-08 이 모두 있으면 1-08, 아니면 공개 소비 P-03 이 모두 있으면 P-03."""
    center = set(loaded_months(con, CARD_MONTHLY))
    if all(m in center for m in months):
        return CARD_MONTHLY
    public = set(loaded_months(con, SPEND_PUBLIC))
    return SPEND_PUBLIC if public and all(m in public for m in months) else CARD_MONTHLY


def time_source(con: duckdb.DuckDBPyConnection, months: list[str]) -> str:
    """시간대 카드 지표의 데이터: 센터 1-14 가 모두 있으면 1-14, 아니면 공개 시간대 소비 P-06 이 모두 있으면 P-06."""
    center = set(loaded_months(con, CARD_TIME))
    if all(m in center for m in months):
        return CARD_TIME
    public = set(loaded_months(con, TIME_PUBLIC))
    return TIME_PUBLIC if public and all(m in public for m in months) else CARD_TIME


def card_months(con: duckdb.DuckDBPyConnection) -> list[str]:
    return loaded_months(con, CARD_MONTHLY) or loaded_months(con, SPEND_PUBLIC)


def _public_card_view(con: duckdb.DuckDBPyConnection) -> str:
    """공개 소비(P-03)를 센터 카드(1-08)와 같은 컬럼으로 보여주는 뷰. 이용자수 자리에는 이용건수를 쓴다."""
    con.execute(f"""CREATE OR REPLACE VIEW {PUBLIC_CARD_VIEW} AS
        SELECT month AS base_year_month, {_PUBLIC_REGION} AS dstrct_se, split_part(region, ' ', 1) AS city, emd_nm AS bjd,
               {_PUBLIC_BIZ} AS biz_cd, industry AS biz_nm, {_PUBLIC_USER} AS user_type,
               use_cnt AS user_cnt, use_cnt, use_amt
        FROM "{get_dataset(SPEND_PUBLIC).table}" """)
    return PUBLIC_CARD_VIEW


def _public_time_view(con: duckdb.DuckDBPyConnection) -> str:
    """공개 시간대별 소비(P-06)를 센터 카드 시간대(1-14)와 같은 컬럼으로 보여주는 뷰. 시간대 자리에는 시작 시각을 쓴다."""
    con.execute(f"""CREATE OR REPLACE VIEW {PUBLIC_TIME_VIEW} AS
        SELECT month AS base_year_month, {_PUBLIC_REGION} AS dstrct_se, split_part({_PUBLIC_REGION}, ' ', 1) AS city, emd_nm AS bjd,
               {_PUBLIC_BIZ} AS biz_cd, industry AS biz_nm, {_PUBLIC_USER} AS user_type, CAST(hour AS VARCHAR) AS time,
               use_cnt AS user_cnt, use_cnt, use_amt
        FROM "{get_dataset(TIME_PUBLIC).table}" """)
    return PUBLIC_TIME_VIEW


def _public_nation_view(con: duckdb.DuckDBPyConnection) -> str:
    """국가별 외국인 소비(P-07)를 카드 컬럼으로 보여주는 뷰. 지역 구분이 없어 제주 전체 단위에서만 쓴다."""
    con.execute(f"""CREATE OR REPLACE VIEW {PUBLIC_NATION_VIEW} AS
        SELECT month AS base_year_month, {_PUBLIC_BIZ} AS biz_cd, industry AS biz_nm, {_NATION_USER} AS user_type,
               use_cnt AS user_cnt, use_cnt, use_amt
        FROM "{get_dataset(NATION_PUBLIC).table}" """)
    return PUBLIC_NATION_VIEW


def _require(con: duckdb.DuckDBPyConnection, dataset_id: str, months: list[str]) -> str:
    """데이터셋이 적재돼 있고 필요한 달이 모두 있는지 확인한 뒤 테이블명을 돌려준다."""
    if dataset_id == CARD_MONTHLY and card_source(con, months) == SPEND_PUBLIC:
        return _public_card_view(con)
    if dataset_id == CARD_TIME and time_source(con, months) == TIME_PUBLIC:
        return _public_time_view(con)
    loaded = set(loaded_months(con, dataset_id))
    missing = [m for m in months if m not in loaded]
    if missing:
        raise MissingData(get_dataset(dataset_id), missing)
    return get_dataset(dataset_id).table


def _card_totals(
    con: duckdb.DuckDBPyConnection,
    months: list[str],
    level: str,
    biz_prefix: str | None,
    condition: str = "TRUE",
    extra: dict[str, str] | None = None,
    dataset_id: str = CARD_MONTHLY,
) -> pd.DataFrame:
    """카드 데이터(기본 1-08, 시간대별 1-14 도 같은 컬럼)를 (집계 단위, 달, extra) 별로 합산한다. extra 는 {컬럼명: SQL 식}."""
    table = _require(con, dataset_id, months)
    _NATION_USED.set(False)
    if table in (PUBLIC_CARD_VIEW, PUBLIC_TIME_VIEW):
        by_nation = any(f"'{name}'" in condition for name in FOREIGN_TYPES) and "IN" not in condition
        split_by_nation = any(f"'{name}'" in expr and "IN" not in expr for expr in (extra or {}).values() for name in FOREIGN_TYPES)
        nation_ready = table == PUBLIC_CARD_VIEW and all(m in set(loaded_months(con, NATION_PUBLIC)) for m in months)
        if split_by_nation:
            raise ValueError("관광공사 공개 소비는 국적별 비중을 계산할 수 없습니다(국가별 자료에는 도민·내국인이 없음). 외국인 전체로 보세요")
        if by_nation:
            if not (nation_ready and level == "all"):
                raise ValueError("관광공사 공개 소비는 국적을 동네별로 나누지 않습니다. 국적별로는 지역 단위를 '제주 전체'로 보거나, 외국인 전체로 보세요"
                                 if nation_ready else "관광공사 공개 소비 데이터는 외국인을 국적별로 나누지 않습니다. 외국인 전체로 보세요")
            table = _public_nation_view(con)
            _NATION_USED.set(True)
    select = {**LEVELS[level], "month": "base_year_month", **(extra or {})}
    columns = ", ".join(f"{expr} AS {name}" for name, expr in select.items())
    marks = ", ".join("?" for _ in months)
    totals = con.execute(
        f"""SELECT {columns}, sum(use_amt) AS amt, sum(user_cnt) AS users, sum(use_cnt) AS cnt
            FROM "{table}"
            WHERE base_year_month IN ({marks}) AND biz_cd LIKE ? AND {condition}
            GROUP BY ALL""",
        [*months, (biz_prefix or "") + "%"],
    ).df()
    if totals.empty and biz_prefix and not con.execute(f'SELECT 1 FROM "{table}" WHERE base_year_month IN ({marks}) AND biz_cd LIKE ? LIMIT 1',
                                                      [*months, biz_prefix + "%"]).fetchone():
        # 업종 자체가 이 달 데이터에 없으면 뒤에서 빈 표로 터지지 않게 이유를 바로 말한다 (이용자 구분 때문에 비는 경우는 그대로 둔다)
        name = next((row[1] for row in get_dataset(CARD_MONTHLY).reference("업종코드").rows if row[0] == biz_prefix), biz_prefix)
        if table in (PUBLIC_CARD_VIEW, PUBLIC_TIME_VIEW):
            raise ValueError(f"관광공사 공개 소비 데이터에는 업종 {biz_prefix}({name})이 없습니다. 공개 소비의 업종은 소매(G47)·숙박(I55)·음식점(I56)·여가(R91)·기타서비스(S96) 다섯뿐이라, "
                             "면세점처럼 자세한 업종은 센터 '카드 이용 현황(읍면동)_v02'(1-08)를 넣어야 볼 수 있습니다")
        raise ValueError(f"업종 {biz_prefix}({name})의 결제가 이 달 데이터에 없습니다. 업종 앞자리(G47, I56 등)로 넓혀 보세요")
    return totals


def _pair(totals: pd.DataFrame, dims: list[str], month: str) -> pd.DataFrame:
    """올해와 작년 같은 달을 한 행으로 붙인다 (_now, _before)."""
    now = totals[totals["month"] == month].drop(columns="month")
    before = totals[totals["month"] == previous_year(month)].drop(columns="month")
    if not dims:
        return now.merge(before, how="cross", suffixes=("_now", "_before"))
    return now.merge(before, on=dims, suffixes=("_now", "_before"))


def _reliability(frame: pd.DataFrame, support: list[str]) -> pd.Series:
    weakest = frame[support].min(axis=1)
    return weakest.map(lambda n: next(label for floor, label in RELIABILITY_BINS if n >= floor))


def _release(frame: pd.DataFrame, values: list[str], support: list[str], sort_by: str) -> GuardedTable:
    frame = frame.assign(신뢰수준=_reliability(frame, support)).sort_values(sort_by, ignore_index=True)
    return release_derived(frame, values, support)


def _sources(*pairs: tuple[str, list[str]]) -> tuple[str, ...]:
    out = []
    for dataset_id, months in pairs:
        if dataset_id == SPEND_PUBLIC and _NATION_USED.get():   # 국적별 값은 국가별 외국인 소비에서 왔다
            dataset_id = NATION_PUBLIC
        dataset = get_dataset(dataset_id)
        period = "·".join(f"{m[:4]}.{m[4:]}" for m in sorted(months)) if len(months) <= 3 else f"{min(months)[:4]}.{min(months)[4:]}~{max(months)[:4]}.{max(months)[4:]}"
        out.append(citation(dataset.title, period, dataset.provider if is_public(dataset_id) else "제주빅데이터센터"))
    return tuple(out)


def _pct(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return ((numerator / denominator - 1) * 100).round(1)


def _split_share(totals: pd.DataFrame, dims: list[str], month: str) -> tuple[pd.DataFrame, list[str]]:
    """side 컬럼('part'/'rest')으로 나뉜 합계에서 part 의 금액 비중(share_pct)과 전년 같은 달 대비 변화(change_pp)를 만든다."""
    wide = totals.pivot_table(index=[*dims, "month"], columns="side", values=["amt", "users"], aggfunc="sum", fill_value=0)
    wide.columns = [f"{value}_{side}" for value, side in wide.columns]
    # 해당 구분이나 나머지 결제가 한 건도 없는 경우에도 컬럼이 있어야 한다 (이 칸은 근거 부족으로 가려진다)
    wide = wide.reindex(columns=["amt_part", "amt_rest", "users_part", "users_rest"], fill_value=0).reset_index()
    wide["share"] = wide["amt_part"] / (wide["amt_part"] + wide["amt_rest"]) * 100
    pair = _pair(wide, dims, month)
    pair["share_pct"] = pair["share_now"].round(1)
    pair["change_pp"] = (pair["share_now"] - pair["share_before"]).round(1)
    return pair, [f"users_{side}_{when}" for side in ("part", "rest") for when in ("now", "before")]


def _aggregate_to(frame: pd.DataFrame, level: str, measures: list[str]) -> pd.DataFrame:
    """(권역, 법정동, 달) 단위 표를 카드 집계 단위(LEVELS)로 다시 묶는다. city 는 권역 이름의 앞 단어."""
    frame = frame.assign(city=frame["dstrct_se"].str.split(" ").str[0])
    keys = [*LEVELS[level], "month"]
    return frame.groupby(keys, as_index=False)[measures].sum()


# ---------- 카드만 쓰는 지표 ----------
def sales_yoy(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "전체") -> MetricResult:
    """매출 증감률: 전년 같은 달 대비 카드 이용금액 변화."""
    dims, months = list(LEVELS[level]), [month, previous_year(month)]
    pair = _pair(_card_totals(con, months, level, biz_prefix, SEGMENTS[segment]), dims, month)
    pair["yoy_pct"] = _pct(pair["amt_now"], pair["amt_before"])
    table = _release(pair[[*dims, "yoy_pct", "users_now", "users_before"]], ["yoy_pct"], ["users_now", "users_before"], "yoy_pct")
    return MetricResult(
        f"매출 증감률({segment})",
        "(이번 달 이용금액 ÷ 전년 같은 달 이용금액 − 1) × 100",
        table,
        _sources((card_source(con, months), months)),
    )


def ticket_size_yoy(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "전체") -> MetricResult:
    """객단가 증감률: 건당 결제액의 전년 같은 달 대비 변화."""
    dims, months = list(LEVELS[level]), [month, previous_year(month)]
    pair = _pair(_card_totals(con, months, level, biz_prefix, SEGMENTS[segment]), dims, month)
    pair["ticket_yoy_pct"] = _pct(pair["amt_now"] / pair["cnt_now"], pair["amt_before"] / pair["cnt_before"])
    support = ["users_now", "users_before", "cnt_now", "cnt_before"]
    table = _release(pair[[*dims, "ticket_yoy_pct", *support]], ["ticket_yoy_pct"], support, "ticket_yoy_pct")
    return MetricResult(
        f"객단가 증감률({segment})",
        "건당 결제액(이용금액 ÷ 이용건수)의 전년 같은 달 대비 변화율",
        table,
        _sources((card_source(con, months), months)),
    )


def spend_share(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "외국인") -> MetricResult:
    """소비 비중: 개인 카드 매출 중 해당 이용자 구분(기본 외국인)의 비중과 1년 전 대비 변화. 법인카드는 제외."""
    if segment in ("전체",):
        raise ValueError("소비 비중은 도민·내국인관광객·외국인·관광객·중국·일본·동남아·기타외국 중 하나로 봅니다")
    dims, months = list(LEVELS[level]), [month, previous_year(month)]
    side = {"side": f"CASE WHEN {SEGMENTS[segment]} THEN 'part' ELSE 'rest' END"}
    totals = _card_totals(con, months, level, biz_prefix, "user_type <> '법인'", side)
    pair, support = _split_share(totals, dims, month)
    values = ["share_pct", "change_pp"]
    table = _release(pair[[*dims, *values, *support]], values, support, "share_pct")
    return MetricResult(
        f"소비 비중({segment})",
        f"{segment} 이용금액 ÷ 개인 전체 이용금액 × 100. 변화는 전년 같은 달과의 차이(%p). 법인카드 제외",
        table,
        _sources((card_source(con, months), months)),
    )


def gap_fill(con, month: str, level: str = "bjd", biz_prefix: str | None = None) -> MetricResult:
    """내국인 공백 메우기: 내국인 관광객 소비가 줄어든 자리를 외국인 소비가 얼마나 메웠는가.

    메움률 = 외국인 이용금액 증가분 ÷ 내국인 관광객 이용금액 감소분 × 100. 내국인이 줄지 않은 곳은 비워 둔다.
    관광객 전체 증감률(내국인 + 외국인)도 함께 낸다.
    """
    dims, months = list(LEVELS[level]), [month, previous_year(month)]
    side = {"side": "CASE WHEN user_type = '내국인' THEN 'dom' ELSE 'frn' END"}
    totals = _card_totals(con, months, level, biz_prefix, SEGMENTS["관광객"], side)
    wide = totals.pivot_table(index=[*dims, "month"], columns="side", values=["amt", "users"], aggfunc="sum", fill_value=0)
    wide.columns = [f"{value}_{side}" for value, side in wide.columns]
    wide = wide.reindex(columns=["amt_dom", "amt_frn", "users_dom", "users_frn"], fill_value=0).reset_index()
    pair = _pair(wide, dims, month)
    domestic_drop = pair["amt_dom_before"] - pair["amt_dom_now"]
    foreign_gain = pair["amt_frn_now"] - pair["amt_frn_before"]
    pair["tourist_yoy_pct"] = _pct(pair["amt_dom_now"] + pair["amt_frn_now"], pair["amt_dom_before"] + pair["amt_frn_before"])
    pair["domestic_yoy_pct"] = _pct(pair["amt_dom_now"], pair["amt_dom_before"])
    pair["foreign_yoy_pct"] = _pct(pair["amt_frn_now"], pair["amt_frn_before"])
    pair["fill_pct"] = (foreign_gain / domestic_drop * 100).where(domestic_drop > 0).round(1)
    support = [f"users_{side}_{when}" for side in ("dom", "frn") for when in ("now", "before")]
    values = ["tourist_yoy_pct", "domestic_yoy_pct", "foreign_yoy_pct", "fill_pct"]
    table = _release(pair[[*dims, *values, *support]], values, support, "tourist_yoy_pct")
    return MetricResult(
        "내국인 공백 메우기",
        "메움률 = 외국인 이용금액 증가분 ÷ 내국인 관광객 이용금액 감소분 × 100 (내국인이 줄어든 곳만). 관광객 전체·내국인·외국인 증감률은 전년 같은 달 대비",
        table,
        _sources((card_source(con, months), months)),
    )


# ---------- 유동인구를 함께 쓰는 지표 ----------
def _visitor_condition(segment: str) -> tuple[str, str, list]:
    """세그먼트에 맞는 유동인구 데이터셋과 조건. (데이터셋, 조건 SQL, 바인딩 값)"""
    if segment == "내국인관광객":
        return POPULATION, "resd = '그외'", []
    if segment == "기타외국":
        known = SOUTHEAST_ASIA + ("CHN", "JPN")
        return FOREIGN_POPULATION, f"nationality NOT IN ({', '.join('?' for _ in known)})", list(known)
    if segment not in SEGMENT_NATIONALITIES:
        raise ValueError("낙수 지수는 외국인·중국·일본·동남아·기타외국·내국인관광객 중 하나로 봅니다")
    codes = SEGMENT_NATIONALITIES[segment]
    if codes is None:
        return FOREIGN_POPULATION, "TRUE", []
    return FOREIGN_POPULATION, f"nationality IN ({', '.join('?' for _ in codes)})", list(codes)


def _visitors(con, dataset_id: str, months: list[str], level: str, condition: str, params: list) -> pd.DataFrame:
    """유동인구를 (집계 단위, 달)별 평균 '하루 방문 체류 인원'으로 만든다: 시각별 방문인구 합 ÷ 24 ÷ 들어 있는 날 수."""
    table = _require(con, dataset_id, months)
    select = {**EMD_LEVELS[level], "month": "substr(base_date, 1, 6)"}
    columns = ", ".join(f"{expr} AS {name}" for name, expr in select.items())
    marks = ", ".join("?" for _ in months)
    return con.execute(
        f"""SELECT {columns},
                   sum(TRY_CAST(visit_pop_cnt AS DOUBLE)) / 24 / count(DISTINCT base_date) AS visitors
            FROM "{table}" WHERE substr(base_date, 1, 6) IN ({marks}) AND {condition} GROUP BY ALL""",
        [*months, *params],
    ).df()


def _public_conversion(con, month: str, biz_prefix: str | None, segment: str) -> MetricResult:
    """관광공사 공개 소비(P-03)와 방문객(P-04)으로 읍면동 단위 낙수 지수를 계산한다. 두 데이터가 같은 행정동 이름을 쓴다."""
    spend = _card_totals(con, [month], "bjd", biz_prefix, SEGMENTS[segment]).drop(columns="month")
    column = "foreign_visitors" if segment == "외국인" else "domestic_visitors"
    visitors = con.execute(
        f'SELECT emd_nm AS bjd, sum({column}) AS visitors FROM "{get_dataset(VISITORS_PUBLIC).table}" WHERE month = ? GROUP BY ALL', [month]
    ).df()
    merged = spend.merge(visitors, on="bjd")
    merged = merged[merged["visitors"] > 0]
    spend_share_ = merged["amt"] / merged["amt"].sum()
    visitor_share = merged["visitors"] / merged["visitors"].sum()
    merged["conversion_index"] = (spend_share_ / visitor_share).round(2)
    merged["spend_share_pct"] = (spend_share_ * 100).round(1)
    merged["visitor_share_pct"] = (visitor_share * 100).round(1)
    values = ["conversion_index", "spend_share_pct", "visitor_share_pct"]
    table = _release(merged[["dstrct_se", "bjd", *values, "users", "visitors"]], values, ["users", "visitors"], "conversion_index")
    table = annotate(table, "관광공사 공개 데이터로 읍면동 단위까지 계산했습니다. 소비는 BC카드 결제, 방문객은 SKT 기준이며 표본 판단에는 이용건수를 씁니다.")
    return MetricResult(
        f"낙수 지수({segment}, 읍면동)",
        f"(읍면동의 {segment} 소비금액 비중) ÷ (읍면동의 {segment} 방문객 비중). 1 = 제주 평균",
        table,
        _sources((SPEND_PUBLIC, [month]), (VISITORS_PUBLIC, [month])),
    )


def conversion_index(con, month: str, biz_prefix: str | None = None, segment: str = "외국인") -> MetricResult:
    """낙수 지수: 권역별 해당 관광객의 카드 소비 비중 ÷ 방문 유동인구 비중.

    1 보다 크면 찾아온 사람에 비해 돈을 많이 쓰는 권역, 작으면 사람은 오는데 소비로 이어지지 않는 권역.
    카드는 법정동, 유동인구는 행정동 단위라 두 데이터가 공유하는 권역(7개) 단위로 계산한다.
    """
    if card_source(con, [month]) == SPEND_PUBLIC and segment in ("외국인", "내국인관광객") and month in loaded_months(con, VISITORS_PUBLIC):
        return _public_conversion(con, month, biz_prefix, segment)
    population_id, condition, params = _visitor_condition(segment)
    spend = _card_totals(con, [month], "dstrct_se", biz_prefix, SEGMENTS[segment]).drop(columns="month")
    visitors = _visitors(con, population_id, [month], "dstrct_se", condition, params).drop(columns="month")
    merged = spend.merge(visitors, on="dstrct_se")
    spend_share_ = merged["amt"] / merged["amt"].sum()
    visitor_share = merged["visitors"] / merged["visitors"].sum()
    merged["conversion_index"] = (spend_share_ / visitor_share).round(2)
    merged["spend_share_pct"] = (spend_share_ * 100).round(1)
    merged["visitor_share_pct"] = (visitor_share * 100).round(1)
    values = ["conversion_index", "spend_share_pct", "visitor_share_pct"]
    table = _release(merged[["dstrct_se", *values, "users", "visitors"]], values, ["users", "visitors"], "conversion_index")
    return MetricResult(
        f"낙수 지수({segment})",
        f"(권역의 {segment} 이용금액 비중) ÷ (권역의 {segment} 방문 유동인구 비중). 1 = 제주 평균",
        table,
        _sources((card_source(con, [month]), [month]), (population_id, [month])),
    )


def waste_burden(con, month: str, level: str = "emd", biz_prefix: str | None = None) -> MetricResult:
    """쓰레기 부담: 방문객 1천 명(하루 체류 기준)당 종량제봉투 판매량(L)과, 봉투 판매량·외국인 방문의 전년 대비 변화.

    방문객 = 단기체류 외국인 방문 유동인구 + 도외 거주 내국인 방문 유동인구. 봉투는 가연성·불연성·음식물만 센다.
    전년 같은 달 데이터가 없으면 증감률은 비워 둔다. biz_prefix 는 쓰지 않는다(다른 지표와 호출 형식을 맞추기 위한 자리).
    """
    level = "emd" if level == "bjd" else level
    dims = list(EMD_LEVELS[level])
    months = [month, previous_year(month)]
    have_before = all(previous_year(month) in loaded_months(con, d) for d in (WASTE_BAGS, FOREIGN_POPULATION, POPULATION))
    use_months = months if have_before else [month]

    waste_table = _require(con, WASTE_BAGS, [month])
    marks = ", ".join("?" for _ in use_months)
    # 종량제봉투는 (시, 행정동 짧은 이름) 키라서 유동인구의 "제주시 한림읍" 형식으로 맞추고, 권역은 유동인구 데이터에서 가져온다
    bags = con.execute(
        f"""SELECT city_name || ' ' || emd_name AS emd_nm, substr(base_date, 1, 6) AS month,
                   sum(sale_count * TRY_CAST(replace(CAST(wastebag_liter AS VARCHAR), 'L', '') AS DOUBLE)) / count(DISTINCT base_date) AS liters_per_day
            FROM "{waste_table}"
            WHERE substr(base_date, 1, 6) IN ({marks}) AND wastebag_type IN {WASTE_TYPES} GROUP BY ALL""",
        use_months,
    ).df()
    regions = con.execute(f'SELECT DISTINCT emd_nm, dstrct_se FROM "{get_dataset(FOREIGN_POPULATION).table}"').df()
    bags = bags.merge(regions, on="emd_nm", how="inner")
    bags["city"] = bags["emd_nm"].str.split(" ").str[0]
    bags = bags.groupby([*dims, "month"], as_index=False)["liters_per_day"].sum() if dims else bags.groupby("month", as_index=False)["liters_per_day"].sum()

    foreign = _visitors(con, FOREIGN_POPULATION, use_months, level, "TRUE", []).rename(columns={"visitors": "foreign_visitors"})
    domestic = _visitors(con, POPULATION, use_months, level, "resd = '그외'", []).rename(columns={"visitors": "domestic_visitors"})
    keys = [*dims, "month"]
    merged = foreign.merge(domestic, on=keys).merge(bags, on=keys)
    merged["visitors"] = merged["foreign_visitors"] + merged["domestic_visitors"]
    merged["liters_per_1000"] = (merged["liters_per_day"] / merged["visitors"] * 1000).round(1)

    now = merged[merged["month"] == month].drop(columns="month")
    if have_before:
        pair = _pair(merged[[*dims, "month", "liters_per_day", "foreign_visitors", "visitors"]], dims, month)
        now = now.merge(pair[[*dims, "liters_per_day_before", "foreign_visitors_before"]], on=dims, how="left") if dims else now.assign(liters_per_day_before=pair["liters_per_day_before"].iloc[0], foreign_visitors_before=pair["foreign_visitors_before"].iloc[0])
        now["bag_yoy_pct"] = _pct(now["liters_per_day"], now["liters_per_day_before"])
        now["foreign_visit_yoy_pct"] = _pct(now["foreign_visitors"], now["foreign_visitors_before"])
    else:
        now["bag_yoy_pct"] = np.nan
        now["foreign_visit_yoy_pct"] = np.nan
    values = ["liters_per_1000", "bag_yoy_pct", "foreign_visit_yoy_pct"]
    table = _release(now[[*dims, *values, "visitors"]], values, ["visitors"], "liters_per_1000")
    if not have_before:
        table = annotate(table, "전년 같은 달 데이터가 없어 증감률은 비워 두었습니다.")
    return MetricResult(
        "쓰레기 부담",
        "하루 종량제봉투 판매량(L, 가연성·불연성·음식물) ÷ 하루 방문 체류 인원(외국인 + 도외 내국인) × 1000. 증감률은 전년 같은 달 대비",
        table,
        _sources((WASTE_BAGS, use_months), (FOREIGN_POPULATION, use_months), (POPULATION, use_months)),
    )


# ---------- 크루즈 입항과 함께 보는 지표 ----------
def cruise_lift(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "중국") -> MetricResult:
    """크루즈 기항 효과: 중국발 크루즈 관광객이 많았던 달과 적었던 달의 해당 관광객 카드 소비 차이.

    기준 월까지 카드와 크루즈 데이터가 함께 있는 달을 모아, 중국발 크루즈 관광객 수의 중앙값을 기준으로 '많은 달'과 '적은 달'로 나눈다.
    효과 = (많은 달 평균 이용금액 ÷ 적은 달 평균 이용금액 − 1) × 100. 함께 보는 상관계수는 달별 크루즈 관광객 수와 이용금액의 피어슨 상관.
    같은 달이라도 계절·행사 영향이 섞이므로 인과가 아니라 '함께 움직이는 정도'다.
    """
    card_months_ = [m for m in card_months(con) if m <= month]
    cruise_id = CRUISE_NATION if loaded_months(con, CRUISE_NATION) else CRUISE   # 국적별 크루즈 입도객이 있으면 기간이 더 길다
    cruise_months = set(loaded_months(con, cruise_id))
    months = [m for m in card_months_ if m in cruise_months]
    if len(months) < CRUISE_MIN_MONTHS:
        needed = [m for m in card_months_ if m not in cruise_months] or [month]
        raise MissingData(get_dataset(cruise_id), needed[-CRUISE_MIN_MONTHS:])
    dims = list(LEVELS[level])
    nation_ready = all(m in set(loaded_months(con, NATION_PUBLIC)) for m in months)
    widened = card_source(con, months) == SPEND_PUBLIC and segment in FOREIGN_TYPES and not (level == "all" and nation_ready)
    if widened:   # 관광공사 공개 소비는 국적을 나누지 않아, 중국 대신 외국인 전체로 본다
        segment = "외국인"
    totals = _card_totals(con, months, level, biz_prefix, SEGMENTS[segment])
    marks = ", ".join("?" for _ in months)
    cruise_sql = (f'''SELECT month, sum(CASE WHEN country = '중국' THEN cruise_arrivals ELSE 0 END) AS china_passengers
                     FROM "{get_dataset(CRUISE_NATION).table}" WHERE month IN ({marks}) GROUP BY month''' if cruise_id == CRUISE_NATION
                  else f'SELECT month, china_passengers FROM "{get_dataset(CRUISE).table}" WHERE month IN ({marks})')
    cruise = con.execute(cruise_sql, months).df()
    cruise["high"] = cruise["china_passengers"] > cruise["china_passengers"].median()
    merged = totals.merge(cruise, on="month")

    def summarize(group: pd.DataFrame) -> dict:
        high, low = group[group["high"]], group[~group["high"]]
        lift = (high["amt"].mean() / low["amt"].mean() - 1) * 100 if len(high) and len(low) and low["amt"].mean() > 0 else np.nan
        corr = group["amt"].corr(group["china_passengers"]) if len(group) >= 3 else np.nan
        return {"lift_pct": round(lift, 1) if pd.notna(lift) else np.nan, "corr": round(corr, 2) if pd.notna(corr) else np.nan,
                "months_high": int(len(high)), "months_low": int(len(low)), "users": int(group["users"].min())}

    if dims:
        summary = pd.DataFrame([{**dict(zip(dims, key if isinstance(key, tuple) else (key,))), **summarize(group)} for key, group in merged.groupby(dims)])
    else:
        summary = pd.DataFrame([summarize(merged)])
    values = ["lift_pct", "corr"]
    table = _release(summary[[*dims, *values, "months_high", "months_low", "users"]], values, ["users"], "lift_pct")
    if widened:
        table = annotate(table, "관광공사 공개 소비는 국적을 동네별로 나누지 않아 중국 대신 외국인 전체 소비로 계산했습니다.")
    if cruise_id == CRUISE_NATION:
        table = annotate(table, "많은 달·적은 달은 관광공사 '외국인 크루즈 입도객'의 중국 국적 입도객 수로 나눴습니다.")
    return MetricResult(
        f"크루즈 기항 효과({segment})",
        "(중국발 크루즈 관광객이 중앙값보다 많았던 달의 평균 이용금액 ÷ 적었던 달의 평균 − 1) × 100. corr 는 달별 크루즈 관광객 수와 이용금액의 상관계수(−1~1)",
        table,
        _sources((card_source(con, months), months), (cruise_id, months)),
    )


# ---------- 시간대·POS·매장·입도 데이터를 더한 지표 ----------
def night_share(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "외국인") -> MetricResult:
    """저녁·밤 소비 비중: 해당 이용자 구분의 카드 소비 중 저녁·밤 시간대 비중과 1년 전 대비 변화.

    낮에만 들렀다 떠나는 관광(크루즈 기항·단체 일정)은 저녁·밤 소비가 적다. 비중이 오르면 머무는 관광으로 바뀌는 신호로 본다.
    """
    dims, months = list(LEVELS[level]), [month, previous_year(month)]
    side = {"side": f"CASE WHEN {NIGHT_CONDITION} THEN 'part' ELSE 'rest' END"}
    condition = SEGMENTS[segment] if segment != "전체" else "user_type <> '법인'"
    totals = _card_totals(con, months, level, biz_prefix, condition, side, dataset_id=CARD_TIME)
    pair, support = _split_share(totals, dims, month)
    pair = pair.rename(columns={"share_pct": "night_share_pct"})
    values = ["night_share_pct", "change_pp"]
    table = _release(pair[[*dims, *values, *support]], values, support, "night_share_pct")
    return MetricResult(
        f"저녁·밤 소비 비중({segment})",
        f"{segment} 이용금액 중 저녁·밤 시간대 이용금액 비중 × 100. 변화는 전년 같은 달과의 차이(%p)",
        table,
        _sources((time_source(con, months), months)),
    )


def _arrivals(con, months: list[str], segment: str) -> pd.DataFrame:
    """제주 입도객(P-02)을 달별로 더한다. 외국인은 내국인관광객을 뺀 모든 국적."""
    table = _require(con, ARRIVALS, months)
    condition = "segment <> '내국인관광객'" if segment == "외국인" else "segment = ?"
    params = [*months] + ([] if segment == "외국인" else [segment])
    return con.execute(
        f'SELECT month, sum(jeju_arrivals) AS arrivals FROM "{table}" WHERE month IN ({", ".join("?" for _ in months)}) AND {condition} GROUP BY month',
        params,
    ).df()


def per_visitor(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "외국인") -> MetricResult:
    """입도객 1인당 소비 증감: 입도객이 늘어난 만큼 이 지역 카드 소비도 늘었는가.

    1인당 소비 = 지역의 해당 관광객 이용금액 ÷ 그 달 제주 입도객(같은 국적 묶음). 전년 같은 달과 비교한다.
    0 보다 작으면 사람이 늘어난 속도를 이 지역 소비가 따라가지 못한 것이다.
    """
    if segment not in ("외국인", *FOREIGN_TYPES, "내국인관광객"):
        raise ValueError("입도객 1인당 소비는 외국인·중국·일본·동남아·기타외국·내국인관광객 중 하나로 봅니다")
    dims, months = list(LEVELS[level]), [month, previous_year(month)]
    arrivals = _arrivals(con, months, segment)
    totals = _card_totals(con, months, level, biz_prefix, SEGMENTS[segment]).merge(arrivals, on="month")
    pair = _pair(totals, dims, month)
    pair["per_visitor_yoy_pct"] = _pct(pair["amt_now"] / pair["arrivals_now"], pair["amt_before"] / pair["arrivals_before"])
    pair["spend_yoy_pct"] = _pct(pair["amt_now"], pair["amt_before"])
    pair["arrival_yoy_pct"] = _pct(pair["arrivals_now"], pair["arrivals_before"])
    values = ["per_visitor_yoy_pct", "spend_yoy_pct", "arrival_yoy_pct"]
    support = ["users_now", "users_before"]
    table = _release(pair[[*dims, *values, *support]], values, support, "per_visitor_yoy_pct")
    table = annotate(table, "입도객은 제주 전체 값이라 모든 지역에 같은 분모를 씁니다. 카드는 비씨카드 결제만 담아 1인당 금액 자체보다 증감으로 읽어야 합니다.")
    return MetricResult(
        f"입도객 1인당 소비 증감({segment})",
        "(이번 달 이용금액 ÷ 제주 입도객) ÷ (전년 같은 달 이용금액 ÷ 입도객) − 1, × 100. 입도객은 제주특별자치도관광협회 입도통계(확정치)",
        table,
        _sources((card_source(con, months), months), (ARRIVALS, months)),
    )


def _card_regions(con, month: str) -> pd.DataFrame:
    """카드(1-08)에 들어 있는 (시, 법정동) → 권역 짝. POS 처럼 권역이 없는 데이터에 권역을 붙일 때 쓴다."""
    if not loaded_months(con, CARD_MONTHLY):
        raise MissingData(get_dataset(CARD_MONTHLY), [month])
    table = get_dataset(CARD_MONTHLY).table
    return con.execute(f'SELECT DISTINCT city, bjd, dstrct_se FROM "{table}"').df().drop_duplicates(["city", "bjd"])


def restaurant_pulse(con, month: str, level: str = "bjd", biz_prefix: str | None = None) -> MetricResult:
    """동네 식당 체감: 외식업 POS(현금·모든 카드 포함)의 가게당 매출 증감을 외국인 음식점 카드 소비 증감과 나란히 본다.

    외국인 음식점 소비가 늘었는데 가게당 매출이 제자리면, 늘어난 돈이 동네 식당 전체로는 퍼지지 않은 것이다.
    biz_prefix 는 쓰지 않는다(항상 음식점). POS 표본 가게가 10 곳 미만인 지역은 가린다.
    """
    dims, months = list(LEVELS[level]), [month, previous_year(month)]
    table = _require(con, POS_FOOD, months)
    regions = _card_regions(con, month)
    pos = con.execute(
        f"""SELECT city_nm AS city, bjd_nm AS bjd, base_year_month AS month,
                   sum(TRY_CAST(replace(CAST(sale_amt AS VARCHAR), ',', '') AS DOUBLE)) AS sale, sum(TRY_CAST(replace(CAST(store_cnt AS VARCHAR), ',', '') AS DOUBLE)) AS stores
            FROM "{table}" WHERE base_year_month IN ({", ".join("?" for _ in months)}) GROUP BY ALL""",
        months,
    ).df().merge(regions, on=["city", "bjd"], how="inner").drop(columns="city")
    pos = _aggregate_to(pos, level, ["sale", "stores"])
    pair = _pair(pos, dims, month)
    pair["per_store_yoy_pct"] = _pct(pair["sale_now"] / pair["stores_now"], pair["sale_before"] / pair["stores_before"])
    pair["pos_yoy_pct"] = _pct(pair["sale_now"], pair["sale_before"])

    foreign = _pair(_card_totals(con, months, level, "I56", SEGMENTS["외국인"]), dims, month)
    foreign["foreign_food_yoy_pct"] = _pct(foreign["amt_now"], foreign["amt_before"])
    # 카드 근거 이용자가 10 명 미만이면 그 값만 비운다 (POS 값은 따로 가드를 거친다)
    foreign.loc[foreign[["users_now", "users_before"]].min(axis=1) < 10, "foreign_food_yoy_pct"] = np.nan
    merged = pair.merge(foreign[[*dims, "foreign_food_yoy_pct"]], on=dims, how="left") if dims else pair.assign(foreign_food_yoy_pct=foreign["foreign_food_yoy_pct"].iloc[0] if len(foreign) else np.nan)
    values = ["per_store_yoy_pct", "pos_yoy_pct", "foreign_food_yoy_pct"]
    support = ["stores_now", "stores_before"]
    released = _release(merged[[*dims, *values, *support]], values, support, "per_store_yoy_pct")
    released = annotate(released, "신뢰 수준은 이 지표에서 POS 표본 가게 수 기준입니다.")
    return MetricResult(
        "동네 식당 체감",
        "가게당 POS 매출(총매출액 ÷ 표본 가게 수)의 전년 같은 달 대비 증감률. 함께 보는 값은 외국인 음식점(I56) 카드 이용금액 증감률",
        released,
        _sources((POS_FOOD, months), (card_source(con, months), months)),
    )


def local_owner(con, month: str, level: str = "city", biz_prefix: str | None = None, segment: str = "외국인") -> MetricResult:
    """도민 가게 몫(추정): 해당 관광객 카드 소비 중 제주도민 개인 매장으로 갔을 몫.

    업종마다 '제주도민 개인' 매장 비중(1-17)을 구해, 그 업종의 이용금액에 곱해 더한다(업종 안에서는 매출이 매장 수에 비례한다고 가정).
    면세점·대형마트처럼 법인 매장이 많은 업종으로 소비가 몰리면 값이 내려간다. 매장 수가 시 단위라 시·전체 단위로만 계산한다.
    함께 보는 값은 같은 방법으로 계산한 개인 이용자 전체의 몫이다.
    """
    level = level if level in ("city", "all") else "city"
    dims, months = list(LEVELS[level]), [month, previous_year(month)]
    store_table = _require(con, STORE_COUNT, months)
    stores = con.execute(
        f"""SELECT city, base_year_month AS month, biz_cd,
                   sum(CASE WHEN owner_gb = ? THEN store_cnt ELSE 0 END) AS local_stores, sum(store_cnt) AS stores
            FROM "{store_table}" WHERE base_year_month IN ({", ".join("?" for _ in months)}) GROUP BY ALL""",
        [LOCAL_OWNER, *months],
    ).df()
    if not stores.empty and stores["local_stores"].sum() == 0:
        raise ValueError(f"업종별 매장 수의 매장주 구분에 '{LOCAL_OWNER}' 이 없어 도민 가게 몫을 계산할 수 없습니다. 매장주 구분 값을 확인해 주세요.")
    stores = stores[stores["stores"] > 0].assign(local_ratio=lambda f: f["local_stores"] / f["stores"])

    def take(condition: str) -> pd.DataFrame:
        spend = _card_totals(con, months, "city", biz_prefix, condition, {"biz_cd": "biz_cd"})
        merged = spend.merge(stores, on=["city", "month", "biz_cd"], how="inner")
        merged["local_amt"] = merged["amt"] * merged["local_ratio"]
        grouped = merged.groupby([*dims, "month"], as_index=False)[["local_amt", "amt", "users", "stores"]].sum() if dims else merged.groupby("month", as_index=False)[["local_amt", "amt", "users", "stores"]].sum()
        grouped["take"] = grouped["local_amt"] / grouped["amt"] * 100
        return grouped

    part = _pair(take(SEGMENTS[segment]), dims, month)
    base = _pair(take("user_type <> '법인'"), dims, month)
    part["local_take_pct"] = part["take_now"].round(1)
    part["change_pp"] = (part["take_now"] - part["take_before"]).round(1)
    base["baseline_pct"] = base["take_now"].round(1)
    merged = part.merge(base[[*dims, "baseline_pct"]], on=dims) if dims else part.assign(baseline_pct=base["baseline_pct"].iloc[0])
    values = ["local_take_pct", "change_pp", "baseline_pct"]
    support = ["users_now", "users_before", "stores_now"]
    table = _release(merged[[*dims, *values, *support]], values, support, "local_take_pct")
    table = annotate(table, "업종 안에서 매출이 매장 수에 비례한다고 가정한 추정값입니다. 실제 매장별 매출은 아닙니다.")
    return MetricResult(
        f"도민 가게 몫({segment}, 추정)",
        f"Σ(업종별 {segment} 이용금액 × 그 업종의 제주도민 개인 매장 비중) ÷ {segment} 이용금액 × 100. 변화는 전년 같은 달과의 차이(%p), 비교값은 개인 이용자 전체",
        table,
        _sources((card_source(con, months), months), (STORE_COUNT, months)),
    )


def store_yield(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "관광객") -> MetricResult:
    """가게당 관광객 소비: 동네의 관광객 소비 비중 ÷ 그 동네의 가게 비중.

    1 보다 크면 가게 수에 비해 관광객 돈이 많이 떨어지는 동네, 작으면 가게는 많은데 관광객 소비가 적은 동네.
    매장 수는 소상공인 상가정보(P-05, 공개)이고 소비는 센터 카드(1-08) 또는 관광공사 공개 소비(P-03)다.
    업종을 고르지 않으면 관광객이 주로 쓰는 소매·숙박·음식점·여가(TOURIST_BIZ)만 양쪽에서 같이 센다.
    금액 자체(가게당 몇 원)는 내보내지 않는다. 매장 수가 공개라 곱하면 카드 매출 합계가 되살아나기 때문이다.
    """
    level = level if level in LEVELS else "bjd"
    store_table = get_dataset(STORES_PUBLIC).table
    if not con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [store_table]).fetchone():
        raise MissingData(get_dataset(STORES_PUBLIC), [])
    source = card_source(con, [month])
    if source == SPEND_PUBLIC and biz_prefix and not any(f"{code}000".startswith(biz_prefix) for code in TOURIST_BIZ):
        raise ValueError("관광공사 공개 소비 데이터는 업종이 소매(G47)·숙박(I55)·음식점(I56)·여가(R91) 넷뿐이라 그보다 자세한 업종으로는 볼 수 없습니다")
    tourist_biz = ", ".join(f"'{code}'" for code in TOURIST_BIZ)
    condition = SEGMENTS[segment] if biz_prefix else f"({SEGMENTS[segment]}) AND substr(biz_cd, 1, 3) IN ({tourist_biz})"
    spend = _card_totals(con, [month], "bjd", biz_prefix, condition).drop(columns="month")
    spend["city"] = spend["dstrct_se"].str.split(" ").str[0]
    name_column = "emd_nm" if source == SPEND_PUBLIC else "bjd_nm"   # 관광공사 소비는 행정동, 센터 카드는 법정동
    store_filter = "ksic_cd LIKE ?" if biz_prefix else f"substr(ksic_cd, 1, 3) IN ({tourist_biz})"
    stores = con.execute(
        f'SELECT city, {name_column} AS bjd, sum(stores) AS stores, max(snapshot) AS snapshot FROM "{store_table}" WHERE {store_filter} GROUP BY city, {name_column}',
        [biz_prefix + "%"] if biz_prefix else [],
    ).df()
    snapshot = stores["snapshot"].max() if not stores.empty else ""
    merged = spend.merge(stores.drop(columns="snapshot"), on=["city", "bjd"])
    merged = merged[merged["stores"] > 0]
    dims = list(LEVELS[level])
    grouped = merged.groupby(dims, as_index=False)[["amt", "users", "stores"]].sum() if dims else merged[["amt", "users", "stores"]].sum().to_frame().T
    spend_share_ = grouped["amt"] / merged["amt"].sum()
    store_share = grouped["stores"] / merged["stores"].sum()
    grouped["yield_index"] = (spend_share_ / store_share).round(2)
    grouped["spend_share_pct"] = (spend_share_ * 100).round(1)
    grouped["store_share_pct"] = (store_share * 100).round(1)
    values = ["yield_index", "spend_share_pct", "store_share_pct"]
    table = _release(grouped[[*dims, *values, "users", "stores"]], values, ["users", "stores"], "yield_index")
    when = f"{snapshot[:4]}년 {int(snapshot[4:])}월" if len(snapshot) == 6 else "최근"
    scope = f"업종 {biz_prefix}" if biz_prefix else "소매·숙박·음식점·여가 업종"
    table = annotate(table, f"매장 수는 {when} 말 영업 중인 상가(소상공인시장진흥공단)이며 소비 달과 시점이 다를 수 있습니다. {scope}만 양쪽에서 같이 셉니다.")
    return MetricResult(
        f"가게당 관광객 소비({segment})",
        f"(동네의 {segment} 소비금액 비중) ÷ (동네의 가게 수 비중). 1 = 제주 평균, 1보다 크면 가게 수에 비해 관광객 소비가 많은 곳",
        table,
        (*_sources((source, [month])), *(_sources((STORES_PUBLIC, [snapshot])) if snapshot else ())),
    )


# ---------- 메인 화면의 지명 카드: 관광공사 공개 데이터로 본 동네 한눈에 ----------
_PUBLIC_TOURIST_INDUSTRIES = ("소매업", "숙박업", "음식점업", "예술스포츠여가업")   # TOURIST_BIZ 와 같은 업종
PLACE_MIN_COUNT = 10   # 이용건수가 이보다 적으면 그 값은 비운다 (반출 기준과 같은 문턱)


def place_profile(con, names: list[str], month: str | None = None) -> dict:
    """행정동 몇 곳을 묶어 외국인 관광객 소비·방문 변화, 낙수 지수, 가게당 소비, 최근 2년 추이를 돌려준다.

    공개 데이터(P-03 소비, P-04 방문객, P-05 매장 수)만 쓴다. names 가 비면 제주 전체.
    값은 모두 증감률·지수·0~1 로 맞춘 추이라 금액 자체는 나가지 않는다.
    """
    spend_months = loaded_months(con, SPEND_PUBLIC)
    if not spend_months:
        return {"available": False}
    month = month if month in spend_months else spend_months[-1]
    before = previous_year(month)
    spend = get_dataset(SPEND_PUBLIC).table
    everywhere = not names
    where = "TRUE" if everywhere else f"emd_nm IN ({', '.join('?' for _ in names)})"
    params = [] if everywhere else list(names)

    def foreign_spend(m: str, scope: str = where, scope_params: list | None = None, industries: bool = False) -> tuple[float, float]:
        extra = f" AND industry IN ({', '.join(repr(i) for i in _PUBLIC_TOURIST_INDUSTRIES)})" if industries else ""
        row = con.execute(
            f"SELECT coalesce(sum(use_amt), 0), coalesce(sum(use_cnt), 0) FROM \"{spend}\" WHERE month = ? AND tourist_type = '외국인 관광객' AND {scope}{extra}",
            [m, *(params if scope_params is None else scope_params)],
        ).fetchone()
        return float(row[0]), float(row[1])

    def change(now: float, past: float) -> float | None:
        return round((now / past - 1) * 100, 1) if past > 0 else None

    amt_now, cnt_now = foreign_spend(month)
    amt_before, cnt_before = foreign_spend(before)
    result = {"available": True, "month": month, "names": names or ["제주 전체"],
              "spend_yoy": change(amt_now, amt_before) if min(cnt_now, cnt_before) >= PLACE_MIN_COUNT else None}
    all_amt = con.execute(f"SELECT coalesce(sum(use_amt), 0) FROM \"{spend}\" WHERE month = ? AND {where}", [month, *params]).fetchone()[0]
    result["foreign_share"] = round(amt_now / all_amt * 100, 1) if all_amt and cnt_now >= PLACE_MIN_COUNT else None

    visitor_months = loaded_months(con, VISITORS_PUBLIC)
    result["visitors_yoy"] = result["conversion_index"] = None
    if month in visitor_months:
        visitors = get_dataset(VISITORS_PUBLIC).table
        def foreign_visitors(m: str, scope: str = where, scope_params: list | None = None) -> float:
            return float(con.execute(f"SELECT coalesce(sum(foreign_visitors), 0) FROM \"{visitors}\" WHERE month = ? AND {scope}", [m, *(params if scope_params is None else scope_params)]).fetchone()[0])
        v_now = foreign_visitors(month)
        if before in visitor_months:
            result["visitors_yoy"] = change(v_now, foreign_visitors(before))
        if not everywhere and v_now >= PLACE_MIN_COUNT and cnt_now >= PLACE_MIN_COUNT:
            jeju_amt, _ = foreign_spend(month, "TRUE", [])
            jeju_visitors = foreign_visitors(month, "TRUE", [])
            result["conversion_index"] = round((amt_now / jeju_amt) / (v_now / jeju_visitors), 2) if jeju_amt and jeju_visitors else None

    result["store_yield"] = None
    store_table = get_dataset(STORES_PUBLIC).table
    if not everywhere and con.execute("SELECT 1 FROM information_schema.tables WHERE table_name = ?", [store_table]).fetchone():
        biz = f"substr(ksic_cd, 1, 3) IN ({', '.join(repr(c) for c in TOURIST_BIZ)})"
        here = con.execute(f"SELECT coalesce(sum(stores), 0) FROM \"{store_table}\" WHERE {biz} AND {where}", params).fetchone()[0]
        total = con.execute(f"SELECT coalesce(sum(stores), 0) FROM \"{store_table}\" WHERE {biz}").fetchone()[0]
        t_amt, t_cnt = foreign_spend(month, industries=True)
        j_amt, _ = foreign_spend(month, "TRUE", [], industries=True)
        if here >= PLACE_MIN_COUNT and t_cnt >= PLACE_MIN_COUNT and j_amt and total:
            result["store_yield"] = round((t_amt / j_amt) / (here / total), 2)

    # 최근 24개월 외국인 소비 추이를 0~1 로 맞춘다 (금액은 내보내지 않는다)
    recent = [m for m in spend_months if m <= month][-24:]
    rows = dict(con.execute(
        f"SELECT month, sum(use_amt) FROM \"{spend}\" WHERE tourist_type = '외국인 관광객' AND {where} AND month IN ({', '.join('?' for _ in recent)}) GROUP BY month",
        [*params, *recent],
    ).fetchall())
    peak = max(rows.values(), default=0) or 1
    result["trend"] = [{"month": m, "level": round(float(rows.get(m, 0)) / float(peak), 3)} for m in recent]

    if not everywhere:   # 진단 화면에서 그 동네를 고를 때 쓰는 권역
        view = _public_card_view(con)
        regions = con.execute(f"SELECT DISTINCT dstrct_se, bjd FROM \"{view}\" WHERE bjd IN ({', '.join('?' for _ in names)})", list(names)).fetchall()
        result["areas"] = [{"dstrct_se": r[0], "bjd": r[1]} for r in regions]
    result["source"] = citation("제주 지역별 소비·방문객(읍면동)", f"{month[:4]}.{month[4:]}", "제주관광공사(제주관광빅데이터플랫폼)")
    return result
