import csv

import duckdb
import pandas as pd
import pytest

from golmok import metrics
from golmok.guard import SUPPRESSED_COLUMN
from golmok.loader import load_center_csv
from golmok.schemas import get_dataset

DEFAULTS = {
    "dstrct_se": "제주시 서부", "city": "제주시", "area_type": "읍면", "bjd": "한림읍",
    "biz_cd": "I56220", "biz_nm": "비알콜 음료점업", "user_type": "내국인", "tour_type": "개별",
    "age": "30", "sex": "여성", "week": "월요일", "time": "14",
    "user_cnt": 100, "use_cnt": 100, "use_amt": 1_000_000,
    # 유동인구 (내국인 4-11, 외국인 4-15)
    "emd_cd": "50110250", "emd_nm": "제주시 한림읍", "resd": "그외", "nationality": "CHN",
    "resd_pop_cnt": 0, "work_pop_cnt": 0, "visit_pop_cnt": 100,
    # 종량제봉투 5-03
    "city_name": "제주시", "emd_name": "한림읍", "wastebag_type": "가연성", "wastebag_liter": "20L", "sale_count": 10, "sale_amount": 4800,
    # 크루즈 P-01
    "calls": 10, "passengers": 20000, "china_calls": 8, "china_passengers": 15000, "jeju_port_calls": 5, "gangjeong_calls": 5,
}
EAST = {"dstrct_se": "제주시 동부", "bjd": "구좌읍", "emd_nm": "제주시 구좌읍", "emd_cd": "50110256", "emd_name": "구좌읍"}


def load(con, tmp_path, dataset_id, rows):
    """명세서 컬럼 순서의 CSV 를 만들어 적재한다. rows 는 DEFAULTS 를 덮어쓰는 값."""
    columns = get_dataset(dataset_id).column_names
    path = tmp_path / f"{dataset_id}_{len(list(tmp_path.iterdir()))}.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            row = {**DEFAULTS, **row}
            if "month" in row:
                row.update(base_year_month=row["month"], base_date=row["month"] + "15", pt_year=row["month"][:4], pt_month=str(int(row["month"][4:])))
            writer.writerow({c: row.get(c, "") for c in columns})
    load_center_csv(con, path)


@pytest.fixture
def con():
    return duckdb.connect(":memory:")


def row_for(result, **where):
    frame = result.table.frame
    for column, value in where.items():
        frame = frame[frame[column] == value]
    assert len(frame) == 1, frame
    return frame.iloc[0]


def test_sales_yoy_reports_change_and_hides_support(con, tmp_path):
    load(con, tmp_path, "1-08", [
        {"month": "202510", "use_amt": 1_000_000},
        {"month": "202610", "use_amt": 800_000},
        # 표본이 너무 적은 동네
        {"month": "202510", "bjd": "한경면", "user_cnt": 5, "use_amt": 100_000},
        {"month": "202610", "bjd": "한경면", "user_cnt": 6, "use_amt": 300_000},
    ])
    result = metrics.sales_yoy(con, "202610")
    assert row_for(result, bjd="한림읍")["yoy_pct"] == -20.0
    assert row_for(result, bjd="한림읍")["신뢰수준"] == "높음"
    thin = row_for(result, bjd="한경면")
    assert thin[SUPPRESSED_COLUMN] and pd.isna(thin["yoy_pct"])
    assert not {"users_now", "users_before"} & set(result.table.frame.columns)
    assert result.sources == ("제주빅데이터센터, 카드 이용 현황(읍면동)_v02(2025.10·2026.10)",)


