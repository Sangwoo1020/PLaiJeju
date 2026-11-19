import csv

from importlib import resources

import duckdb
import pytest

from golmok.loader import AmbiguousDataset, SchemaMismatch, UnknownDataset, load_center_csv
from golmok.schemas import get_dataset, identify, load_registry

CARD_EMD_V02 = "1-08"
CARD_ROW = {
    "base_year_month": "202605", "pt_year": "2026", "pt_month": "5", "dstrct_se": "제주시 서부",
    "city": "제주시", "area_type": "읍면", "bjd": "한림읍", "biz_cd": "I56220", "biz_nm": "비알콜 음료점업",
    "user_type": "내국인", "tour_type": "개별", "age": "30", "sex": "여성",
    "user_cnt": "16", "use_cnt": "17", "use_amt": "365160",
}


def write_csv(path, rows, columns=None, bom=False):
    columns = columns or list(rows[0])
    with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def con():
    return duckdb.connect(":memory:")


def test_registry_covers_spec():
    registry = load_registry()
    assert len(registry) == 79   # 센터 71종 + 공개 데이터 형식 8종
    assert registry["P-01"].exportable and registry["P-01"].table == "pub_cruise_monthly"
    card = registry[CARD_EMD_V02]
    assert card.table == "tb_card_usage_emd_new"
    assert not card.exportable
    assert card.column_names[-3:] == ("user_cnt", "use_cnt", "use_amt")


def test_identify_tells_v01_from_v02():
    v02 = get_dataset(CARD_EMD_V02).column_names
    v01 = get_dataset("1-07").column_names
    assert [m.dataset.id for m in identify(v02)] == ["1-08"]
    assert [m.dataset.id for m in identify(v01)] == ["1-07"]


def test_load_detects_dataset_and_casts_types(con, tmp_path):
    path = write_csv(tmp_path / "card.csv", [CARD_ROW], bom=True)
    result = load_center_csv(con, path)
    assert result.dataset.id == CARD_EMD_V02
    assert result.rows_added == 1
    amount, code = con.execute(f'SELECT use_amt, biz_cd FROM "{result.table}"').fetchone()
    assert amount == 365160 and isinstance(amount, int)
    assert code == "I56220"


def test_load_accepts_csv_without_partition_columns(con, tmp_path):
    columns = [c for c in CARD_ROW if c not in ("pt_year", "pt_month")]
    result = load_center_csv(con, write_csv(tmp_path / "card.csv", [CARD_ROW], columns))
    assert result.dataset.id == CARD_EMD_V02
    assert result.rows_added == 1


def test_monthly_files_append_and_same_file_is_not_double_counted(con, tmp_path):
    may = write_csv(tmp_path / "may.csv", [CARD_ROW])
    june = write_csv(tmp_path / "june.csv", [{**CARD_ROW, "base_year_month": "202606", "pt_month": "6"}])
    load_center_csv(con, may)
    load_center_csv(con, june)
    again = load_center_csv(con, may)
    assert again.skipped_duplicate and again.rows_added == 0
    assert con.execute('SELECT count(*) FROM "tb_card_usage_emd_new"').fetchone()[0] == 2


def test_unknown_columns_are_rejected(con, tmp_path):
    path = write_csv(tmp_path / "x.csv", [{"foo": "1", "bar": "2"}])
    with pytest.raises(UnknownDataset):
        load_center_csv(con, path)


def test_near_match_reports_missing_columns(con, tmp_path):
    columns = [c for c in CARD_ROW if c != "use_amt"]
    path = write_csv(tmp_path / "card.csv", [CARD_ROW], columns)
    with pytest.raises(SchemaMismatch, match="use_amt"):
        load_center_csv(con, path)


def test_identical_schemas_require_explicit_choice(con, tmp_path):
    # 1-02(내국인)와 1-05(중국인) 관광업종 카드 금액은 컬럼 구성이 같다.
    columns = get_dataset("1-02").column_names
    path = write_csv(tmp_path / "tour.csv", [{c: "1" for c in columns}])
    with pytest.raises(AmbiguousDataset) as error:
        load_center_csv(con, path)
    assert {d.id for d in error.value.candidates} == {"1-02", "1-05"}
    assert load_center_csv(con, path, dataset_id="1-05").dataset.id == "1-05"


def test_bundled_foreign_arrivals_match_published_total():
    """프로그램에 든 입도통계(공개)의 2025년 외국인 합계가 발표치(2,242,187명)와 맞는다."""
    con = duckdb.connect(":memory:")
    path = resources.files("golmok").joinpath("reference/foreign_arrivals_public.csv")
    assert load_center_csv(con, str(path)).dataset.id == "P-02"
    total = con.execute("SELECT sum(jeju_arrivals) FROM pub_foreign_arrivals WHERE month LIKE '2025%' AND segment <> '내국인관광객'").fetchone()[0]
    assert total == 2_242_187


def test_real_center_header_without_partition_columns(tmp_path):
    """대회 게시판 답변(2026-11-09)으로 공개된 1-08 실제 머리글: 명세서의 pt_year·pt_month 가 없고 글자 값에 따옴표가 붙는다."""
    import duckdb
    from golmok import metrics
    from golmok.loader import load_center_csv
    path = tmp_path / "card.csv"
    path.write_text(
        "base_year_month,dstrct_se,city,area_type,bjd,biz_cd,biz_nm,user_type,tour_type,age,sex,user_cnt,use_cnt,use_amt\n"
        '202607,"서귀포시 서부","서귀포시","읍면","대정읍","I56111","한식 일반 음식점업","중국","개별","30","F",25,40,1200000\n'
        '202507,"서귀포시 서부","서귀포시","읍면","대정읍","I56111","한식 일반 음식점업","중국","개별","30","F",20,30,900000\n', encoding="utf-8")
    con = duckdb.connect(":memory:")
    result = load_center_csv(con, path)
    assert result.dataset.id == "1-08" and result.rows_added == 2
    frame = metrics.sales_yoy(con, "202607", "bjd", None, "중국").table.frame
    assert frame.iloc[0]["bjd"] == "대정읍" and round(frame.iloc[0]["yoy_pct"], 1) == 33.3


@pytest.mark.parametrize("encoding", ["cp949", "utf-16"])
def test_korean_windows_encodings_load_like_utf8(con, tmp_path, encoding):
    """한국어 Windows 엑셀이 저장하는 CP949, UTF-16 CSV 도 UTF-8 과 똑같이 적재된다."""
    utf8 = write_csv(tmp_path / "card_utf8.csv", [CARD_ROW])
    other = tmp_path / f"card_{encoding}.csv"
    other.write_bytes(utf8.read_bytes().decode("utf-8").encode(encoding))

    result = load_center_csv(con, other)
    assert result.dataset.id == CARD_EMD_V02 and result.rows_added == 1
    assert con.execute(f'SELECT bjd, biz_nm, use_amt FROM "{result.table}"').fetchall() == [("한림읍", "비알콜 음료점업", 365160)]
    # 내용이 같으면 인코딩이 달라도 같은 파일로 보고 두 번 세지 않는다
    assert load_center_csv(con, utf8).skipped_duplicate


def test_file_that_is_not_text_is_refused_with_a_reason(con, tmp_path):
    from golmok.loader import UnreadableFile

    path = tmp_path / "book.csv"
    path.write_bytes(b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00\xff\xfe\x80\x81\xff\xff")   # xlsx 를 이름만 csv 로 바꾼 경우
    with pytest.raises(UnreadableFile, match="CP949"):
        load_center_csv(con, path)
