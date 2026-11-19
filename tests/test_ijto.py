"""제주관광빅데이터플랫폼 공개 데이터(P-03 소비, P-04 방문객) 정리·적재·지표 연결."""
import csv
import importlib.util
from pathlib import Path

import duckdb
import pytest

from golmok import metrics
from golmok.loader import load_center_csv

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("import_ijto", ROOT / "tools" / "import_ijto.py")
import_ijto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(import_ijto)

# 플랫폼에서 받은 원본과 같은 머리글
SPEND_HEADER = ["기준연월", "관광객구분", "읍면동코드", "읍면동명", "지역명", "업종명", "이용건수", "소비금액(원)"]
VISITOR_HEADER = ["기준연월", "읍면동코드", "시도명", "시군구명", "읍면동명", "내국인 방문객수", "외국인 방문객수", "총 방문객수 (내국인+외국인)"]
DONGS = [("5011060", "연동", "제주시 연동"), ("5011025", "구좌읍", "제주시 구좌읍"), ("5013058", "중문동", "서귀포시 중문동")]


def write_raw(path: Path, header: list[str], rows: list[list], encoding: str = "utf-8-sig") -> Path:
    with open(path, "w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def raw_spend(tmp_path: Path) -> Path:
    rows = []
    for month, scale in (("2025-09", 1), ("2026-09", 2)):
        for code, dong, region in DONGS:
            for tourist, amount in (("도민", 5_000_000), ("내국인 관광객", 3_000_000), ("외국인 관광객", 1_000_000)):
                weight = 3 if dong == "연동" and tourist == "외국인 관광객" else 1
                rows.append([month, tourist, code, dong, region, "음식점업", "120", f"{amount * scale * weight:,}"])
    return write_raw(tmp_path / "제주 지역별 소비.csv", SPEND_HEADER, rows, encoding="cp949")


def raw_visitors(tmp_path: Path) -> Path:
    rows = [["2026-09", "50110" + str(i), "제주특별자치도", region.split()[0], dong, "1,000", "500", "1,500"] for i, (_, dong, region) in enumerate(DONGS)]
    return write_raw(tmp_path / "제주 지역별 방문객.csv", VISITOR_HEADER, rows)


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(import_ijto, "OUT_DIR", tmp_path / "out")
    for raw in (raw_spend(tmp_path), raw_visitors(tmp_path)):
        rows = import_ijto.read_rows(raw)
        kind = import_ijto.kind_of(rows)
        mapping, order = (import_ijto.SPEND_COLUMNS, import_ijto.SPEND_ORDER) if kind == "spend" else (import_ijto.VISITOR_COLUMNS, import_ijto.VISITOR_ORDER)
        import_ijto.write(import_ijto.tidy(rows, mapping, order), order, f"{kind}.csv")
    connection = duckdb.connect()
    assert load_center_csv(connection, tmp_path / "out" / "spend.csv").dataset.id == "P-03"
    assert load_center_csv(connection, tmp_path / "out" / "visitors.csv").dataset.id == "P-04"
    return connection


def test_import_normalizes_month_and_numbers(con):
    row = con.execute("SELECT month, use_amt FROM pub_ijto_spend WHERE emd_nm = '연동' AND tourist_type = '외국인 관광객' AND month = '202609'").fetchone()
    assert row == ("202609", 6_000_000)
    assert con.execute("SELECT sum(foreign_visitors) FROM pub_ijto_visitors").fetchone()[0] == 1500


def test_card_metrics_fall_back_to_public_spend(con):
    assert metrics.card_source(con, ["202609", "202509"]) == metrics.SPEND_PUBLIC
    result = metrics.sales_yoy(con, "202609", level="bjd", segment="외국인")
    assert any("제주관광공사" in s or "관광빅데이터" in s for s in result.sources)
    frame = result.table.frame.set_index("bjd")
    assert frame.loc["연동", "yoy_pct"] == pytest.approx(100.0)


def test_public_spend_has_no_nationality(con):
    with pytest.raises(ValueError, match="국적"):
        metrics.sales_yoy(con, "202609", level="bjd", segment="중국")


def test_conversion_index_by_dong_with_public_visitors(con):
    result = metrics.conversion_index(con, "202609", segment="외국인")
    frame = result.table.frame.set_index("bjd")
    # 방문객은 세 동이 같고 외국인 소비는 연동이 3배 → 연동 3/5 ÷ 1/3 = 1.8
    assert frame.loc["연동", "conversion_index"] == pytest.approx(1.8)
    assert frame.loc["구좌읍", "conversion_index"] == pytest.approx(0.6)
    assert "읍면동" in result.title


def test_place_profile_summarizes_dongs_without_amounts(con):
    profile = metrics.place_profile(con, ["연동"], "202609")
    assert profile["available"] and profile["month"] == "202609"
    assert profile["spend_yoy"] == pytest.approx(100.0)   # 연동 외국인 소비 2배
    assert profile["conversion_index"] == pytest.approx(1.8)
    assert profile["areas"] == [{"dstrct_se": "제주시 중부(서)", "bjd": "연동"}]
    assert all(0 <= point["level"] <= 1 for point in profile["trend"])
    assert not any(key in profile for key in ("amt", "use_amt", "visitors"))
    whole = metrics.place_profile(con, [], "202609")
    assert whole["names"] == ["제주 전체"] and whole["conversion_index"] is None


# ---------- 시간대별 소비(P-06), 국가별 외국인 소비(P-07), 국적별 크루즈 입도객(P-08) ----------
TIME_HEADER = ["기준연월", "관광객구분", "읍면동코드", "읍면동명", "업종명", "카드 매출액(원)", "이용건수", "시간대"]
NATION_HEADER = ["기준연월", "관광객구분", "업종명", "이용건수", "소비금액(원)", "국가"]
CRUISE_HEADER = ["기준연월(YYYYMM)", "국가", "크루즈 입국자수"]


def import_raw(tmp_path, name, header, rows):
    raw = write_raw(tmp_path / name, header, rows)
    rows_ = import_ijto.read_rows(raw)
    kind = import_ijto.kind_of(rows_)
    mapping, order, keys, values, out_name = import_ijto.KINDS[kind]
    merged = import_ijto.merge_parts(import_ijto.tidy(rows_, mapping, order), order[:keys], values)
    import_ijto.write(merged, order, out_name)
    return kind, import_ijto.OUT_DIR / out_name


def test_night_share_from_public_hourly_spend(con, tmp_path):
    rows = []
    for month in ("2025-09", "2026-09"):
        night = "300,000" if month == "2026-09" else "100,000"
        for code, dong, _ in DONGS:
            rows += [[month, "외국인 관광객", code, dong, "음식점업", "300,000", "50", "13"],
                     [month, "외국인 관광객", code, dong, "음식점업", night, "50", "21"],
                     [month, "외국인 관광객", code, dong, "음식점업", "0", "0", "21"]]   # 조각 줄은 더해진다
    kind, path = import_raw(tmp_path, "시간대별 카드 소비 패턴.csv", TIME_HEADER, rows)
    assert kind == "time"
    assert load_center_csv(con, path).dataset.id == "P-06"
    result = metrics.night_share(con, "202609", "bjd", None, "외국인")
    frame = result.table.frame.set_index("bjd")
    assert frame.loc["연동", "night_share_pct"] == pytest.approx(50.0)
    assert frame.loc["연동", "change_pp"] == pytest.approx(25.0)
    assert "시간대별 카드 소비" in result.sources[0]


def test_nationality_from_public_nation_spend_only_island_wide(con, tmp_path):
    rows = [["2025-09", "외국인 관광객", "소매업", "100", "1,000,000", "중국"], ["2026-09", "외국인 관광객", "소매업", "100", "1,500,000", "중국"],
            ["2025-09", "외국인 관광객", "소매업", "100", "500,000", "베트남"], ["2026-09", "외국인 관광객", "소매업", "100", "500,000", "베트남"]]
    kind, path = import_raw(tmp_path, "국가별 외국인 소비.csv", NATION_HEADER, rows)
    assert kind == "nation"
    load_center_csv(con, path)
    whole = metrics.sales_yoy(con, "202609", "all", None, "중국")
    assert whole.table.frame.iloc[0]["yoy_pct"] == pytest.approx(50.0)
    assert "국가별 외국인 소비" in whole.sources[0]
    assert metrics.sales_yoy(con, "202609", "all", None, "동남아").table.frame.iloc[0]["yoy_pct"] == pytest.approx(0.0)
    with pytest.raises(ValueError, match="제주 전체"):
        metrics.sales_yoy(con, "202609", "bjd", None, "중국")
    with pytest.raises(ValueError, match="비중"):
        metrics.spend_share(con, "202609", "all", None, "중국")
    # 외국인 전체 지표는 여전히 동네별 공개 소비로 계산한다
    assert "제주 지역별 소비" in metrics.sales_yoy(con, "202609", "bjd", None, "외국인").sources[0]


def test_cruise_nation_counts_import():
    rows = [["202611", "중국", "100"], ["202611", "중국", "50"], ["202611", "대만", "7"]]
    mapping, order, keys, values, _ = import_ijto.KINDS["cruise"]
    raw = [dict(zip(CRUISE_HEADER, r)) for r in rows]
    assert import_ijto.kind_of(raw) == "cruise"
    merged = import_ijto.merge_parts(import_ijto.tidy(raw, mapping, order), order[:keys], values)
    assert {(r["country"], r["cruise_arrivals"]) for r in merged} == {("중국", "150"), ("대만", "7")}


def test_foreign_share_still_works_on_public_spend(con):
    """'외국인'(국적 묶음 IN 목록)은 국적별 나눔이 아니므로 공개 소비로 비중을 계산한다."""
    frame = metrics.spend_share(con, "202609", "bjd", None, "외국인").table.frame.set_index("bjd")
    assert frame.loc["연동", "share_pct"] > 0


# ---------- Agent 도구: 문제 동네 훑기, 추이, 두 달 비교, 동네 요약, 자율 브리핑 ----------
def toolbox_for(con):
    import threading
    from golmok.tools import Toolbox
    return Toolbox(con, threading.Lock(), lambda: {"가상데이터": False, "데이터": [], "분석 가능한 달": ["202509", "202609"]})


def test_scan_issues_counts_flags_per_dong(con):
    toolbox = toolbox_for(con)
    result = toolbox.execute("scan_issues", {"month": "202609", "segment": "외국인"})
    names = [entry["동네"] for entry in result["동네"]]
    assert "구좌읍" in names and "연동" not in names[:1]   # 연동은 외국인 소비가 3배라 낙수 지수가 높다
    first = result["동네"][0]
    assert first["걸린 지표 수"] == len(first["걸린 지표"]) and first["계산된 지표 수"] >= first["걸린 지표 수"]
    assert all(isinstance(hit["값"], (int, float)) for entry in result["동네"] for hit in entry["걸린 지표"])


def test_trend_compare_and_place_tools(con):
    toolbox = toolbox_for(con)
    trend = toolbox.execute("trend", {"places": ["연동"], "segment": "외국인", "until": "202609", "months": 3})
    assert trend["rows"][-1]["month"] == "202609" and trend["rows"][-1]["spend_yoy_pct"] == pytest.approx(100.0)
    assert not any(key in trend["rows"][-1] for key in ("amt", "users", "use_amt"))
    compare = toolbox.execute("compare_periods", {"metric": "sales_yoy", "month_a": "202609", "month_b": "202609", "level": "bjd",
                                                  "biz_prefix": None, "segment": "외국인", "focus": ["연동"]})
    assert compare["rows"] == [{"지역": "연동", "앞 달": 100.0, "뒤 달": 100.0, "차이": 0.0}]
    place = toolbox.execute("place_summary", {"places": ["연동"], "month": "202609"})
    assert place["conversion_index"] == pytest.approx(1.8) and "trend" not in place


def test_brief_offline_finds_places_and_checks_them(con, monkeypatch):
    from golmok import agent
    monkeypatch.setattr(agent, "make_client", lambda: None)
    toolbox = toolbox_for(con)
    events = list(agent.run_brief(toolbox, "202609"))
    labels = [e["label"] for e in events if e["type"] == "step"]
    assert labels[0].startswith("계획") and any(label.startswith("동네 요약") for label in labels) and any(label.startswith("월별 추이") for label in labels)
    final = events[-1]
    assert final["mode"] == "offline" and "주의 동네" in final["text"] and final["unverified"] == []