def test_sales_yoy_filters_segment_and_business(con, tmp_path):
    load(con, tmp_path, "1-08", [
        {"month": "202510", "use_amt": 1_000_000},
        {"month": "202610", "use_amt": 900_000},
        {"month": "202510", "user_type": "중국", "use_amt": 1_000_000},
        {"month": "202610", "user_type": "중국", "use_amt": 1_500_000},
        {"month": "202510", "biz_cd": "I55111", "use_amt": 1_000_000},
        {"month": "202610", "biz_cd": "I55111", "use_amt": 5_000_000},
    ])
    assert row_for(metrics.sales_yoy(con, "202610", biz_prefix="I56", segment="내국인관광객"), bjd="한림읍")["yoy_pct"] == -10.0
    assert row_for(metrics.sales_yoy(con, "202610", biz_prefix="I56", segment="중국"), bjd="한림읍")["yoy_pct"] == 50.0
    assert row_for(metrics.sales_yoy(con, "202610", biz_prefix="I56", segment="외국인"), bjd="한림읍")["yoy_pct"] == 50.0
    assert metrics.sales_yoy(con, "202610", level="all", biz_prefix="I56").table.frame["yoy_pct"].tolist() == [20.0]


def test_same_named_districts_in_two_cities_stay_separate(con, tmp_path):
    jeju = {"bjd": "월평동", "dstrct_se": "제주시 중부(동)"}
    seogwipo = {"bjd": "월평동", "dstrct_se": "서귀포시 중부", "city": "서귀포시"}
    load(con, tmp_path, "1-08", [
        {"month": "202510", **jeju, "use_amt": 1_000_000}, {"month": "202610", **jeju, "use_amt": 1_100_000},
        {"month": "202510", **seogwipo, "use_amt": 1_000_000}, {"month": "202610", **seogwipo, "use_amt": 700_000},
    ])
    result = metrics.sales_yoy(con, "202610")
    assert row_for(result, dstrct_se="제주시 중부(동)")["yoy_pct"] == 10.0
    assert row_for(result, dstrct_se="서귀포시 중부")["yoy_pct"] == -30.0
    assert set(metrics.sales_yoy(con, "202610", level="city").table.frame["city"]) == {"제주시", "서귀포시"}


def test_missing_previous_year_names_what_to_download(con, tmp_path):
    load(con, tmp_path, "1-08", [{"month": "202610"}])
    with pytest.raises(metrics.MissingData) as error:
        metrics.sales_yoy(con, "202610")
    assert error.value.months == ["202510"]
    assert "카드 이용 현황(읍면동)_v02" in str(error.value) and "2025년 10월" in str(error.value) and "센터에서" in str(error.value)


def test_missing_table_is_reported_before_any_query(con, tmp_path):
    load(con, tmp_path, "1-08", [{"month": "202610"}])
    with pytest.raises(metrics.MissingData, match="외국인 유동인구"):
        metrics.conversion_index(con, "202610")


def test_spend_share_excludes_corporate_cards_and_tracks_change(con, tmp_path):
    rows = []
    for month, foreign_amount in (("202510", 200_000), ("202610", 400_000)):
        rows += [
            {"month": month, "user_type": "중국", "use_amt": foreign_amount},
            {"month": month, "user_type": "내국인", "use_amt": 400_000},
            {"month": month, "user_type": "제주도민", "use_amt": 200_000},
            {"month": month, "user_type": "법인", "use_amt": 9_000_000},
        ]
    load(con, tmp_path, "1-08", rows)
    row = row_for(metrics.spend_share(con, "202610"), bjd="한림읍")
    assert row["share_pct"] == 40.0 and row["change_pp"] == 15.0
    assert row_for(metrics.spend_share(con, "202610", segment="도민"), bjd="한림읍")["share_pct"] == 20.0
    with pytest.raises(ValueError):
        metrics.spend_share(con, "202610", segment="전체")


def test_spend_share_hides_rows_with_only_that_segment(con, tmp_path):
    load(con, tmp_path, "1-08", [{"month": "202510", "user_type": "중국"}, {"month": "202610", "user_type": "중국"}])
    row = row_for(metrics.spend_share(con, "202610"), bjd="한림읍")
    assert row[SUPPRESSED_COLUMN] and pd.isna(row["share_pct"])


