"""Agent 가 쓰는 도구.

도구 결과는 그대로 외부 LLM 으로 나간다. 그래서 지표는 항상 llm_payload(반출 가드를 거친 표)로만 내보내고,
CSV 원본을 읽는 도구는 두지 않는다.
"""
from __future__ import annotations

from typing import Callable

from golmok import metrics, models
from golmok.guard import llm_payload
from golmok.report import MetricSection
from golmok.schemas import get_dataset, load_registry

# 도구 이름 → (계산 함수, 호출 형식, 기본 이용자 구분, 화면 탭 번호)
#   형식 card    : f(con, month, level, biz, [segment])   법정동·권역·시·전체
#   형식 region  : f(con, month, biz, segment)             항상 권역
#   형식 emd     : f(con, month, level, biz)               행정동·권역·시·전체
METRICS = {
    "sales_yoy": (metrics.sales_yoy, "card", "전체", 0),
    "ticket_size_yoy": (metrics.ticket_size_yoy, "card", "전체", 1),
    "spend_share": (metrics.spend_share, "card", "외국인", 2),
    "gap_fill": (metrics.gap_fill, "card", None, 3),
    "conversion_index": (metrics.conversion_index, "region", "외국인", 4),
    "waste_burden": (metrics.waste_burden, "emd", None, 5),
    "cruise_lift": (metrics.cruise_lift, "card", "중국", 6),
    "night_share": (metrics.night_share, "card", "외국인", 7),
    "per_visitor": (metrics.per_visitor, "card", "외국인", 8),
    "restaurant_pulse": (metrics.restaurant_pulse, "card", None, 9),
    "local_owner": (metrics.local_owner, "card", "외국인", 10),
    "store_yield": (metrics.store_yield, "card", "관광객", 11),
    # 분석 알고리즘 (관광공사 공개 데이터, 읍면동 단위)
    "expected_gap": (models.expected_gap, "card", "외국인", 12),
    "cruise_adjusted": (models.cruise_adjusted, "card", "외국인", 13),
    "dong_types": (models.dong_types, "card", "외국인", 14),
}
# 데이터가 한 단위로만 있어 집계 단위가 정해진 지표: 요청 단위 → 실제 단위
FIXED_LEVELS = {"local_owner": {"bjd": "city", "dstrct_se": "city"},
                **{name: {"dstrct_se": "bjd", "city": "bjd", "all": "bjd"} for name in ("expected_gap", "cruise_adjusted", "dong_types")}}

# 지표 → (값 컬럼, 변화 컬럼, 모양, 기준선, 단위, 함께 보여줄 보조값 (컬럼, 이름, 단위))
SHAPES = {
    "sales_yoy": ("yoy_pct", None, "diverging", 0, "%", None),
    "ticket_size_yoy": ("ticket_yoy_pct", None, "diverging", 0, "%", None),
    "spend_share": ("share_pct", "change_pp", "share", 0, "%", None),
    "gap_fill": ("tourist_yoy_pct", None, "diverging", 0, "%", ("fill_pct", "메움률", "%")),
    "conversion_index": ("conversion_index", None, "diverging", 1, "", None),
    "waste_burden": ("liters_per_1000", None, "magnitude", 0, "L", ("foreign_visit_yoy_pct", "외국인 방문 전년比", "%")),
    "cruise_lift": ("lift_pct", None, "diverging", 0, "%", ("corr", "상관", "")),
    "night_share": ("night_share_pct", "change_pp", "share", 0, "%", None),
    "per_visitor": ("per_visitor_yoy_pct", None, "diverging", 0, "%", ("arrival_yoy_pct", "입도객 전년比", "%")),
    "restaurant_pulse": ("per_store_yoy_pct", None, "diverging", 0, "%", ("foreign_food_yoy_pct", "외국인 음식점 카드", "%")),
    "local_owner": ("local_take_pct", "change_pp", "share", 0, "%", ("baseline_pct", "개인 이용자 전체", "%")),
    "store_yield": ("yield_index", None, "diverging", 1, "", ("store_share_pct", "가게 비중", "%")),
    "expected_gap": ("gap_pct", None, "diverging", 0, "%", ("visitor_yoy_pct", "방문객 전년比", "%")),
    "cruise_adjusted": ("adj_lift_pct", None, "diverging", 0, "%", ("naive_lift_pct", "보정 전", "%")),
    "dong_types": ("type", None, "category", 0, "", ("conversion_index", "방문 대비 소비", "")),
}
LEVEL_NAMES = {"bjd": "법정동별", "emd": "행정동별", "dstrct_se": "권역별", "city": "시별", "all": "제주 전체"}

