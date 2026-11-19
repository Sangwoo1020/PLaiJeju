"""제주빅데이터센터 데이터 명세(스키마 레지스트리) 조회와 CSV 식별.

레지스트리는 tools/build_schema_registry.py 가 명세서 xlsx 에서 생성한 center_schemas.json(센터 71종)과,
센터 밖 공개 데이터의 형식을 적은 public_schemas.json(P-로 시작하는 번호)을 합친 것이다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

# 센터 시스템의 SQL 검색용 파티션 컬럼. 내려받은 CSV 에 없을 수도 있어 식별에서 제외한다.
PARTITION_COLUMNS = frozenset({"pt_year", "pt_month"})
# 이 비율 이상 컬럼이 일치하면 "유사 데이터셋"으로 안내한다.
PARTIAL_MATCH_THRESHOLD = 0.8


@dataclass(frozen=True)
class Column:
    name: str
    ko: str
    type: str
    example: str
    note: str


@dataclass(frozen=True)
class Reference:
    """명세서의 [참고N] 코드표 (법정동 목록, 업종코드표 등)."""

    title: str
    fields: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Dataset:
    id: str
    title: str
    category: str
    table: str
    description: str
    period: str
    exportable: bool
    provider: str
    columns: tuple[Column, ...]
    references: tuple[Reference, ...] = ()

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def reference(self, keyword: str) -> Reference:
        """제목에 keyword 가 들어간 코드표를 돌려준다."""
        for ref in self.references:
            if keyword in ref.title:
                return ref
        raise KeyError(f"{self.id} {self.title} 에 '{keyword}' 코드표가 없습니다")

    @property
    def key_columns(self) -> frozenset[str]:
        return frozenset(self.column_names) - PARTITION_COLUMNS


@dataclass(frozen=True)
class Match:
    dataset: Dataset
    missing: tuple[str, ...]  # 명세에는 있는데 CSV 에 없는 컬럼
    extra: tuple[str, ...]  # CSV 에만 있는 컬럼

    @property
    def exact(self) -> bool:
        return not self.missing and not self.extra


REGISTRY_FILES = ("center_schemas.json", "public_schemas.json")


def is_public(dataset_id: str) -> bool:
    """센터 밖 공개 데이터(반출 제한 없음)인지."""
    return dataset_id.startswith("P-")


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Dataset]:
    registry = {}
    items = [item for name in REGISTRY_FILES for item in json.loads(resources.files("golmok").joinpath(name).read_text(encoding="utf-8"))["schemas"]]
    for item in items:
        columns = tuple(Column(**c) for c in item["columns"])
        references = tuple(
            Reference(r["title"], tuple(r["fields"]), tuple(tuple(row) for row in r["rows"]))
            for r in item.get("references", [])
        )
        registry[item["id"]] = Dataset(**{**item, "columns": columns, "references": references})
    return registry


def get_dataset(dataset_id: str) -> Dataset:
    try:
        return load_registry()[dataset_id]
    except KeyError:
        raise KeyError(f"명세서에 없는 데이터셋 번호입니다: {dataset_id}") from None


def identify(csv_columns: list[str] | tuple[str, ...]) -> list[Match]:
    """CSV 헤더로 어떤 센터 데이터셋인지 찾는다.

    정확히 일치하는 데이터셋이 있으면 그것만(컬럼 구성이 같은 데이터셋이 여러 개면 모두) 돌려준다.
    없으면 컬럼이 80% 이상 겹치는 후보를 일치도 순으로 돌려준다. 후보가 없으면 빈 목록.
    """
    given = frozenset(c.strip() for c in csv_columns) - PARTITION_COLUMNS
    exact, partial = [], []
    for dataset in load_registry().values():
        expected = dataset.key_columns
        missing = tuple(sorted(expected - given))
        extra = tuple(sorted(given - expected))
        match = Match(dataset, missing, extra)
        if match.exact:
            exact.append(match)
            continue
        score = len(expected & given) / len(expected | given)
        if score >= PARTIAL_MATCH_THRESHOLD:
            partial.append((score, match))
    if exact:
        return exact
    return [m for _, m in sorted(partial, key=lambda pair: -pair[0])]
