"""반출 가드: 제주빅데이터센터 데이터 반출 정책(2026.09.01 개정)을 코드로 강제한다.

반출 불가 데이터(카드·포스·유동인구·기업)에 적용되는 규칙:
- 원본·발췌는 반출 불가.
- 단순집계(합계·평균 등)는 10 미만 수치가 있으면 반출 불가.
  처리 방법: 범주처리(상위 단위로 재집계) / 삭제처리 / 비식별처리.
- 합계 등에서 빼서 10 미만 값을 계산할 수 있으면(역산) 반출 제한.
- 응용집계(증감률·비율·순위·지수 등)는 수치를 포함해 반출 가능.

우리 해석(정책보다 보수적):
- 금액처럼 값 자체는 크더라도, 그 칸을 만든 이용자수·이용건수가 10 미만이면 작은 칸으로 본다.
- 응용집계도 근거 표본이 10 미만이면 내보내지 않고, 근거가 된 단순집계는 결과에서 뺀다.

LLM 으로 나가는 값과 보고서에 실리는 값은 모두 GuardedTable 이어야 하며,
GuardedTable 은 이 모듈의 release_* 함수로만 만들 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import pandas as pd

SMALL_CELL_THRESHOLD = 10
SUPPRESSED_COLUMN = "비공개"

_ISSUED_BY_GUARD = object()


@dataclass(frozen=True)
class GuardedTable:
    """반출 기준을 통과한 표."""

    frame: pd.DataFrame
    kind: str  # "simple"(단순집계) | "derived"(응용집계)
    treatment: str  # "none" | "rollup"(범주처리) | "suppress"(삭제처리)
    notes: tuple[str, ...] = ()
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._token is not _ISSUED_BY_GUARD:
            raise TypeError("GuardedTable 은 golmok.guard 의 release_* 함수로만 만들 수 있습니다")


def _small(frame: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    """columns 중 하나라도 10 미만인 행."""
    return (frame[list(columns)] < SMALL_CELL_THRESHOLD).any(axis=1)


def _aggregate(frame: pd.DataFrame, dims: Sequence[str], columns: Sequence[str]) -> pd.DataFrame:
    if not dims:
        return frame[list(columns)].sum().to_frame().T
    return frame.groupby(list(dims), as_index=False, dropna=False)[list(columns)].sum()


def _suppress(table: pd.DataFrame, dims: Sequence[str], columns: Sequence[str]) -> tuple[pd.DataFrame, int, int]:
    """작은 칸을 지우고, 역산을 막기 위해 같은 줄의 다음으로 작은 칸도 지운다.

    어떤 차원 하나를 뺀 나머지가 같은 칸들(표의 한 줄)에서 지워진 칸이 정확히 하나면
    그 줄의 합계로 값을 되살릴 수 있다. 그래서 줄마다 지워진 칸이 0개 또는 2개 이상이 되게 한다.
    """
    hidden = _small(table, columns)
    primary = int(hidden.sum())
    size_order = table[list(columns)].min(axis=1)

    changed = True
    while changed:
        changed = False
        for dim in dims:
            others = [d for d in dims if d != dim]
            lines = [part.index for _, part in table.groupby(others, dropna=False)] if others else [table.index]
            for index in lines:
                if hidden[index].sum() != 1 or len(index) < 2:
                    continue
                visible = size_order[index][~hidden[index]]
                hidden[visible.idxmin()] = True
                changed = True

    result = table.copy()
    result[list(columns)] = result[list(columns)].astype("Float64").mask(hidden)
    result[SUPPRESSED_COLUMN] = hidden
    return result, primary, int(hidden.sum()) - primary


def release_simple(
    frame: pd.DataFrame,
    levels: Sequence[Sequence[str]],
    measures: Sequence[str],
    support: Sequence[str] = (),
) -> GuardedTable:
    """단순집계(합계) 표를 반출 가능한 형태로 만든다.

    levels: 집계 단위 후보. 첫 번째가 원하는 단위이고 뒤로 갈수록 상위(더 큰) 단위다.
            예) [["bjd", "biz_nm"], ["dstrct_se", "biz_nm"], ["city", "biz_nm"]]
    measures: 합산할 값 컬럼 (금액, 건수처럼 더할 수 있는 값).
    support: 값은 아니지만 칸의 크기를 판단하는 컬럼 (이용자수, 이용건수).

    원하는 단위에 10 미만 칸이 있으면 상위 단위로 올려 본다(범주처리).
    가장 상위 단위에서도 남으면 원하는 단위로 돌아가 해당 칸을 지운다(삭제처리).
    """
    columns = list(dict.fromkeys([*measures, *support]))
    for depth, dims in enumerate(levels):
        table = _aggregate(frame, dims, columns)
        if not _small(table, columns).any():
            if depth == 0:
                return GuardedTable(table, "simple", "none", _token=_ISSUED_BY_GUARD)
            note = f"10 미만 칸이 있어 집계 단위를 [{', '.join(levels[0])}] 에서 [{', '.join(dims) or '전체'}] 로 올렸습니다(범주처리)."
            return GuardedTable(table, "simple", "rollup", (note,), _token=_ISSUED_BY_GUARD)

    dims = list(levels[0])
    table, primary, secondary = _suppress(_aggregate(frame, dims, columns), dims, columns)
    note = f"10 미만 칸 {primary}개를 비공개 처리했고, 역산을 막기 위해 {secondary}개 칸을 추가로 비공개 처리했습니다(삭제처리)."
    return GuardedTable(table, "simple", "suppress", (note,), _token=_ISSUED_BY_GUARD)


def release_derived(frame: pd.DataFrame, values: Sequence[str], support: Sequence[str]) -> GuardedTable:
    """응용집계(증감률·비율·지수 등) 표를 내보낸다.

    values: 응용집계 값 컬럼.
    support: 각 행의 근거가 된 단순집계 컬럼 (예: 올해·작년 이용자수). 10 미만이면 그 행의 값을 내보내지 않는다.
             근거 컬럼 자체는 결과에서 제거한다 (비율과 분모가 함께 나가면 분자를 되살릴 수 있으므로).
    """
    weak = _small(frame, support)
    table = frame.drop(columns=list(support))
    table[list(values)] = table[list(values)].astype("Float64").mask(weak)
    table[SUPPRESSED_COLUMN] = weak
    notes = (f"근거 표본이 10 미만인 {int(weak.sum())}개 행은 값을 내보내지 않았습니다.",) if weak.any() else ()
    return GuardedTable(table, "derived", "suppress" if weak.any() else "none", notes, _token=_ISSUED_BY_GUARD)


def annotate(table: GuardedTable, *notes: str) -> GuardedTable:
    """가드를 거친 표에 설명을 덧붙인다. 값은 바꾸지 않는다."""
    return GuardedTable(table.frame, table.kind, table.treatment, (*table.notes, *notes), _token=_ISSUED_BY_GUARD)


def llm_payload(tables: dict[str, GuardedTable]) -> dict:
    """외부 LLM 으로 보낼 수 있는 유일한 형태. 가드를 거치지 않은 표는 거부한다."""
    payload = {}
    for name, table in tables.items():
        if not isinstance(table, GuardedTable):
            raise TypeError(f"'{name}' 은(는) 반출 가드를 거치지 않았습니다. release_simple/release_derived 로 만든 표만 보낼 수 있습니다.")
        payload[name] = {
            "집계유형": "응용집계" if table.kind == "derived" else "단순집계",
            "처리내역": list(table.notes),
            "rows": table.frame.astype(object).where(table.frame.notna(), None).to_dict("records"),
        }
    return payload


def citation(dataset_title: str, period: str, provider: str = "제주빅데이터센터") -> str:
    """반출 정책이 요구하는 출처 표기. 공개 데이터는 제공처를 그대로 적는다."""
    return f"{provider}, {dataset_title}({period})"