def test_ticket_size_yoy(con, tmp_path):
    load(con, tmp_path, "1-08", [
        {"month": "202510", "use_cnt": 100, "use_amt": 2_000_000},
        {"month": "202610", "use_cnt": 100, "use_amt": 1_500_000},
    ])
    assert row_for(metrics.ticket_size_yoy(con, "202610"), bjd="한림읍")["ticket_yoy_pct"] == -25.0


def test_gap_fill_measures_how_much_foreign_spend_covers_domestic_drop(con, tmp_path):
    load(con, tmp_path, "1-08", [
        {"month": "202510", "user_type": "내국인", "use_amt": 1_000_000},
        {"month": "202610", "user_type": "내국인", "use_amt": 800_000},       # 내국인 -200,000
        {"month": "202510", "user_type": "중국", "use_amt": 300_000},
        {"month": "202610", "user_type": "중국", "use_amt": 350_000},         # 외국인 +50,000 → 메움률 25%
        {"month": "202510", **EAST, "user_type": "내국인", "use_amt": 500_000},
        {"month": "202610", **EAST, "user_type": "내국인", "use_amt": 600_000},  # 내국인이 늘어난 곳은 메움률 없음
        {"month": "202510", **EAST, "user_type": "일본", "use_amt": 100_000},
        {"month": "202610", **EAST, "user_type": "일본", "use_amt": 100_000},
    ])
    result = metrics.gap_fill(con, "202610")
    west = row_for(result, bjd="한림읍")
    assert west["fill_pct"] == 25.0 and west["domestic_yoy_pct"] == -20.0 and west["foreign_yoy_pct"] == 16.7
    assert west["tourist_yoy_pct"] == round((1_150_000 / 1_300_000 - 1) * 100, 1)
    east = row_for(result, bjd="구좌읍")
    assert pd.isna(east["fill_pct"]) and not east[SUPPRESSED_COLUMN]


def test_conversion_index_compares_spend_share_with_visitor_share(con, tmp_path):
    load(con, tmp_path, "1-08", [
        {"month": "202610", "user_type": "중국", "use_amt": 750_000},           # 서부: 매출의 75%
        {"month": "202610", **EAST, "user_type": "중국", "use_amt": 250_000},   # 동부: 매출의 25%
    ])
    load(con, tmp_path, "4-15", [
        {"month": "202610", "visit_pop_cnt": 500},                              # 서부: 방문객의 50%
        {"month": "202610", **EAST, "visit_pop_cnt": 500},                      # 동부: 방문객의 50%
        {"month": "202610", "nationality": "JPN", "visit_pop_cnt": 99_999},     # 중국만 볼 때 일본인은 제외
    ])
    result = metrics.conversion_index(con, "202610", segment="중국")
    assert row_for(result, dstrct_se="제주시 서부")["conversion_index"] == 1.5
    assert row_for(result, dstrct_se="제주시 동부")["conversion_index"] == 0.5
    assert row_for(result, dstrct_se="제주시 동부")["visitor_share_pct"] == 50.0
    assert len(result.sources) == 2 and "단기체류 외국인 유동인구" in result.sources[1]
    everyone = metrics.conversion_index(con, "202610", segment="외국인")
    assert row_for(everyone, dstrct_se="제주시 서부")["conversion_index"] < 1 < row_for(everyone, dstrct_se="제주시 동부")["conversion_index"]   # 일본인 방문이 서부에 몰려 있으므로


def test_conversion_index_for_domestic_tourists_uses_korean_population(con, tmp_path):
    load(con, tmp_path, "1-08", [{"month": "202610", "use_amt": 750_000}, {"month": "202610", **EAST, "use_amt": 250_000}])
    load(con, tmp_path, "4-11", [
        {"month": "202610", "visit_pop_cnt": 500},
        {"month": "202610", **EAST, "visit_pop_cnt": 500},
        {"month": "202610", "resd": "제주", "visit_pop_cnt": 99_999},  # 도민은 방문객에서 제외
    ])
    result = metrics.conversion_index(con, "202610", segment="내국인관광객")
    assert row_for(result, dstrct_se="제주시 서부")["conversion_index"] == 1.5