# 지표 값마다 어느 쪽이 문제인가 (화면의 '주의 지역'과 같은 규칙). 비중·값 지표는 가장 나쁜 4분의 1, 증감·지수는 기준선보다 나쁜 곳
ISSUE_RULES = {
    "yoy_pct": "low", "ticket_yoy_pct": "low", "share_pct": "low", "tourist_yoy_pct": "low", "conversion_index": "low",
    "liters_per_1000": "high", "lift_pct": "low", "night_share_pct": "low", "per_visitor_yoy_pct": "low", "per_store_yoy_pct": "low",
    "local_take_pct": "low", "yield_index": "low", "gap_pct": "low", "adj_lift_pct": "low",
}
# 문제 동네를 훑을 때 쓰는 지표 (동네 단위로 나오는 것)
SCAN_METRICS = ["sales_yoy", "ticket_size_yoy", "spend_share", "gap_fill", "conversion_index", "per_visitor", "night_share",
                "store_yield", "expected_gap", "cruise_adjusted"]
NUMERALS = {"1": "일", "2": "이", "3": "삼", "4": "사"}


def _place_key(row: dict) -> str | None:
    """행정동(일도1동)과 법정동(일도일동)을 같은 동네로 묶는 이름. 권역·시 단위 행은 None."""
    name = row.get("bjd") or (row.get("emd_nm") or "").split(" ")[-1] or None
    if not name:
        return None
    return name[:-2] + NUMERALS[name[-2]] + "동" if len(name) > 2 and name.endswith("동") and name[-2] in NUMERALS else name

TOOLS = [
    {
        "name": "list_data",
        "description": "지금 넣어 둔 데이터와 들어 있는 달, 가상 데이터 여부를 알려준다. 어떤 달을 볼지 모를 때 먼저 부른다.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "search_datasets",
        "description": "제주빅데이터센터 데이터 명세서(와 공개 데이터 형식)에서 데이터를 찾는다. 이용자에게 어떤 데이터를 내려받으면 되는지 안내할 때 쓴다.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "찾을 말. 예: 유동인구, 종량제, 크루즈"}},
            "required": ["keyword"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compute_metric",
        "description": (
            "관광 낙수 지표를 계산한다. "
            "sales_yoy=매출 증감률(이용자 구분별), ticket_size_yoy=객단가 증감률, spend_share=소비 비중(기본 외국인; 1년 전 대비 변화 포함), "
            "gap_fill=내국인 공백 메우기(내국인 관광객 소비 감소분을 외국인 소비 증가분이 메운 비율), "
            "conversion_index=낙수 지수(소비 비중 ÷ 방문객 비중; 센터 데이터면 권역 단위, 관광공사 공개 데이터면 읍면동 단위; 외국인·중국·일본·동남아·기타외국·내국인관광객), "
            "waste_burden=쓰레기 부담(방문객 1천 명당 종량제봉투 판매량, 행정동 단위; segment 는 쓰지 않음), "
            "cruise_lift=크루즈 기항 효과(크루즈가 많이 온 달과 적게 온 달의 소비 차이, 여러 달을 모아 계산), "
            "night_share=저녁·밤 소비 비중(카드 시간대 데이터; 낮에만 쓰고 떠나는 관광인지 머무는 관광인지), "
            "per_visitor=입도객 1인당 소비 증감(공개 입도통계를 분모로; 입도객 증가만큼 지역 소비가 늘었는가, 외국인·중국·일본·동남아·기타외국·내국인관광객), "
            "restaurant_pulse=동네 식당 체감(외식업 POS 가게당 매출 증감과 외국인 음식점 카드 소비 증감 비교; segment·biz 는 쓰지 않음), "
            "local_owner=도민 가게 몫(업종별 제주도민 개인 매장 비중으로 추정한 소비 몫, 시·전체 단위만), "
            "store_yield=가게당 관광객 소비(동네의 관광객 소비 비중 ÷ 가게 수 비중, 1 = 제주 평균; 매장 수는 소상공인 상가정보, 업종을 안 고르면 소매·숙박·음식점·여가만), "
            "expected_gap=기대 대비 실제 소비(방문객 수·계절·추세로 예측한 기대 소비보다 실제가 몇 % 많고 적은가, 회귀 모델, 읍면동, 외국인·내국인관광객), "
            "cruise_adjusted=크루즈 효과 보정(섬 전체의 계절·추세를 뺀 뒤 중국 크루즈가 많은 달에 동네 소비가 섬 평균보다 더 늘어난 정도, 이중차분, 읍면동), "
            "dong_types=동네 유형(최근 12개월 외국인 소비 구성으로 쇼핑 소비형·숙박·리조트형·사람만 오는형·음식·카페형으로 묶음, k-평균). "
            "데이터가 없으면 missing 에 내려받을 데이터를 알려준다."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": list(METRICS)},
                "month": {"type": "string", "description": "기준 월 YYYYMM"},
                "level": {"type": "string", "enum": list(metrics.LEVELS), "description": "bjd=법정동(쓰레기 부담은 행정동), dstrct_se=권역, city=시, all=제주 전체"},
                "biz_prefix": {"type": ["string", "null"], "description": "업종코드 또는 앞부분. 예: I56220, I56, G47130(면세점). 전체면 null"},
                "segment": {"anyOf": [{"type": "string", "enum": list(metrics.SEGMENTS)}, {"type": "null"}], "description": "이용자 구분. gap_fill, waste_burden 에서는 null. 비우면 지표의 기본값(소비 비중·낙수 지수는 외국인, 크루즈는 중국)"},
                "focus": {"type": "array", "items": {"type": "string"}, "description": "결과에서 볼 지역 이름. 전부 보려면 빈 목록"},
            },
            "required": ["metric", "month", "level", "biz_prefix", "segment", "focus"],
            "additionalProperties": False,
        },
    },
]


