import pandas as pd
import pytest

from golmok.guard import SUPPRESSED_COLUMN, GuardedTable, citation, llm_payload, release_derived, release_simple


def cells(rows):
    return pd.DataFrame(rows, columns=["city", "dstrct_se", "bjd", "biz_nm", "user_cnt", "use_amt"])


SAFE = cells([
    ("제주시", "제주시 서부", "한림읍", "한식", 120, 3_000_000),
    ("제주시", "제주시 서부", "애월읍", "한식", 300, 9_000_000),
])

# 한경면 한식은 이용자 4명 → 법정동 단위로는 반출 불가, 권역으로 올리면 안전
SMALL_IN_BJD = cells([
    ("제주시", "제주시 서부", "한림읍", "한식", 120, 3_000_000),
    ("제주시", "제주시 서부", "한경면", "한식", 4, 90_000),
    ("제주시", "제주시 동부", "구좌읍", "한식", 80, 2_000_000),
])

LEVELS = [["bjd", "biz_nm"], ["dstrct_se", "biz_nm"], ["city", "biz_nm"]]


def test_safe_table_passes_unchanged():
    result = release_simple(SAFE, LEVELS, measures=["use_amt"], support=["user_cnt"])
    assert result.treatment == "none"
    assert result.frame["use_amt"].tolist() == [9_000_000, 3_000_000]  # 애월읍, 한림읍


def test_small_cell_is_rolled_up_to_the_next_unit():
    result = release_simple(SMALL_IN_BJD, LEVELS, measures=["use_amt"], support=["user_cnt"])
    assert result.treatment == "rollup"
    assert "bjd" not in result.frame.columns
    west = result.frame.set_index("dstrct_se").loc["제주시 서부"]
    assert west["user_cnt"] == 124 and west["use_amt"] == 3_090_000
    assert (result.frame["user_cnt"] >= 10).all()


def test_amount_cell_backed_by_few_users_counts_as_small():
    # 금액은 크지만 이용자가 3명뿐인 칸
    frame = cells([("제주시", "제주시 서부", "한림읍", "골프장", 3, 5_000_000)])
    result = release_simple(frame, [["bjd", "biz_nm"]], measures=["use_amt"], support=["user_cnt"])
    assert result.treatment == "suppress"
    assert result.frame["use_amt"].isna().all()


def test_suppression_hides_a_second_cell_so_totals_cannot_reveal_it():
    frame = cells([
        ("제주시", "제주시 서부", "한림읍", "한식", 3, 60_000),
        ("제주시", "제주시 서부", "애월읍", "한식", 50, 1_000_000),
        ("제주시", "제주시 서부", "한경면", "한식", 80, 1_600_000),
    ])
    result = release_simple(frame, [["bjd", "biz_nm"]], measures=["use_amt"], support=["user_cnt"])
    hidden = result.frame.set_index("bjd")[SUPPRESSED_COLUMN]
    assert result.treatment == "suppress"
    assert hidden["한림읍"] and hidden["애월읍"] and not hidden["한경면"]
    assert result.frame.set_index("bjd").loc["한경면", "use_amt"] == 1_600_000


def test_derived_values_drop_support_and_withhold_thin_rows():
    frame = pd.DataFrame({
        "bjd": ["한림읍", "한경면"],
        "yoy_pct": [-12.5, 40.0],
        "users_now": [105, 7],
        "users_before": [120, 5],
    })
    result = release_derived(frame, values=["yoy_pct"], support=["users_now", "users_before"])
    assert result.kind == "derived"
    assert list(result.frame.columns) == ["bjd", "yoy_pct", SUPPRESSED_COLUMN]
    assert result.frame["yoy_pct"].tolist()[0] == -12.5
    assert pd.isna(result.frame["yoy_pct"].tolist()[1])


def test_llm_payload_rejects_unguarded_data():
    with pytest.raises(TypeError, match="반출 가드"):
        llm_payload({"원본": SAFE})
    guarded = release_simple(SAFE, LEVELS, measures=["use_amt"], support=["user_cnt"])
    payload = llm_payload({"매출": guarded})
    assert payload["매출"]["집계유형"] == "단순집계"
    assert len(payload["매출"]["rows"]) == 2


def test_guarded_table_cannot_be_forged():
    with pytest.raises(TypeError):
        GuardedTable(SAFE, "simple", "none")


def test_citation_format():
    assert citation("카드 이용 현황(읍면동)_v02", "2025.05~2026.10") == "제주빅데이터센터, 카드 이용 현황(읍면동)_v02(2025.05~2026.10)"