def test_waste_burden_per_visitor_and_yoy(con, tmp_path):
    # 하루 방문 체류 인원 = 시각별 방문인구 합 ÷ 24 ÷ 날 수. 한 시각 행만 넣었으므로 2400 명이면 100명/일
    load(con, tmp_path, "4-15", [{"month": "202510", "visit_pop_cnt": 1200}, {"month": "202610", "visit_pop_cnt": 2400}])
    load(con, tmp_path, "4-11", [{"month": "202510", "visit_pop_cnt": 1200}, {"month": "202610", "visit_pop_cnt": 2400}])
    load(con, tmp_path, "5-03", [
        {"month": "202510", "sale_count": 10, "wastebag_liter": "20L"},             # 하루 200L
        {"month": "202610", "sale_count": 20, "wastebag_liter": "20L"},             # 하루 400L
        {"month": "202610", "wastebag_type": "재사용", "sale_count": 99, "wastebag_liter": "20L"},  # 생활 배출이 아니라 제외
    ])
    row = row_for(metrics.waste_burden(con, "202610"), emd_nm="제주시 한림읍")
    assert row["liters_per_1000"] == 2000.0          # 400L ÷ 200명 × 1000
    assert row["bag_yoy_pct"] == 100.0 and row["foreign_visit_yoy_pct"] == 100.0
    assert "visitors" not in metrics.waste_burden(con, "202610").table.frame.columns


def test_waste_burden_without_previous_year_leaves_change_empty(con, tmp_path):
    load(con, tmp_path, "4-15", [{"month": "202610", "visit_pop_cnt": 2400}])
    load(con, tmp_path, "4-11", [{"month": "202610", "visit_pop_cnt": 2400}])
    load(con, tmp_path, "5-03", [{"month": "202610", "sale_count": 20, "wastebag_liter": "20L"}])
    result = metrics.waste_burden(con, "202610", level="all")
    assert result.table.frame["liters_per_1000"].tolist() == [2000.0]
    assert pd.isna(result.table.frame["bag_yoy_pct"].iloc[0]) and any("전년" in note for note in result.table.notes)


def test_cruise_lift_splits_months_by_cruise_passengers(con, tmp_path):
    months = ["202601", "202602", "202603", "202604"]
    load(con, tmp_path, "1-08", [{"month": m, "user_type": "중국", "use_amt": amt} for m, amt in zip(months, (100, 100, 150, 150))])
    load(con, tmp_path, "P-01", [{"month": m, "china_passengers": pax} for m, pax in zip(months, (1000, 1000, 5000, 5000))])
    result = metrics.cruise_lift(con, "202604")
    row = row_for(result, bjd="한림읍")
    assert row["lift_pct"] == 50.0 and row["corr"] == 1.0 and row["months_high"] == 2 and row["months_low"] == 2
    assert result.sources[1].startswith("제주특별자치도(크루즈선 입항실적), 크루즈 입항 실적(월별)(2026.01~2026.04)")


def test_cruise_lift_needs_enough_overlapping_months(con, tmp_path):
    load(con, tmp_path, "1-08", [{"month": m, "user_type": "중국"} for m in ("202601", "202602", "202603", "202604")])
    load(con, tmp_path, "P-01", [{"month": "202601"}])
    with pytest.raises(metrics.MissingData) as error:
        metrics.cruise_lift(con, "202604")
    assert error.value.dataset.id == "P-01" and "202604" in error.value.months and "같은 컬럼" in str(error.value)