NEW_TOOLS = [
    {
        "name": "scan_issues",
        "description": (
            "여러 지표(매출·객단가 증감, 소비 비중, 공백 메우기, 낙수 지수, 입도객 1인당, 저녁·밤 소비, 가게당 소비, 기대 대비 실제, 크루즈 보정)를 "
            "동네 단위로 한꺼번에 계산해, 문제 쪽(증감·지수는 기준선보다 나쁜 곳, 비중은 가장 나쁜 4분의 1)에 여러 번 든 동네를 많이 걸린 순으로 돌려준다. "
            "어디부터 봐야 할지 모를 때, 브리핑을 쓸 때 먼저 부른다. 표본이 적은 값은 세지 않는다."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "기준 월 YYYYMM"},
                "segment": {"type": "string", "enum": ["외국인", "내국인관광객"], "description": "볼 관광객"},
            },
            "required": ["month", "segment"],
            "additionalProperties": False,
        },
    },
    {
        "name": "trend",
        "description": "읍면동(행정동 이름, 예: 건입동·대천동) 몇 곳을 묶어 달마다 소비와 방문객의 전년 같은 달 대비 증감, 소비 비중을 돌려준다. 언제부터 나빠졌는지, 일시적인지 볼 때 쓴다. 관광공사 공개 데이터 기준.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "places": {"type": "array", "items": {"type": "string"}, "description": "읍면동 이름. 빈 목록이면 제주 전체"},
                "segment": {"type": "string", "enum": ["외국인", "내국인관광객", "도민"]},
                "until": {"type": "string", "description": "마지막 달 YYYYMM"},
                "months": {"type": "integer", "description": "보여 줄 달 수(3~36)"},
            },
            "required": ["places", "segment", "until", "months"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_periods",
        "description": "같은 지표를 두 달(예: 작년 같은 달과 올해, 여름과 가을)에 계산해 지역마다 나란히 놓고 차이를 돌려준다. 변화가 이번 달만의 일인지 확인할 때 쓴다.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": list(METRICS)},
                "month_a": {"type": "string", "description": "앞 달 YYYYMM"},
                "month_b": {"type": "string", "description": "뒤 달 YYYYMM"},
                "level": {"type": "string", "enum": list(metrics.LEVELS)},
                "biz_prefix": {"type": ["string", "null"]},
                "segment": {"anyOf": [{"type": "string", "enum": list(metrics.SEGMENTS)}, {"type": "null"}]},
                "focus": {"type": "array", "items": {"type": "string"}, "description": "볼 지역 이름. 전부면 빈 목록"},
            },
            "required": ["metric", "month_a", "month_b", "level", "biz_prefix", "segment", "focus"],
            "additionalProperties": False,
        },
    },
    {
        "name": "place_summary",
        "description": "읍면동 몇 곳을 묶어 외국인 관광객 소비·방문객의 전년 대비 증감, 낙수 지수, 가게당 관광객 소비를 한 번에 돌려준다(관광공사 공개 데이터). 특정 동네를 소개할 때 쓴다.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "places": {"type": "array", "items": {"type": "string"}, "description": "읍면동 이름(행정동). 빈 목록이면 제주 전체"},
                "month": {"type": "string", "description": "기준 월 YYYYMM"},
            },
            "required": ["places", "month"],
            "additionalProperties": False,
        },
    },
]
TOOLS.extend(NEW_TOOLS)
TOOL_STEP_NAMES = {"scan_issues": "여러 지표로 문제 동네 훑기", "trend": "월별 추이 확인", "compare_periods": "두 달 비교", "place_summary": "동네 요약"}


