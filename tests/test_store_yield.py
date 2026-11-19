"""가게당 관광객 소비(store_yield): 소상공인 상가 매장 수(P-05)를 분모로."""
import csv

import duckdb
import pytest

from golmok import metrics
from golmok.guard import SUPPRESSED_COLUMN
from golmok.loader import load_center_csv
from test_ijto import con as ijto_con  # noqa: F401  관광공사 공개 소비가 들어 있는 연결
from test_metrics import EAST, load

STORE_HEADER = ["snapshot", "city", "emd_cd", "emd_nm", "bjd_nm", "category", "ksic_cd", "stores"]


def load_stores(con, tmp_path, rows):
    path = tmp_path / f"stores_{len(list(tmp_path.iterdir()))}.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(STORE_HEADER)
        writer.writerows(rows)
    assert load_center_csv(con, path).dataset.id == "P-05"


@pytest.fixture
def con():
    return duckdb.connect(":memory:")


def test_store_yield_compares_spend_share_with_store_share(con, tmp_path):
    # 소비는 한림읍 3 : 구좌읍 1, 가게는 같다 → 한림 1.5, 구좌 0.5
    load(con, tmp_path, "1-08", [
        {"month": "202610", "biz_cd": "I56220", "use_amt": 3_000_000},
        {"month": "202610", "biz_cd": "I56220", "use_amt": 1_000_000, **EAST},
        {"month": "202610", "biz_cd": "M71100", "use_amt": 50_000_000},   # 관광 업종이 아니라 빠진다
    ])
    load_stores(con, tmp_path, [
        ["202609", "제주시", "50110250", "한림읍", "한림읍", "음식", "I56220", 40],
        ["202609", "제주시", "50110256", "구좌읍", "구좌읍", "음식", "I56111", 40],
        ["202609", "제주시", "50110250", "한림읍", "한림읍", "과학·기술", "M71100", 500],
    ])
    result = metrics.store_yield(con, "202610", "bjd", None, "내국인관광객")
    frame = result.table.frame.set_index("bjd")
    assert frame.loc["한림읍", "yield_index"] == pytest.approx(1.5)
    assert frame.loc["구좌읍", "yield_index"] == pytest.approx(0.5)
    assert frame.loc["한림읍", "store_share_pct"] == pytest.approx(50.0)
    assert not {"amt", "stores", "users"} & set(result.table.frame.columns)   # 금액·매장 수는 내보내지 않는다
    assert result.sources[-1] == "소상공인시장진흥공단(공공데이터포털), 제주 상가 매장 수(읍면동·업종)(2026.09)"


def test_store_yield_hides_dongs_with_few_stores(con, tmp_path):
    load(con, tmp_path, "1-08", [{"month": "202610", "biz_cd": "I56220"}, {"month": "202610", "biz_cd": "I56220", **EAST}])
    load_stores(con, tmp_path, [
        ["202609", "제주시", "50110250", "한림읍", "한림읍", "음식", "I56220", 40],
        ["202609", "제주시", "50110256", "구좌읍", "구좌읍", "음식", "I56220", 3],
    ])
    frame = metrics.store_yield(con, "202610", "bjd", "I56", "내국인관광객").table.frame.set_index("bjd")
    assert frame.loc["구좌읍", SUPPRESSED_COLUMN]


def test_store_yield_asks_for_store_data(con, tmp_path):
    load(con, tmp_path, "1-08", [{"month": "202610"}])
    with pytest.raises(metrics.MissingData, match="P-05"):
        metrics.store_yield(con, "202610")


def test_store_yield_on_public_spend_joins_by_admin_dong(ijto_con, tmp_path):  # noqa: F811
    load_stores(ijto_con, tmp_path, [
        ["202609", "제주시", "50110630", "연동", "연동", "음식", "I56111", 100],
        ["202609", "제주시", "50110256", "구좌읍", "구좌읍", "음식", "I56111", 100],
        ["202609", "서귀포시", "50130580", "중문동", "대포동", "음식", "I56111", 100],
    ])
    result = metrics.store_yield(ijto_con, "202609", "bjd", None, "외국인")
    frame = result.table.frame.set_index("bjd")
    # 외국인 소비는 연동이 다른 두 동의 3배, 가게는 같다 → 연동 3/5 ÷ 1/3 = 1.8
    assert frame.loc["연동", "yield_index"] == pytest.approx(1.8)
    assert "중문동" in frame.index   # 행정동 이름으로 붙는다
    with pytest.raises(ValueError, match="업종"):
        metrics.store_yield(ijto_con, "202609", "bjd", "G47130", "외국인")