def test_night_share_counts_evening_and_night_spend(con, tmp_path):
    load(con, tmp_path, "1-14", [
        {"month": "202510", "user_type": "중국", "time": "저녁", "use_amt": 300_000},
        {"month": "202510", "user_type": "중국", "time": "오후", "use_amt": 700_000},
        {"month": "202610", "user_type": "중국", "time": "밤", "use_amt": 500_000},
        {"month": "202610", "user_type": "중국", "time": "오전", "use_amt": 500_000},
        {"month": "202610", "user_type": "제주도민", "time": "밤", "use_amt": 9_000_000},   # 다른 구분은 섞지 않는다
        {"month": "202510", "user_type": "제주도민", "time": "밤", "use_amt": 9_000_000},
    ])
    result = metrics.night_share(con, "202610", "bjd", None, "외국인")
    row = row_for(result, bjd="한림읍")
    assert (row["night_share_pct"], row["change_pp"]) == (50.0, 20.0)
    assert "카드 이용 현황(시간대)_v02" in result.sources[0]


def test_per_visitor_divides_spend_by_island_arrivals(con, tmp_path):
    load(con, tmp_path, "1-08", [
        {"month": "202510", "user_type": "중국", "use_amt": 1_000_000},
        {"month": "202610", "user_type": "중국", "use_amt": 1_200_000},
    ])
    load(con, tmp_path, "P-02", [
        {"month": "202510", "country": "중국", "segment": "중국", "jeju_arrivals": 1000, "korea_arrivals": 5000},
        {"month": "202610", "country": "중국", "segment": "중국", "jeju_arrivals": 1500, "korea_arrivals": 6000},
        {"month": "202610", "country": "일본", "segment": "일본", "jeju_arrivals": 99999, "korea_arrivals": 99999},
    ])
    row = row_for(metrics.per_visitor(con, "202610", "bjd", None, "중국"), bjd="한림읍")
    assert (row["per_visitor_yoy_pct"], row["spend_yoy_pct"], row["arrival_yoy_pct"]) == (-20.0, 20.0, 50.0)
    with pytest.raises(ValueError):
        metrics.per_visitor(con, "202610", "bjd", None, "도민")


def test_restaurant_pulse_compares_pos_per_store_with_foreign_card(con, tmp_path):
    load(con, tmp_path, "1-08", [
        {"month": "202510", "user_type": "중국", "use_amt": 1_000_000},
        {"month": "202610", "user_type": "중국", "use_amt": 1_500_000},
    ])
    load(con, tmp_path, "1-19", [
        {"month": "202510", "city_nm": "제주시", "bjd_nm": "한림읍", "sale_amt": 10_000_000, "store_cnt": 20},
        {"month": "202610", "city_nm": "제주시", "bjd_nm": "한림읍", "sale_amt": 9_900_000, "store_cnt": 18},
    ])
    result = metrics.restaurant_pulse(con, "202610", "bjd")
    row = row_for(result, bjd="한림읍")
    assert (row["per_store_yoy_pct"], row["pos_yoy_pct"], row["foreign_food_yoy_pct"]) == (10.0, -1.0, 50.0)
    assert row["dstrct_se"] == "제주시 서부"   # 권역은 카드 데이터에서 붙인다
    assert not {"stores_now", "sale_now"} & set(result.table.frame.columns)


def test_local_owner_weights_spend_by_local_store_share(con, tmp_path):
    stores = []
    for month in ("202510", "202610"):
        stores += [
            {"month": month, "biz_cd": "I56220", "owner_gb": "제주도민 개인", "store_cnt": 30},
            {"month": month, "biz_cd": "I56220", "owner_gb": "법인", "store_cnt": 10},
            {"month": month, "biz_cd": "G47130", "owner_gb": "제주도민 개인", "store_cnt": 1},
            {"month": month, "biz_cd": "G47130", "owner_gb": "법인", "store_cnt": 19},
        ]
    load(con, tmp_path, "1-17", stores)
    load(con, tmp_path, "1-08", [
        {"month": "202510", "user_type": "중국", "biz_cd": "I56220", "use_amt": 2_000_000},
        {"month": "202510", "user_type": "중국", "biz_cd": "G47130", "use_amt": 2_000_000},
        {"month": "202610", "user_type": "중국", "biz_cd": "I56220", "use_amt": 1_000_000},
        {"month": "202610", "user_type": "중국", "biz_cd": "G47130", "use_amt": 3_000_000},
    ])
    result = metrics.local_owner(con, "202610", "bjd", None, "외국인")   # 법정동을 물어도 시 단위로 계산한다
    row = row_for(result, city="제주시")
    assert (row["local_take_pct"], row["change_pp"], row["baseline_pct"]) == (22.5, -17.5, 22.5)
    assert any("추정" in note for note in result.table.notes)


