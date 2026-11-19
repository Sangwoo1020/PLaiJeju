"""센터에서 내려받은 CSV 를 식별·검증해 DuckDB 에 적재한다.

센터 데이터는 Agent 가 직접 조회할 수 없고, 이용자가 센터 화면에서 CSV(영문 컬럼)로
내려받아 넣는 구조다. 월 단위로 나눠 받은 파일을 같은 테이블에 이어 붙이며,
같은 파일을 두 번 넣어도 중복 집계되지 않도록 내용 해시로 걸러낸다.
UTF-8 이 아닌 파일(한국어 Windows 엑셀이 저장하는 CP949, UTF-16)은 UTF-8 로 바꿔서 읽는다.
"""
from __future__ import annotations

import codecs
import csv
import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import duckdb

from golmok.schemas import Dataset, Match, get_dataset, identify, is_public

_DUCKDB_TYPES = {"INT": "BIGINT", "BIGINT": "BIGINT", "DOUBLE": "DOUBLE"}


class UnknownDataset(ValueError):
    """CSV 헤더가 명세서의 어떤 데이터셋과도 맞지 않는다."""


class AmbiguousDataset(ValueError):
    """컬럼 구성이 같은 데이터셋이 여러 개라 이용자가 골라야 한다."""

    def __init__(self, candidates: list[Dataset]):
        self.candidates = candidates
        names = ", ".join(f"{d.id} {d.title}" for d in candidates)
        super().__init__(f"컬럼 구성이 같은 데이터셋이 여러 개입니다. 하나를 지정해 주세요: {names}")


class SchemaMismatch(ValueError):
    """명세서와 컬럼이 달라 그대로 적재할 수 없다."""


class UnreadableFile(ValueError):
    """글자 인코딩을 알 수 없어 CSV 로 읽을 수 없다."""


@dataclass(frozen=True)
class LoadResult:
    dataset: Dataset
    table: str
    rows_added: int
    skipped_duplicate: bool


_UTF16_MARKS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
_ENCODINGS = ("utf-8", "cp949")   # cp949 는 EUC-KR 을 포함한다


def _decodes(path: Path, encoding: str) -> bool:
    """파일 전체가 그 인코딩으로 읽히는지. 큰 파일도 메모리에 다 올리지 않고 조금씩 확인한다."""
    decoder = codecs.getincrementaldecoder(encoding)()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def detect_encoding(path: Path) -> str:
    """CSV 의 글자 인코딩: utf-8, cp949, utf-16 가운데 하나. 어느 것도 아니면 UnreadableFile."""
    with open(path, "rb") as handle:
        if handle.read(2) in _UTF16_MARKS:
            return "utf-16"
    for encoding in _ENCODINGS:
        if _decodes(path, encoding):
            return encoding
    raise UnreadableFile("UTF-8 이나 CP949(EUC-KR)로 저장한 CSV 가 아닙니다. 엑셀 파일(xlsx)이라면 'CSV UTF-8' 형식으로 다시 저장해 주세요.")


@contextmanager
def _as_utf8(path: Path) -> Iterator[Path]:
    """UTF-8 파일은 그대로, 아니면 UTF-8 로 옮겨 적은 임시 파일을 준다(쓰고 나면 지운다)."""
    encoding = detect_encoding(path)
    if encoding == "utf-8":
        yield path
        return
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="") as copy:
        with open(path, encoding=encoding, newline="") as source:
            shutil.copyfileobj(source, copy, 1 << 20)
    converted = Path(copy.name)
    try:
        yield converted
    finally:
        converted.unlink(missing_ok=True)


