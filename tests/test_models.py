"""분석 알고리즘 3종: 답을 아는 작은 가상 패널로 확인한다."""
import math

import duckdb
import numpy as np
import pandas as pd
import pytest

from golmok import models

DONGS = ["연동", "노형동", "건입동", "중문동", "애월읍", "성산읍"]


def months(start: str, count: int) -> list[str]:
    year, month = int(start[:4]), int(start[4:])
    out = []
    for _ in range(count):
        out.append(f"{year}{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def load(con, spend_rows: list[dict], visitor_rows: list[dict]) -> None:
    spend = pd.DataFrame(spend_rows)
    spend = spend.assign(emd_cd="0", region="", **({} if "industry" in spend else {"industry": "음식점업"}))
    con.register("spend_df", spend)
    con.execute("CREATE TABLE pub_ijto_spend AS SELECT month, tourist_type, emd_cd, emd_nm, region, industry, CAST(use_cnt AS BIGINT) AS use_cnt, CAST(use_amt AS DOUBLE) AS use_amt FROM spend_df")
    visitors = pd.DataFrame(visitor_rows).assign(emd_cd="0", city="", domestic_visitors=1000)
    visitors["total_visitors"] = visitors["domestic_visitors"] + visitors["foreign_visitors"]
    con.register("visitor_df", visitors)
    con.execute("CREATE TABLE pub_ijto_visitors AS SELECT month, emd_cd, city, emd_nm, domestic_visitors, foreign_visitors, total_visitors FROM visitor_df")


def wiggle(i: int, j: int) -> float:
    """결정적인 작은 흔들림 (난수 대신)"""
    return 0.03 * math.sin(1.7 * i + 2.3 * j)


def test_expected_gap_finds_the_dong_that_spent_less_than_expected():
    con = duckdb.connect()
    span = months("202209", 49)   # 마지막 달(202609)이 기준 월
    spend, visitors = [], []
    for i, month in enumerate(span):
        season = 0.3 * math.sin(int(month[4:]) / 12 * 2 * math.pi)
        for j, dong in enumerate(DONGS):
            v = 1000 * (j + 1) * math.exp(0.01 * i + 0.2 * math.cos(i + j))
            amt = math.exp(5 + j * 0.4 + season + 0.9 * math.log(v) + 0.02 * i + wiggle(i, j))
            if month == span[-1] and dong == "건입동":
                amt *= 0.5
            spend.append({"month": month, "tourist_type": "외국인 관광객", "emd_nm": dong, "use_cnt": 500, "use_amt": amt})
            visitors.append({"month": month, "emd_nm": dong, "foreign_visitors": v})
    load(con, spend, visitors)
    frame = models.expected_gap(con, span[-1]).table.frame.set_index("bjd")
    assert frame.loc["건입동", "gap_pct"] == pytest.approx(-50, abs=6)
    assert frame.loc["건입동", "signal"] == "뚜렷함"
    others = frame.drop(index="건입동")["gap_pct"]
    assert others.abs().max() < 8
    assert not {"amt", "use_amt", "users", "visitors"} & set(frame.columns)


def test_cruise_adjusted_removes_island_wide_trend():
    con = duckdb.connect()
    span = months("202303", 16)
    spend, visitors = [], []
    china = [100 if i % 2 else 0 for i in range(len(span))]   # 많은 달·적은 달이 번갈아
    for i, month in enumerate(span):
        for j, dong in enumerate(DONGS):
            amt = math.exp(10 + 0.08 * i + 0.1 * j + wiggle(i, j))   # 섬 전체가 함께 늘어나는 회복세
            if dong == "건입동" and china[i]:
                amt *= 1.5
            spend.append({"month": month, "tourist_type": "외국인 관광객", "emd_nm": dong, "use_cnt": 500, "use_amt": amt})
            visitors.append({"month": month, "emd_nm": dong, "foreign_visitors": 1000})
    load(con, spend, visitors)
    con.execute("CREATE TABLE pub_cruise_monthly AS SELECT * FROM (VALUES " + ", ".join(f"('{m}', 1, {c}, 1, {c}, 1, 0)" for m, c in zip(span, china))
                + ") t(month, calls, passengers, china_calls, china_passengers, jeju_port_calls, gangjeong_calls)")
    result = models.cruise_adjusted(con, span[-1])
    frame = result.table.frame.set_index("bjd")
    # 건입동만 크루즈 달에 1.5배 → 섬 평균을 뺀 추가 효과는 exp(log1.5 × 5/6) − 1 ≈ 40%
    assert frame.loc["건입동", "adj_lift_pct"] == pytest.approx(40.1, abs=4)
    assert frame.drop(index="건입동")["adj_lift_pct"].between(-12, 0).all()
    # 보정 전 단순 비교에는 회복세가 섞여 다른 동네도 늘어난 것처럼 보인다
    assert frame.drop(index="건입동")["naive_lift_pct"].min() > 0


def test_dong_types_names_each_group_by_its_strongest_trait():
    con = duckdb.connect()
    span = months("202410", 24)
    profile = {   # 동네 → (업종별 외국인 소비 구성, 외국인 방문객)
        "연동": ({"소매업": 80, "숙박업": 5, "음식점업": 15}, 100), "노형동": ({"소매업": 75, "숙박업": 10, "음식점업": 15}, 100),
        "중문동": ({"소매업": 5, "숙박업": 80, "음식점업": 15}, 100), "건입동": ({"소매업": 10, "숙박업": 75, "음식점업": 15}, 100),
        "애월읍": ({"소매업": 10, "숙박업": 5, "음식점업": 85}, 100), "성산읍": ({"소매업": 10, "숙박업": 10, "음식점업": 80}, 100),
        "추자면": ({"소매업": 30, "숙박업": 20, "음식점업": 50}, 3000), "한경면": ({"소매업": 35, "숙박업": 15, "음식점업": 50}, 3000),
    }
    spend, visitors = [], []
    for month in span:
        for dong, (mix, v) in profile.items():
            for industry, share in mix.items():
                spend.append({"month": month, "tourist_type": "외국인 관광객", "emd_nm": dong, "industry": industry, "use_cnt": 100, "use_amt": share * 1000})
            spend.append({"month": month, "tourist_type": "도민", "emd_nm": dong, "industry": "음식점업", "use_cnt": 100, "use_amt": 400000})
            visitors.append({"month": month, "emd_nm": dong, "foreign_visitors": v})
    load(con, spend, visitors)
    frame = models.dong_types(con, span[-1]).table.frame.set_index("bjd")
    assert frame.loc[["연동", "노형동"], "type"].eq("쇼핑 소비형").all()
    assert frame.loc[["중문동", "건입동"], "type"].eq("숙박·리조트형").all()
    assert frame.loc[["애월읍", "성산읍"], "type"].eq("음식·카페형").all()
    assert frame.loc[["추자면", "한경면"], "type"].eq("사람만 오는형").all()
    again = models.dong_types(con, span[-1]).table.frame.set_index("bjd")["type"]
    assert again.equals(frame["type"])   # 난수가 없어 매번 같다


def test_models_refuse_nationality_segments():
    con = duckdb.connect()
    with pytest.raises(ValueError, match="외국인"):
        models.expected_gap(con, "202609", segment="중국")