class Toolbox:
    """도구 실행기. 한 질문에 하나씩 만들고, 쓴 근거와 마지막 화면 조건을 모아 둔다."""

    def __init__(self, con, lock, describe: Callable[[], dict]):
        self.con, self.lock, self.describe = con, lock, describe
        self.evidence: list[dict] = []
        self.sources: list[str] = []
        self.view: dict | None = None
        self.sections: list[MetricSection] = []

    def execute(self, name: str, tool_input: dict) -> dict:
        if name == "list_data":
            return self.describe()
        if name == "search_datasets":
            return self._search(tool_input["keyword"])
        if name == "compute_metric":
            return self._compute(**tool_input)
        if name == "scan_issues":
            return self._scan(**tool_input)
        if name == "trend":
            return self._trend(**tool_input)
        if name == "compare_periods":
            return self._compare(**tool_input)
        if name == "place_summary":
            return self._place(**tool_input)
        raise ValueError(f"없는 도구입니다: {name}")

    # ----- 새 도구 -----
    def _metric_rows(self, metric: str, month: str, level: str, biz: str | None, segment: str | None) -> tuple[str, list[dict]]:
        """지표를 계산해 (제목, 가드를 거친 행) 을 돌려준다. 화면·보고서 기록은 남기지 않는다."""
        function, kind, default_segment, _ = METRICS[metric]
        segment = segment or default_segment
        level = FIXED_LEVELS.get(metric, {}).get(level, level)
        with self.lock:
            if kind == "region":
                result = function(self.con, month, biz, segment)
            elif kind == "emd" or default_segment is None:
                result = function(self.con, month, level, biz)
            else:
                result = function(self.con, month, level, biz, segment)
        return result.title, llm_payload({result.title: result.table})[result.title]["rows"], list(result.sources)

    def _scan(self, month: str, segment: str) -> dict:
        places: dict[str, dict] = {}
        used, skipped = [], []
        for metric in SCAN_METRICS:
            value_key, _, shape, baseline, _, _ = SHAPES[metric]
            direction = ISSUE_RULES.get(value_key)
            try:
                title, rows, sources = self._metric_rows(metric, month, "bjd", None, segment if METRICS[metric][2] is not None else None)
            except (metrics.MissingData, ValueError):
                skipped.append(TOOL_METRIC_NAMES.get(metric, metric))
                continue
            checked = [row for row in rows if row.get(value_key) is not None and not row.get("비공개") and row.get("신뢰수준") != "낮음" and _place_key(row)]
            if not checked:
                skipped.append(TOOL_METRIC_NAMES.get(metric, metric))
                continue
            used.append(title)
            self.sources.extend(s for s in sources if s not in self.sources)
            worse = (lambda row: -row[value_key]) if direction == "low" else (lambda row: row[value_key])
            if shape == "diverging":
                flagged = [row for row in checked if (row[value_key] < baseline if direction == "low" else row[value_key] > baseline)]
            else:
                flagged = sorted(checked, key=worse, reverse=True)[: max(1, len(checked) // 4)]
            flagged_keys = {_place_key(row) for row in flagged}
            for row in checked:
                key = _place_key(row)
                entry = places.setdefault(key, {"동네": row.get("bjd") or row.get("emd_nm"), "권역": row.get("dstrct_se"), "걸린 지표": [], "계산된 지표 수": 0})
                entry["계산된 지표 수"] += 1
                if key in flagged_keys:
                    value = row[value_key]
                    shown = f"{value:.1f}%" if shape == "share" else (f"{value:+.1f}%" if SHAPES[metric][4] == "%" else f"{value:.2f}")
                    entry["걸린 지표"].append({"지표": title, "값": value, "표시": shown})
                    self.evidence.append({"value": row[value_key]})
        ranked = sorted((p for p in places.values() if p["걸린 지표"]), key=lambda p: (-len(p["걸린 지표"]), -len(p["걸린 지표"]) / p["계산된 지표 수"], p["동네"]))
        for entry in ranked:
            entry["걸린 지표 수"] = len(entry["걸린 지표"])
        return {"기준 월": month, "관광객": segment, "본 지표": used, "데이터가 없어 빠진 지표": skipped,
                "규칙": "증감·지수는 기준선(0% 또는 1)보다 나쁜 곳, 비중·값은 가장 나쁜 4분의 1. 표본 적은 값 제외",
                "동네": ranked[:8], "전체 동네 수": len(places)}

    def _trend(self, places: list[str], segment: str, until: str, months: int) -> dict:
        try:
            with self.lock:
                result = models.place_trend(self.con, places, segment, until, months)
        except metrics.MissingData as error:
            return {"missing": str(error)}
        rows = llm_payload({result.title: result.table})[result.title]["rows"]
        self.evidence.extend(rows)
        self.sources.extend(s for s in result.sources if s not in self.sources)
        return {"지표": result.title, "계산식": result.definition, "출처": list(result.sources), "rows": rows}

    def _compare(self, metric: str, month_a: str, month_b: str, level: str, biz_prefix: str | None = None, segment: str | None = None, focus: list[str] | None = None) -> dict:
        if metric not in METRICS:
            raise ValueError(f"없는 지표입니다: {metric}")
        value_key = SHAPES[metric][0]
        try:
            title, rows_a, sources_a = self._metric_rows(metric, month_a, level, biz_prefix or None, segment)
            _, rows_b, sources_b = self._metric_rows(metric, month_b, level, biz_prefix or None, segment)
        except metrics.MissingData as error:
            return {"missing": str(error), "데이터": f"{error.dataset.id} {error.dataset.title}", "필요한 달": error.months}
        place = lambda row: row.get("bjd") or row.get("emd_nm") or row.get("dstrct_se") or row.get("city") or "제주 전체"
        before = {place(row): row.get(value_key) for row in rows_a}
        rows = []
        for row in rows_b:
            name = place(row)
            if focus and not any(name.startswith(f) or f in name for f in focus):
                continue
            a, b = before.get(name), row.get(value_key)
            diff = round(b - a, 2) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
            rows.append({"지역": name, "앞 달": a, "뒤 달": b, "차이": diff})
            self.evidence.append({"a": a, "b": b, "d": diff})
        self.sources.extend(s for s in [*sources_a, *sources_b] if s not in self.sources)
        return {"지표": title, "값": value_key, "앞 달": month_a, "뒤 달": month_b, "rows": rows[:30]}

    def _place(self, places: list[str], month: str) -> dict:
        with self.lock:
            profile = metrics.place_profile(self.con, places, month)
        if not profile.get("available"):
            return {"missing": "관광공사 공개 소비 데이터가 없습니다"}
        profile = {key: value for key, value in profile.items() if key not in ("trend", "areas")}
        self.evidence.append({k: v for k, v in profile.items() if isinstance(v, (int, float))})
        if profile.get("source") and profile["source"] not in self.sources:
            self.sources.append(profile["source"])
        return profile

    def _search(self, keyword: str) -> dict:
        words = keyword.split()
        found = []
        for dataset in load_registry().values():
            text = " ".join([dataset.title, dataset.description, *(c.ko for c in dataset.columns)])
            if words and all(word in text for word in words):
                found.append({"번호": dataset.id, "이름": dataset.title, "기간": dataset.period, "반출": "가능" if dataset.exportable else "불가", "제공처": dataset.provider})
        return {"결과": found[:8], "총 건수": len(found)}

    def _compute(self, metric: str, month: str, level: str, biz_prefix: str | None = None, segment: str | None = None, focus: list[str] | None = None) -> dict:
        if metric not in METRICS:
            raise ValueError(f"없는 지표입니다: {metric}")
        function, kind, default_segment, tab = METRICS[metric]
        biz = biz_prefix or None
        segment = segment or default_segment
        level = FIXED_LEVELS.get(metric, {}).get(level, level)
        try:
            with self.lock:
                if kind == "region":
                    result = function(self.con, month, biz, segment)
                elif kind == "emd":
                    result = function(self.con, month, level, biz)
                elif default_segment is None:
                    result = function(self.con, month, level, biz)
                else:
                    result = function(self.con, month, level, biz, segment)
        except metrics.MissingData as error:
            return {"missing": str(error), "데이터": f"{error.dataset.id} {error.dataset.title}", "필요한 달": error.months}

        payload = llm_payload({result.title: result.table})[result.title]
        rows = all_rows = payload["rows"]
        note = None
        if focus:
            rows = [row for row in rows if any(_matches(row, name) for name in focus)]
            if not rows:
                note = f"{', '.join(focus)} 의 결과가 없습니다. 이 지역에 해당 결제가 없거나 지역 이름이 다를 수 있습니다."
        self.evidence.extend(rows)
        self.sources.extend(s for s in result.sources if s not in self.sources)
        shown_level = ("bjd" if "bjd" in result.table.frame.columns else "dstrct_se") if kind == "region" else ("emd" if kind == "emd" and level == "bjd" else level)
        # 화면은 처음 계산한 지표를 보여준다. 뒤의 계산은 비교를 위한 것일 때가 많다.
        self.view = self.view or {
            "month": month, "level": "bjd" if shown_level == "emd" else shown_level, "biz": biz or "",
            "segment": segment if default_segment is not None else "외국인", "tab": tab, "focus": rows[0] if focus and len(rows) == 1 else None,
        }
        value_key, change_key, shape, baseline, unit, extra = SHAPES[metric]
        condition = f"{month[:4]}년 {int(month[4:])}월, {LEVEL_NAMES[shown_level]}, 업종 {_biz_name(biz)}"
        if metric == "cruise_lift":
            condition = f"{month[:4]}년 {int(month[4:])}월까지 여러 달, {LEVEL_NAMES[shown_level]}, 업종 {_biz_name(biz)}"
        self.sections.append(MetricSection(result.title, result.definition, condition, value_key, change_key, shape, baseline, unit, list(payload["처리내역"]), list(result.sources), rows, all_rows if focus else [], extra))
        answer = {"지표": result.title, "계산식": result.definition, "집계유형": payload["집계유형"], "처리내역": payload["처리내역"], "출처": list(result.sources), "rows": rows}
        if note:
            answer["참고"] = note
        return answer


TOOL_METRIC_NAMES = {
    "sales_yoy": "매출 증감률", "ticket_size_yoy": "객단가 증감률", "spend_share": "소비 비중", "gap_fill": "내국인 공백 메우기",
    "conversion_index": "낙수 지수", "per_visitor": "입도객 1인당 소비", "night_share": "저녁·밤 소비 비중",
    "store_yield": "가게당 관광객 소비", "expected_gap": "기대 대비 실제 소비", "cruise_adjusted": "크루즈 효과 보정",
}


def _biz_name(biz: str | None) -> str:
    if not biz:
        return "전체"
    names = {"I56": "음식점·카페·주점", "I55": "숙박", "G47": "소매", **dict(business_codes())}
    return names.get(biz, biz)


def _matches(row: dict, name: str) -> bool:
    return any(str(row.get(key) or "").startswith(name) or name in str(row.get(key) or "") for key in ("bjd", "emd_nm", "dstrct_se", "city"))


def business_codes() -> list[tuple[str, str]]:
    return [tuple(row) for row in get_dataset(metrics.CARD_MONTHLY).reference("업종코드").rows]


# 행정동 43곳 (관광공사 공개 데이터·유동인구가 쓰는 이름). 법정동 표에 없는 이름(대천동·화북동 등)도 알아듣게 한다
ADMIN_DONGS = [
    "한림읍", "애월읍", "구좌읍", "조천읍", "한경면", "추자면", "우도면", "일도1동", "일도2동", "이도1동", "이도2동", "삼도1동", "삼도2동",
    "용담1동", "용담2동", "건입동", "화북동", "삼양동", "봉개동", "아라동", "오라동", "연동", "노형동", "외도동", "이호동", "도두동",
    "대정읍", "남원읍", "성산읍", "안덕면", "표선면", "송산동", "정방동", "중앙동", "천지동", "효돈동", "영천동", "동홍동", "서홍동",
    "대륜동", "대천동", "중문동", "예래동",
]


def place_names() -> list[str]:
    legal = [row[0] for row in get_dataset(metrics.CARD_MONTHLY).reference("법정동").rows]
    return list(dict.fromkeys([*legal, *ADMIN_DONGS]))