def test_per_visitor_keeps_domestic_arrivals_out_of_foreign(con, tmp_path):
    load(con, tmp_path, "1-08", [
        {"month": "202510", "user_type": "중국", "use_amt": 1_000_000},
        {"month": "202610", "user_type": "중국", "use_amt": 1_000_000},
        {"month": "202510", "user_type": "내국인", "use_amt": 1_000_000},
        {"month": "202610", "user_type": "내국인", "use_amt": 900_000},
    ])
    load(con, tmp_path, "P-02", [
        {"month": "202510", "country": "중국", "segment": "중국", "jeju_arrivals": 1000, "korea_arrivals": ""},
        {"month": "202610", "country": "중국", "segment": "중국", "jeju_arrivals": 1250, "korea_arrivals": ""},
        {"month": "202510", "country": "내국인", "segment": "내국인관광객", "jeju_arrivals": 10000, "korea_arrivals": ""},
        {"month": "202610", "country": "내국인", "segment": "내국인관광객", "jeju_arrivals": 9000, "korea_arrivals": ""},
    ])
    foreign = row_for(metrics.per_visitor(con, "202610", "bjd", None, "외국인"), bjd="한림읍")
    assert (foreign["arrival_yoy_pct"], foreign["per_visitor_yoy_pct"]) == (25.0, -20.0)
    domestic = row_for(metrics.per_visitor(con, "202610", "bjd", None, "내국인관광객"), bjd="한림읍")
    assert (domestic["arrival_yoy_pct"], domestic["per_visitor_yoy_pct"]) == (-10.0, 0.0)


def test_night_share_reads_hour_ranges_too(con, tmp_path):
    load(con, tmp_path, "1-14", [
        {"month": "202510", "user_type": "중국", "time": "18~22시", "use_amt": 300_000},
        {"month": "202510", "user_type": "중국", "time": "07~11시", "use_amt": 700_000},
        {"month": "202610", "user_type": "중국", "time": "22시~07시", "use_amt": 500_000},
        {"month": "202610", "user_type": "중국", "time": "14~18시", "use_amt": 500_000},
    ])
    row = row_for(metrics.night_share(con, "202610", "bjd", None, "중국"), bjd="한림읍")
    assert (row["night_share_pct"], row["change_pp"]) == (50.0, 20.0)


def test_local_owner_refuses_when_no_local_owner_code(con, tmp_path):
    load(con, tmp_path, "1-17", [{"month": m, "biz_cd": "I56220", "owner_gb": "도민개인", "store_cnt": 30} for m in ("202510", "202610")])
    load(con, tmp_path, "1-08", [{"month": m, "user_type": "중국"} for m in ("202510", "202610")])
    with pytest.raises(ValueError, match="제주도민 개인"):
        metrics.local_owner(con, "202610", "city", None, "외국인")


def test_missing_business_code_is_explained(con, tmp_path):
    """데이터에 없는 업종을 고르면 빈 표로 터지지 않고 이유를 말한다 (크루즈 효과에서 면세점을 골랐을 때 KeyError 가 나던 문제)."""
    load(con, tmp_path, "1-08", [{"month": "202510", "biz_cd": "I56220"}, {"month": "202610", "biz_cd": "I56220"}])
    with pytest.raises(ValueError, match="G47130\\(면세점\\)"):
        metrics.sales_yoy(con, "202610", "bjd", "G47130", "외국인")
