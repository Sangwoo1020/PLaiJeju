"""관광공사 공개 데이터(읍면동 소비 P-03, 방문객 P-04)로 돌리는 분석 알고리즘 3종.

API 를 부르지 않고 이 PC 안에서 계산하며, 같은 데이터면 항상 같은 결과가 나온다(난수 없음).

1. expected_gap   기대 소비 대비 실제
   동네마다 '방문객 수·달(계절)·장기 추세'로 기대 소비를 예측하는 회귀 모델을 기준 월 직전 36개월로 맞추고,
   기준 월의 실제 소비가 기대보다 얼마나 많거나 적은지 본다. 낙수 지수(단순 비율)와 달리 계절과 추세를 걸러낸다.
2. cruise_adjusted  크루즈 효과(보정)
   동네와 달의 고정효과를 뺀(섬 전체의 계절·추세·회복세를 뺀) 소비가, 중국 크루즈 관광객이 많은 달에
   그 동네에서 얼마나 더 늘었는지 본다(이중차분). 기존 크루즈 기항 효과의 '회복기 부풀림'을 덜어낸다.
3. dong_types     동네 유형
   최근 12개월 외국인 소비의 업종 구성, 외국인 비중, 방문 대비 소비, 증가세로 43개 동네를 k-평균(k=4)으로 묶고
   묶음마다 가장 두드러진 특징(소매·숙박·음식점 비중, 낮은 방문 대비 소비)으로 이름을 붙인다.

모든 결과는 반출 가드(release_derived)를 거친 증감률·지수·비중만 내보내며 금액은 내보내지 않는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from golmok.guard import annotate, release_derived
from golmok.metrics import (
    CRUISE, CRUISE_NATION, SPEND_PUBLIC, VISITORS_PUBLIC, _PUBLIC_BIZ, _PUBLIC_REGION, MetricResult, MissingData,
    _reliability, _sources, loaded_months, previous_year,
)
from golmok.schemas import get_dataset

FIT_MONTHS = 36          # 기대 소비 모델을 맞추는 기간(기준 월 직전)
BACKTEST_MONTHS = 6      # 예측 오차를 재는 지난 달 수
MIN_COUNT = 10           # 이용건수가 이보다 적은 칸은 쓰지 않는다 (반출 기준과 같은 문턱)
CRUISE_MIN = 8           # 크루즈 보정에 필요한 최소 달 수
SIGNAL_T = 2.58          # 크루즈 보정의 '뚜렷함' 문턱. 달별 값이 서로 이어져 있어(자기상관) 1.96 보다 엄격하게 둔다
TYPE_COUNT = 4
_SEGMENT = {"외국인": ("외국인 관광객", "foreign_visitors"), "내국인관광객": ("내국인 관광객", "domestic_visitors")}
INDUSTRIES = ["소매업", "숙박업", "음식점업", "예술스포츠여가업", "기타서비스업"]


def _segment(segment: str) -> tuple[str, str]:
    if segment not in _SEGMENT:
        raise ValueError("이 분석은 관광공사 공개 데이터로 '외국인' 또는 '내국인관광객'만 볼 수 있습니다")
    return _SEGMENT[segment]


def _spend_panel(con, tourist: str, biz_prefix: str | None) -> pd.DataFrame:
    """(달, 읍면동)별 해당 관광객 소비. 공개 소비(P-03)가 없으면 MissingData."""
    if not loaded_months(con, SPEND_PUBLIC):
        raise MissingData(get_dataset(SPEND_PUBLIC), [])
    frame = con.execute(
        f"""SELECT month, emd_nm AS bjd, {_PUBLIC_REGION} AS dstrct_se, sum(use_amt) AS amt, sum(use_cnt) AS cnt
            FROM "{get_dataset(SPEND_PUBLIC).table}"
            WHERE tourist_type = ? AND ({_PUBLIC_BIZ}) LIKE ? GROUP BY ALL""",
        [tourist, (biz_prefix or "") + "%"],
    ).df()
    if frame.empty:
        raise ValueError("관광공사 공개 소비의 업종은 소매(G47)·숙박(I55)·음식점(I56)·여가(R91)·기타(S96)라 그 업종으로는 볼 수 없습니다")
    return frame


def _visitor_panel(con, column: str) -> pd.DataFrame:
    if not loaded_months(con, VISITORS_PUBLIC):
        raise MissingData(get_dataset(VISITORS_PUBLIC), [])
    return con.execute(f'SELECT month, emd_nm AS bjd, sum({column}) AS visitors FROM "{get_dataset(VISITORS_PUBLIC).table}" GROUP BY ALL').df()


def _months_before(months: list[str], month: str, count: int) -> list[str]:
    return [m for m in sorted(months) if m < month][-count:]


# ---------- 1. 기대 소비 대비 실제 ----------
def _design(frame: pd.DataFrame, dongs: list[str], origin: str) -> np.ndarray:
    """회귀 설계행렬: 동네 고정효과 + 달(1~12월) 효과 + log(방문객) + 선형 추세."""
    dong_dummy = (frame["bjd"].to_numpy()[:, None] == np.array(dongs)[None, :]).astype(float)
    calendar = frame["month"].str[4:].astype(int).to_numpy()
    month_dummy = (calendar[:, None] == np.arange(2, 13)[None, :]).astype(float)   # 1월이 기준
    trend = ((frame["month"].str[:4].astype(int) - int(origin[:4])) * 12 + frame["month"].str[4:].astype(int) - int(origin[4:])).to_numpy() / 12
    return np.column_stack([dong_dummy, month_dummy, np.log(frame["visitors"].to_numpy()), trend])


def _fit_predict(panel: pd.DataFrame, month: str) -> tuple[pd.DataFrame, float, int] | None:
    """기준 월 직전 FIT_MONTHS 개월로 맞추고 기준 월을 예측한다. (예측표, 잔차 표준편차, 학습 행 수)."""
    train_months = _months_before(panel["month"].unique().tolist(), month, FIT_MONTHS)
    train = panel[panel["month"].isin(train_months)]
    target = panel[panel["month"] == month]
    if len(train_months) < 13 or target.empty:
        return None
    dongs = sorted(train["bjd"].unique())
    target = target[target["bjd"].isin(dongs)]
    origin = train_months[0]
    x, y = _design(train, dongs, origin), np.log(train["amt"].to_numpy())
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    dof = max(1, len(y) - np.linalg.matrix_rank(x))
    sigma = float(np.sqrt(np.sum((y - x @ coef) ** 2) / dof))
    predicted = _design(target, dongs, origin) @ coef
    return target.assign(log_expected=predicted), sigma, len(y)


def expected_gap(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "외국인") -> MetricResult:
    """기대 소비 대비 실제: (실제 소비 ÷ 방문객·계절·추세로 예측한 기대 소비 − 1) × 100. 읍면동 단위."""
    tourist, visitor_column = _segment(segment)
    spend = _spend_panel(con, tourist, biz_prefix)
    visitors = _visitor_panel(con, visitor_column)
    panel = spend.merge(visitors, on=["month", "bjd"])
    panel = panel[(panel["amt"] > 0) & (panel["visitors"] > 0) & (panel["cnt"] >= MIN_COUNT)]
    if month not in set(panel["month"]):
        raise MissingData(get_dataset(SPEND_PUBLIC), [month])
    fitted = _fit_predict(panel, month)
    if fitted is None:
        raise MissingData(get_dataset(SPEND_PUBLIC), _months_before(sorted(set(panel["month"])), month, 13) or [month])
    table, sigma, rows = fitted
    gap = np.log(table["amt"]) - table["log_expected"]
    table["gap_pct"] = ((np.exp(gap) - 1) * 100).round(1)
    table["signal"] = np.where(np.abs(gap) > 1.645 * sigma, "뚜렷함", "보통 범위")

    previous = visitors[visitors["month"] == previous_year(month)].rename(columns={"visitors": "visitors_before"})
    table = table.merge(previous[["bjd", "visitors_before"]], on="bjd", how="left")
    table["visitor_yoy_pct"] = ((table["visitors"] / table["visitors_before"] - 1) * 100).round(1)

    # 신뢰: 지난 6개월을 한 달씩 예측해 본 오차(절댓값의 중앙값)
    errors = []
    for past in _months_before(sorted(set(panel["month"])), month, BACKTEST_MONTHS):
        back = _fit_predict(panel, past)
        if back:
            errors.extend(np.abs(np.exp(np.log(back[0]["amt"]) - back[0]["log_expected"]) - 1).tolist())
    backtest = f"지난 {BACKTEST_MONTHS}개월을 한 달씩 미리 예측해 본 오차는 중앙값 {np.median(errors) * 100:.1f}%입니다." if errors else ""

    values = ["gap_pct", "visitor_yoy_pct"]
    frame = table[["dstrct_se", "bjd", *values, "signal", "cnt"]].rename(columns={"cnt": "users"})
    frame = frame.assign(신뢰수준=_reliability(frame, ["users"])).sort_values("gap_pct", ignore_index=True)
    released = release_derived(frame, values, ["users"])
    released = annotate(
        released,
        f"기대 소비는 기준 월 직전 {FIT_MONTHS}개월({rows:,}개 동네·달)로 맞춘 회귀(동네 고정효과, 1~12월 효과, log 방문객, 추세)의 예측입니다. "
        f"'뚜렷함'은 예측 오차의 90% 범위(±{(np.exp(1.645 * sigma) - 1) * 100:.0f}%)를 벗어난 곳입니다. {backtest}",
    )
    return MetricResult(
        f"기대 대비 실제 소비({segment})",
        f"(실제 {segment} 소비 ÷ 방문객 수·계절·추세로 예측한 기대 소비 − 1) × 100. 0 보다 작으면 온 사람에 비해 기대보다 덜 쓴 동네",
        released,
        _sources((SPEND_PUBLIC, [*_months_before(sorted(set(panel["month"])), month, FIT_MONTHS), month]), (VISITORS_PUBLIC, [month])),
    )


# ---------- 2. 크루즈 효과(보정) ----------
def _cruise_series(con) -> tuple[str, pd.DataFrame]:
    if loaded_months(con, CRUISE_NATION):
        frame = con.execute(f"""SELECT month, sum(CASE WHEN country = '중국' THEN cruise_arrivals ELSE 0 END) AS china
                                FROM "{get_dataset(CRUISE_NATION).table}" GROUP BY month""").df()
        return CRUISE_NATION, frame
    if loaded_months(con, CRUISE):
        return CRUISE, con.execute(f'SELECT month, china_passengers AS china FROM "{get_dataset(CRUISE).table}"').df()
    raise MissingData(get_dataset(CRUISE), [])


def _two_way_residual(frame: pd.DataFrame, column: str, rounds: int = 25) -> pd.Series:
    """동네 평균과 달 평균을 번갈아 빼 두 방향 고정효과를 걷어낸다(빈칸이 있어도 맞도록 반복)."""
    residual = frame[column] - frame[column].mean()
    for _ in range(rounds):
        residual = residual - residual.groupby(frame["bjd"]).transform("mean")
        residual = residual - residual.groupby(frame["month"]).transform("mean")
    return residual


def cruise_adjusted(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "외국인") -> MetricResult:
    """크루즈 효과(보정): 섬 전체의 계절·추세를 뺀 뒤, 중국 크루즈 관광객이 많은 달에 그 동네 소비가 섬 평균보다 더 늘어난 정도."""
    tourist, _ = _segment(segment)
    spend = _spend_panel(con, tourist, biz_prefix)
    cruise_id, cruise = _cruise_series(con)
    months = sorted(set(spend["month"]) & set(cruise["month"]))
    months = [m for m in months if m <= month]
    if len(months) < CRUISE_MIN:
        raise MissingData(get_dataset(cruise_id), [month])
    panel = spend[spend["month"].isin(months) & (spend["amt"] > 0) & (spend["cnt"] >= MIN_COUNT)].merge(cruise, on="month")
    panel = panel.assign(log_amt=np.log(panel["amt"]))
    panel["residual"] = _two_way_residual(panel, "log_amt")
    panel["high"] = panel["china"] > cruise[cruise["month"].isin(months)]["china"].median()

    rows = []
    for (dstrct_se, bjd), group in panel.groupby(["dstrct_se", "bjd"]):
        high, low = group[group["high"]], group[~group["high"]]
        if len(high) < 3 or len(low) < 3:
            continue
        effect = high["residual"].mean() - low["residual"].mean()
        se = np.sqrt(high["residual"].var(ddof=1) / len(high) + low["residual"].var(ddof=1) / len(low))
        naive = high["amt"].mean() / low["amt"].mean() - 1
        rows.append({"dstrct_se": dstrct_se, "bjd": bjd, "adj_lift_pct": round((np.exp(effect) - 1) * 100, 1),
                     "naive_lift_pct": round(naive * 100, 1), "signal": "뚜렷함" if se > 0 and abs(effect / se) > SIGNAL_T else "보통 범위",
                     "users": int(group["cnt"].min())})
    frame = pd.DataFrame(rows)
    values = ["adj_lift_pct", "naive_lift_pct"]
    frame = frame.assign(신뢰수준=_reliability(frame, ["users"])).sort_values("adj_lift_pct", ignore_index=True)
    released = annotate(
        release_derived(frame, values, ["users"]),
        f"{months[0][:4]}.{months[0][4:]}~{months[-1][:4]}.{months[-1][4:]} {len(months)}개월. 동네·달 고정효과로 섬 전체의 계절·추세·회복세를 뺀 뒤, "
        "중국 크루즈 관광객이 중앙값보다 많은 달과 적은 달의 차이를 봅니다. 섬 평균 대비 '추가로' 늘어난 정도라 섬 평균 동네는 0 근처입니다. "
        "'뚜렷함'은 차이가 표준오차의 2.58배를 넘는 곳입니다(달끼리 이어진 값이라 엄격하게 둠). 단순 비교는 보정 전 값입니다.",
    )
    return MetricResult(
        f"크루즈 효과 보정({segment})",
        f"중국 크루즈 관광객이 많은 달의 {segment} 소비 증가에서 섬 전체의 계절·추세를 뺀 동네별 추가 변화(%)",
        released,
        _sources((SPEND_PUBLIC, months), (cruise_id, months)),
    )


# ---------- 3. 동네 유형 ----------
TYPE_NAMES = {  # 특징 → (이름, 한 줄 설명)
    "retail": ("쇼핑 소비형", "외국인 소비가 소매(면세·화장품 등)에 몰린 곳"),
    "lodging": ("숙박·리조트형", "외국인 소비에서 숙박 비중이 큰 곳"),
    "visit_only": ("사람만 오는형", "방문에 비해 외국인 소비가 적은 곳"),
    "food": ("음식·카페형", "외국인 소비가 음식점·카페에 몰린 관광 마을"),
}


def _kmeans(points: np.ndarray, k: int, rounds: int = 100) -> np.ndarray:
    """결정적 k-평균: 첫 중심은 원점에서 가장 먼 점, 다음은 이미 고른 중심들에서 가장 먼 점(난수 없음)."""
    centers = [points[np.argmax(np.linalg.norm(points, axis=1))]]
    while len(centers) < k:
        distance = np.min([np.linalg.norm(points - c, axis=1) for c in centers], axis=0)
        centers.append(points[np.argmax(distance)])
    centers = np.array(centers)
    labels = np.zeros(len(points), dtype=int)
    for _ in range(rounds):
        labels_new = np.argmin(np.linalg.norm(points[:, None, :] - centers[None, :, :], axis=2), axis=1)
        if np.array_equal(labels_new, labels) and _ > 0:
            break
        labels = labels_new
        centers = np.array([points[labels == j].mean(axis=0) if np.any(labels == j) else centers[j] for j in range(k)])
    return labels


def dong_types(con, month: str, level: str = "bjd", biz_prefix: str | None = None, segment: str = "외국인") -> MetricResult:
    """동네 유형: 최근 12개월 외국인 소비 구성으로 동네를 4가지로 묶는다."""
    if not loaded_months(con, SPEND_PUBLIC):
        raise MissingData(get_dataset(SPEND_PUBLIC), [])
    table = get_dataset(SPEND_PUBLIC).table
    recent = [m for m in loaded_months(con, SPEND_PUBLIC) if m <= month][-12:]
    earlier = [m for m in loaded_months(con, SPEND_PUBLIC) if m <= previous_year(month)][-12:]
    if len(recent) < 12:
        raise MissingData(get_dataset(SPEND_PUBLIC), [month])
    marks = lambda ms: ", ".join("?" for _ in ms)
    spend = con.execute(
        f"""SELECT emd_nm AS bjd, {_PUBLIC_REGION} AS dstrct_se, tourist_type, industry, sum(use_amt) AS amt, sum(use_cnt) AS cnt
            FROM "{table}" WHERE month IN ({marks(recent)}) GROUP BY ALL""", recent).df()
    foreign = spend[spend["tourist_type"] == "외국인 관광객"]
    mix = foreign.pivot_table(index=["dstrct_se", "bjd"], columns="industry", values="amt", aggfunc="sum", fill_value=0)
    mix = mix.reindex(columns=INDUSTRIES, fill_value=0)
    features = pd.DataFrame(index=mix.index)
    total_foreign = mix.sum(axis=1)
    features["retail_pct"] = mix["소매업"] / total_foreign * 100
    features["lodging_pct"] = mix["숙박업"] / total_foreign * 100
    features["food_pct"] = mix["음식점업"] / total_foreign * 100
    all_spend = spend.groupby(["dstrct_se", "bjd"])["amt"].sum()
    features["foreign_share_pct"] = total_foreign / all_spend * 100
    users = foreign.groupby(["dstrct_se", "bjd"])["cnt"].sum()

    visitors = con.execute(
        f'SELECT emd_nm AS bjd, sum(foreign_visitors) AS v FROM "{get_dataset(VISITORS_PUBLIC).table}" WHERE month IN ({marks(recent)}) GROUP BY ALL', recent
    ).df().set_index("bjd")["v"] if loaded_months(con, VISITORS_PUBLIC) else None
    if visitors is not None:
        share_spend = total_foreign / total_foreign.sum()
        v = visitors.reindex(features.index.get_level_values("bjd")).to_numpy()
        features["conversion_index"] = (share_spend.to_numpy() / (v / np.nansum(v)))
    else:
        features["conversion_index"] = 1.0
    if len(earlier) == 12:
        before = con.execute(
            f"""SELECT emd_nm AS bjd, sum(use_amt) AS amt FROM "{table}" WHERE month IN ({marks(earlier)}) AND tourist_type = '외국인 관광객' GROUP BY ALL""", earlier
        ).df().set_index("bjd")["amt"]
        features["growth_pct"] = (total_foreign.to_numpy() / before.reindex(features.index.get_level_values("bjd")).to_numpy() - 1) * 100
    else:
        features["growth_pct"] = 0.0
    features = features.replace([np.inf, -np.inf], np.nan).fillna(features.median(numeric_only=True))

    # 표준화 후 묶기. 낙수 지수는 치우침이 커서 log 로 편다
    model = features.assign(conversion_index=np.log(features["conversion_index"].clip(lower=0.01)))
    columns = ["retail_pct", "lodging_pct", "food_pct", "foreign_share_pct", "conversion_index", "growth_pct"]
    z = (model[columns] - model[columns].median()) / model[columns].std(ddof=0).replace(0, 1)
    z = z.clip(-2, 2)   # 한두 동네의 극단값이 혼자 한 유형이 되지 않게 표준점수를 ±2 에서 자른다
    labels = _kmeans(z.to_numpy(), TYPE_COUNT)

    # 묶음마다 가장 두드러진 특징으로 이름 붙이기(한 특징은 한 묶음에만)
    centroid = pd.DataFrame(z.to_numpy()).groupby(labels).mean()
    centroid.columns = columns
    score = pd.DataFrame({
        "retail": centroid["retail_pct"], "lodging": centroid["lodging_pct"],
        "visit_only": -centroid["conversion_index"], "food": centroid["food_pct"],
    })
    names: dict[int, str] = {}
    while len(names) < len(score.index):
        remaining = score.drop(index=list(names), columns=[c for c in score.columns if c in names.values()])
        cluster, feature = remaining.stack().idxmax()
        names[cluster] = feature
    order = list(TYPE_NAMES)
    frame = features.reset_index()
    frame["type"] = [TYPE_NAMES[names[label]][0] for label in labels]
    frame["type_id"] = [order.index(names[label]) for label in labels]
    frame["users"] = users.reindex(features.index).to_numpy()
    frame = frame.round({"retail_pct": 1, "lodging_pct": 1, "food_pct": 1, "foreign_share_pct": 1, "conversion_index": 2, "growth_pct": 1})
    weak = frame["users"] < MIN_COUNT
    frame.loc[weak, "type"] = None
    values = ["conversion_index", "retail_pct", "lodging_pct", "food_pct", "foreign_share_pct", "growth_pct"]
    frame = frame.assign(신뢰수준=_reliability(frame, ["users"])).sort_values(["type_id", "bjd"], ignore_index=True)
    released = annotate(
        release_derived(frame[["dstrct_se", "bjd", "type", "type_id", *values, "users", "신뢰수준"]], values, ["users"]),
        f"{recent[0][:4]}.{recent[0][4:]}~{recent[-1][:4]}.{recent[-1][4:]} 12개월 외국인 관광객 소비로 묶었습니다. "
        "특징 6가지(소매·숙박·음식점 비중, 외국인 소비 비중, 방문 대비 소비(log), 1년 전 대비 증가)를 표준화해 k-평균(k=4, 난수 없는 초기값)으로 나누고, "
        "묶음 평균에서 가장 두드러진 특징으로 이름을 붙였습니다. "
        + " / ".join(f"{name}: {note}" for name, note in TYPE_NAMES.values()),
    )
    return MetricResult(
        "동네 유형(외국인 소비)",
        "최근 12개월 외국인 소비의 업종 구성·외국인 비중·방문 대비 소비·증가세로 나눈 4가지 유형",
        released,
        _sources((SPEND_PUBLIC, recent), (VISITORS_PUBLIC, recent)),
    )


# ---------- Agent 도구용: 동네 월별 추이 ----------
def place_trend(con, names: list[str], segment: str = "외국인", until: str | None = None, months: int = 12):
    """읍면동 몇 곳(비면 제주 전체)의 월별 전년 같은 달 대비 소비·방문객 증감과 소비 비중. 금액은 내보내지 않는다."""
    tourist_types = {"외국인": "외국인 관광객", "내국인관광객": "내국인 관광객", "도민": "도민"}
    if segment not in tourist_types:
        raise ValueError("추이는 외국인·내국인관광객·도민으로 볼 수 있습니다")
    spend_months = loaded_months(con, SPEND_PUBLIC)
    if not spend_months:
        raise MissingData(get_dataset(SPEND_PUBLIC), [])
    until = until if until in spend_months else spend_months[-1]
    months = max(3, min(int(months), 36))
    shown = [m for m in spend_months if m <= until][-months:]
    where = f"emd_nm IN ({', '.join('?' for _ in names)})" if names else "TRUE"
    wanted = sorted(set(shown) | {previous_year(m) for m in shown})
    marks = ", ".join("?" for _ in wanted)
    spend = con.execute(
        f"""SELECT month, sum(CASE WHEN tourist_type = ? THEN use_amt ELSE 0 END) AS amt, sum(CASE WHEN tourist_type = ? THEN use_cnt ELSE 0 END) AS cnt,
                   sum(use_amt) AS all_amt
            FROM "{get_dataset(SPEND_PUBLIC).table}" WHERE {where} AND month IN ({marks}) GROUP BY month""",
        [tourist_types[segment], tourist_types[segment], *names, *wanted],
    ).df().set_index("month")
    visitors = None
    if segment != "도민" and loaded_months(con, VISITORS_PUBLIC):
        column = "foreign_visitors" if segment == "외국인" else "domestic_visitors"
        visitors = con.execute(
            f'SELECT month, sum({column}) AS v FROM "{get_dataset(VISITORS_PUBLIC).table}" WHERE {where} AND month IN ({marks}) GROUP BY month',
            [*names, *wanted],
        ).df().set_index("month")["v"]
    rows = []
    for m in shown:
        before = previous_year(m)
        now, past = spend["amt"].get(m), spend["amt"].get(before)
        row = {"month": m, "spend_yoy_pct": round((now / past - 1) * 100, 1) if now and past else np.nan,
               "share_pct": round(now / spend["all_amt"][m] * 100, 1) if now and spend["all_amt"].get(m) else np.nan,
               "visitor_yoy_pct": np.nan, "users": int(min(spend["cnt"].get(m, 0), spend["cnt"].get(before, 0)))}
        if visitors is not None and visitors.get(m) and visitors.get(before):
            row["visitor_yoy_pct"] = round((visitors[m] / visitors[before] - 1) * 100, 1)
        rows.append(row)
    frame = pd.DataFrame(rows)
    values = ["spend_yoy_pct", "visitor_yoy_pct", "share_pct"]
    frame = frame.assign(신뢰수준=_reliability(frame, ["users"]))
    released = release_derived(frame, values, ["users"])
    label = "·".join(names) if names else "제주 전체"
    return MetricResult(
        f"월별 추이({label}, {segment})",
        f"달마다 {segment} 소비와 방문객의 전년 같은 달 대비 증감(%), 그리고 그 달 전체 카드 소비 중 {segment} 비중(%)",
        released,
        _sources((SPEND_PUBLIC, shown), *(((VISITORS_PUBLIC, shown),) if visitors is not None else ())),
    )