def read_header(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return [name.strip() for name in next(csv.reader(handle), [])]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(header: list[str], dataset_id: str | None) -> Match:
    if dataset_id is not None:
        dataset = get_dataset(dataset_id)
        given = frozenset(header)
        missing = tuple(sorted(dataset.key_columns - given))
        if missing:
            raise SchemaMismatch(f"{dataset.id} {dataset.title} 에 필요한 컬럼이 없습니다: {', '.join(missing)}")
        return Match(dataset, missing, extra=())

    matches = identify(header)
    if not matches:
        raise UnknownDataset("명세서의 어떤 데이터셋과도 컬럼이 맞지 않습니다: " + ", ".join(header))
    if not matches[0].exact:
        best = matches[0]
        raise SchemaMismatch(
            f"가장 비슷한 데이터셋은 {best.dataset.id} {best.dataset.title} 이지만 컬럼이 다릅니다. "
            f"없는 컬럼: {', '.join(best.missing) or '없음'} / 모르는 컬럼: {', '.join(best.extra) or '없음'}"
        )
    if len(matches) > 1:
        raise AmbiguousDataset([m.dataset for m in matches])
    return matches[0]


def _column_type(column) -> str:
    """명세 타입을 DuckDB 타입으로. 숫자 타입인데 예시값에 단위가 붙어 있으면(종량제봉투용량 "50L") 문자열로 둔다."""
    spec_type = column.type
    if spec_type.startswith("DECIMAL"):
        return "DOUBLE"
    if spec_type in _DUCKDB_TYPES and not column.example.replace(".", "").replace("-", "").isdigit():
        return "VARCHAR"
    return _DUCKDB_TYPES.get(spec_type, "VARCHAR")


def load_center_csv(con: duckdb.DuckDBPyConnection, path: str | Path, dataset_id: str | None = None) -> LoadResult:
    """CSV 한 개를 적재한다. dataset_id 를 주지 않으면 헤더로 자동 식별한다."""
    original = Path(path)
    with _as_utf8(original) as readable:   # 같은 내용이면 인코딩이 달라도 같은 파일로 본다(해시는 UTF-8 기준)
        return _load(con, readable, original.name, dataset_id)


def _load(con: duckdb.DuckDBPyConnection, path: Path, file_name: str, dataset_id: str | None) -> LoadResult:
    header = read_header(path)
    dataset = _resolve(header, dataset_id).dataset
    table = dataset.table or "center_" + dataset.id.replace("-", "_")

    present = [c for c in dataset.columns if c.name in header]
    definitions = ", ".join(f'"{c.name}" {_column_type(c)}' for c in present)
    con.execute("CREATE TABLE IF NOT EXISTS _loaded_files (file_hash VARCHAR PRIMARY KEY, table_name VARCHAR, file_name VARCHAR)")
    con.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({definitions})')

    file_hash = _file_hash(path)
    if con.execute("SELECT 1 FROM _loaded_files WHERE file_hash = ?", [file_hash]).fetchone():
        return LoadResult(dataset, table, rows_added=0, skipped_duplicate=True)

    # 공개 데이터(크루즈 입항 등)는 달 단위로 갱신하므로, 같은 달이 이미 있으면 새 파일 값으로 바꾼다.
    if is_public(dataset.id) and "month" in header:
        months = [row[0] for row in con.execute("SELECT DISTINCT month FROM read_csv(?, header = true, all_varchar = true)", [str(path)]).fetchall()]
        if months:
            con.execute(f'DELETE FROM "{table}" WHERE month IN ({", ".join("?" for _ in months)})', months)

    # 코드 값(법정동코드 등)의 앞자리 0 을 지키기 위해 전부 문자열로 읽은 뒤 명세 타입으로 변환한다.
    names = ", ".join(f'"{c.name}"' for c in present)
    casts = ", ".join(f'TRY_CAST("{c.name}" AS {_column_type(c)})' for c in present)
    before = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    con.execute(
        f'INSERT INTO "{table}" ({names}) SELECT {casts} FROM read_csv(?, header = true, all_varchar = true)',
        [str(path)],
    )
    after = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    con.execute("INSERT INTO _loaded_files VALUES (?, ?, ?)", [file_hash, table, file_name])
    return LoadResult(dataset, table, rows_added=after - before, skipped_duplicate=False)
