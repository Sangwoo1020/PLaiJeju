import io
import threading
import zipfile

import duckdb
import pytest

from golmok import report
from golmok.tools import Toolbox
from test_agent import METRIC, describe
from test_metrics import load


@pytest.fixture
def data(tmp_path):
    con = duckdb.connect(":memory:")
    load(con, tmp_path, "1-08", [
        {"month": "202510", "use_amt": 1_000_000},
        {"month": "202610", "use_amt": 800_000},
        {"month": "202510", "user_type": "제주도민", "use_amt": 500_000},
        {"month": "202610", "user_type": "제주도민", "use_amt": 500_000},
        {"month": "202510", "bjd": "구좌읍", "dstrct_se": "제주시 동부", "use_amt": 1_000_000},
        {"month": "202610", "bjd": "구좌읍", "dstrct_se": "제주시 동부", "use_amt": 1_100_000},
        {"month": "202510", "bjd": "한경면", "user_cnt": 5, "use_amt": 100_000},
        {"month": "202610", "bjd": "한경면", "user_cnt": 6, "use_amt": 300_000},
    ])
    toolbox = Toolbox(con, threading.Lock(), describe)
    toolbox.execute("compute_metric", METRIC)
    toolbox.execute("compute_metric", {**METRIC, "metric": "spend_share", "segment": "내국인관광객"})
    return report.ReportData("<한림읍> 어때?", "한림읍은 줄었습니다.\n- 근거: 13.3%", "claude", True, ["99.9"], sections=toolbox.sections)


def open_zip(data):
    return zipfile.ZipFile(io.BytesIO(report.build_zip(data)))


def test_zip_has_pdf_pngs_and_release_note_but_no_table_files(data):
    with open_zip(data) as archive:
        names = archive.namelist()
        assert "진단보고서.pdf" in names and "반출신청_참고.txt" in names
        assert archive.read("진단보고서.pdf").startswith(b"%PDF")
        charts = [name for name in names if name.endswith(".png")]
        assert len(charts) == 2 and archive.read(charts[0]).startswith(b"\x89PNG")
        assert not any(name.endswith((".csv", ".xlsx")) for name in names)


def test_release_note_names_sources_and_flags_mock(data):
    with open_zip(data) as archive:
        note = archive.read("반출신청_참고.txt").decode("utf-8-sig")
    assert "제주빅데이터센터, 카드 이용 현황(읍면동)_v02(2025.10·2026.10)" in note
    assert "반출 신청 대상이 아닙니다" in note and "<한림읍> 어때?" in note


def test_support_columns_cannot_reach_the_report(data):
    data.sections[0].rows[0]["use_amt"] = 123456
    with pytest.raises(ValueError, match="use_amt"):
        report.build_zip(data)


def test_withheld_rows_are_named_not_charted(data):
    section = data.sections[0]
    assert "한경면" in report._withheld(section)
    assert "한경면" not in [report.place(row) for row in report._shown(section)]


def test_focused_chart_keeps_other_regions_for_comparison(data):
    section = data.sections[0]
    section.context, section.rows = section.rows, [row for row in section.rows if row.get("bjd") == "구좌읍"]
    assert report.chart_png(section, mock=False).startswith(b"\x89PNG")
    assert [report.place(row) for row in report._shown(section, section.context)] == ["한림읍", "구좌읍"]


def test_context_rows_are_checked_too(data):
    section = data.sections[0]
    section.context = [{**section.rows[0], "users_now": 3}]
    with pytest.raises(ValueError, match="users_now"):
        report.build_zip(data)


def test_minus_sign_is_replaced_for_pdf_fonts(data):
    paragraph = report._text("(가 − 1)", report._styles()["body"])
    assert "−" not in paragraph.text
