"""로컬 웹 화면용 서버. 센터 PC 에서 실행하고 브라우저(Chrome/Edge)로 접속한다.

    python -m uvicorn golmok.app:app --app-dir src --port 8765

적재한 데이터는 메모리에만 두고, 올린 CSV 는 적재 직후 지운다.
프로그램을 닫으면 원본 데이터가 PC 에 남지 않는다 (센터 퇴실 시 삭제 원칙).
화면으로 나가는 수치도 전부 반출 가드를 거친 표다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import uuid
from importlib import resources
from pathlib import Path

import duckdb
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # agent 를 불러오기 전에 읽어야 모델 설정이 반영된다

from golmok import agent, metrics, models  # noqa: E402
from golmok.guard import GuardedTable  # noqa: E402
from golmok.loader import AmbiguousDataset, SchemaMismatch, UnknownDataset, UnreadableFile, load_center_csv  # noqa: E402
from golmok.report import ReportData, build_zip  # noqa: E402
from golmok.schemas import get_dataset, is_public  # noqa: E402
from golmok.tools import SHAPES, Toolbox  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
MOCK_DIR = ROOT / "data" / "mock_center"
MOCK_GENERATOR = ROOT / "tools" / "make_mock_data.py"
BUNDLED_PUBLIC = [
    "reference/cruise_monthly_public.csv", "reference/foreign_arrivals_public.csv",
    "reference/ijto_spend_public.csv", "reference/ijto_visitors_public.csv",   # 제주관광빅데이터플랫폼. tools/import_ijto.py 로 만든다
    "reference/sangga_stores_public.csv",   # 소상공인 상가정보 매장 수. tools/import_sangga.py 로 만든다
    "reference/ijto_spend_time_public.csv", "reference/ijto_spend_nation_public.csv", "reference/ijto_cruise_nation_public.csv",   # 관광공사 시간대·국가별 소비, 국적별 크루즈
]   # 프로그램에 든 공개 데이터. 시작할 때 적재한다

# 진단에 쓰는 데이터와, 이용자에게 보여줄 쓰임새
NEEDED = [
    (metrics.CARD_MONTHLY, "매출·객단가·소비 비중·내국인 공백 메우기·크루즈 효과·가게당 관광객 소비"),
    (metrics.SPEND_PUBLIC, "센터 카드가 없을 때 대신 쓰는 관광객 소비 (2026년 9월까지 프로그램에 들어 있음)"),
    (metrics.VISITORS_PUBLIC, "읍면동 낙수 지수의 방문객 (2026년 9월까지 프로그램에 들어 있음)"),
    (metrics.FOREIGN_POPULATION, "낙수 지수(외국인), 쓰레기 부담"),
    (metrics.POPULATION, "낙수 지수(내국인), 쓰레기 부담"),
    (metrics.WASTE_BAGS, "쓰레기 부담"),
    (metrics.CRUISE, "크루즈 기항 효과 (2025년 5월까지는 프로그램에 들어 있음)"),
    (metrics.CARD_TIME, "저녁·밤 소비 비중"),
    (metrics.TIME_PUBLIC, "센터 카드 시간대가 없을 때의 저녁·밤 소비 (관광공사 공개)"),
    (metrics.NATION_PUBLIC, "공개 소비로 볼 때 국적별(중국·일본·동남아) 제주 전체 지표 (관광공사 공개)"),
    (metrics.CRUISE_NATION, "크루즈 기항 효과의 중국 크루즈 관광객 수 (관광공사 공개, 입항 실적보다 기간이 김)"),
    (metrics.POS_FOOD, "동네 식당 체감"),
    (metrics.STORE_COUNT, "도민 가게 몫"),
    (metrics.STORES_PUBLIC, "가게당 관광객 소비 (2026년 9월 기준 상가 목록이 프로그램에 들어 있음)"),
    (metrics.ARRIVALS, "입도객 1인당 소비 (2026년 9월까지는 프로그램에 들어 있음)"),
]
LEVEL_LABELS = {"bjd": "법정동", "dstrct_se": "권역", "city": "시", "all": "제주 전체"}
SEGMENT_LABELS = {
    "외국인": "외국인 관광객", "중국": "중국 관광객", "일본": "일본 관광객", "동남아": "동남아 관광객", "기타외국": "기타 외국인 관광객",
    "내국인관광객": "내국인 관광객", "관광객": "관광객 전체", "도민": "도민", "전체": "모든 이용자",
}
BIZ_PRESETS = [("", "모든 업종"), ("G47130", "면세점"), ("I56", "음식점·카페·주점"), ("I55", "숙박"), ("G47", "소매")]

app = FastAPI(title="제주 관광 낙수 진단")
con = duckdb.connect(":memory:")
lock = threading.Lock()
state = {"mock": False}
STARTED = uuid.uuid4().hex[:8]
reports: dict[str, ReportData] = {}  # 내려받기를 기다리는 답변. 메모리에만 둔다
MAX_REPORTS = 20

for name in BUNDLED_PUBLIC:
    bundled = resources.files("golmok").joinpath(name)
    if bundled.is_file():
        load_center_csv(con, str(bundled))


class Ask(BaseModel):
    question: str
    conversation: str | None = None   # 이어지는 질문이면 앞 답에서 받은 대화 번호


MAX_CONVERSATIONS = 50
conversations: dict[str, list[dict]] = {}   # 대화 번호 → 앞선 질문과 답 (메모리에만, 프로그램을 닫으면 사라짐)


class Question(BaseModel):
    month: str
    level: str = "bjd"
    biz: str = ""
    segment: str = "외국인"


def _loaded(dataset_id: str) -> dict:
    """데이터셋의 적재 현황: 행 수와 들어 있는 달."""
    dataset = get_dataset(dataset_id)
    months = metrics.loaded_months(con, dataset_id)
    rows = con.execute(f'SELECT count(*) FROM "{dataset.table}"').fetchone()[0] if months else 0
    return {"id": dataset.id, "title": dataset.title, "rows": rows, "months": months, "public": is_public(dataset_id)}


def _table_json(table: GuardedTable) -> dict:
    return {
        "kind": table.kind,
        "notes": list(table.notes),
        "rows": json.loads(table.frame.to_json(orient="records", force_ascii=False)),
    }


@app.get("/api/status")
def status() -> dict:
    with lock:
        needed = [{**_loaded(dataset_id), "used_for": used_for} for dataset_id, used_for in NEEDED]
        months = metrics.card_months(con)   # 센터 카드(1-08)가 있으면 그 달, 없으면 관광공사 공개 소비의 달
    codes = get_dataset(metrics.CARD_MONTHLY).reference("업종코드").rows
    return {
        "mock": state["mock"],
        "needed": needed,
        "months": months,
        "levels": LEVEL_LABELS,
        "segments": SEGMENT_LABELS,
        "biz": [{"code": code, "name": name} for code, name in BIZ_PRESETS] + [{"code": code, "name": name} for code, name in codes],
    }


@app.post("/api/upload")
async def upload(request: Request, filename: str, dataset_id: str | None = None) -> dict:
    """CSV 한 개를 받아 적재한다. 본문은 파일 내용 그대로다."""
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
        async for chunk in request.stream():
            handle.write(chunk)
        path = Path(handle.name)
    try:
        with lock:
            result = load_center_csv(con, path, dataset_id)
    except AmbiguousDataset as error:
        choices = [{"id": d.id, "title": d.title} for d in error.candidates]
        raise HTTPException(409, {"message": f"'{filename}' 은 컬럼 구성이 같은 데이터가 여러 개라 자동으로 구분할 수 없습니다.", "choices": choices})
    except (UnknownDataset, SchemaMismatch, UnreadableFile, StopIteration, UnicodeDecodeError) as error:
        reason = str(error) or "CSV 가 아니거나 내용이 비어 있습니다."
        raise HTTPException(422, {"message": f"'{filename}' 을 읽지 못했습니다. {reason}"})
    finally:
        path.unlink(missing_ok=True)
    return {
        "dataset": {"id": result.dataset.id, "title": result.dataset.title},
        "rows_added": result.rows_added,
        "skipped_duplicate": result.skipped_duplicate,
    }


@app.post("/api/load-mock")
def load_mock() -> dict:
    """가상 데이터를 (없으면 만들어서) 적재한다."""
    if not any(MOCK_DIR.glob("*.csv")):
        subprocess.run([sys.executable, str(MOCK_GENERATOR)], check=True, capture_output=True)
    with lock:
        loaded = [load_center_csv(con, path) for path in sorted(MOCK_DIR.glob("*.csv"))]
        state["mock"] = True
    return {"files": len(loaded), "rows_added": sum(r.rows_added for r in loaded)}


def _form(metric: str, title: str, **override) -> dict:
    value, change, shape, baseline, unit, extra = SHAPES[metric]
    form = {"title": title, "value": value, "shape": shape, "baseline": baseline, "unit": unit}
    if change:
        form["change"] = change
    if extra:
        form["extra"] = {"key": extra[0], "label": extra[1], "unit": extra[2]}
    return {**form, **override}


def _run(fn, form: dict, *args, **kwargs) -> dict:
    """지표 하나를 계산한다. 데이터가 없으면 무엇을 받아야 하는지를 대신 돌려준다."""
    try:
        with lock:
            result = fn(con, *args, **kwargs)
    except (metrics.MissingData, ValueError) as error:   # 데이터가 없거나, 있어도 지표가 쓰는 값이 없을 때
        return {"title": form["title"], "missing": str(error)}
    if form.get("fixed_level") == "권역" and "bjd" in result.table.frame.columns:   # 관광공사 공개 데이터는 읍면동까지 맞출 수 있다
        form = {**form, "fixed_level": "읍면동"}
    return {
        "title": result.title,
        "definition": result.definition,
        "sources": list(result.sources),
        "form": form,
        **_table_json(result.table),
    }


def _foreign_segment(segment: str, fallback: str) -> str:
    """낙수 지수·크루즈 효과처럼 관광객 구분만 받는 지표에 넘길 구분."""
    return segment if segment in (*metrics.FOREIGN_TYPES, "외국인", "내국인관광객") else fallback


@app.post("/api/diagnose")
def diagnose(q: Question) -> dict:
    if q.level not in metrics.LEVELS or q.segment not in metrics.SEGMENTS:
        raise HTTPException(422, {"message": "알 수 없는 집계 단위 또는 이용자 구분입니다."})
    biz = q.biz or None
    share_segment = q.segment if q.segment != "전체" else "외국인"
    cruise_segment = q.segment if q.segment in metrics.FOREIGN_TYPES else "중국"
    visitor_segment = q.segment if q.segment in (*metrics.FOREIGN_TYPES, "내국인관광객") else "외국인"
    model_segment = q.segment if q.segment == "내국인관광객" else "외국인"   # 분석 알고리즘은 공개 데이터라 외국인·내국인관광객만
    return {
        "metrics": [
            _run(metrics.sales_yoy, _form("sales_yoy", "매출 증감률"), q.month, q.level, biz, q.segment),
            _run(metrics.ticket_size_yoy, _form("ticket_size_yoy", "객단가 증감률"), q.month, q.level, biz, q.segment),
            _run(metrics.spend_share, _form("spend_share", "소비 비중"), q.month, q.level, biz, share_segment),
            _run(metrics.gap_fill, _form("gap_fill", "내국인 공백 메우기"), q.month, q.level, biz),
            _run(metrics.conversion_index, _form("conversion_index", "낙수 지수", fixed_level="권역"), q.month, biz, _foreign_segment(q.segment, "외국인")),
            _run(metrics.waste_burden, _form("waste_burden", "쓰레기 부담", fixed_level="행정동" if q.level == "bjd" else None), q.month, q.level, biz),
            _run(metrics.cruise_lift, _form("cruise_lift", "크루즈 기항 효과"), q.month, q.level, biz, cruise_segment),
            _run(metrics.night_share, _form("night_share", "저녁·밤 소비"), q.month, q.level, biz, q.segment),
            _run(metrics.per_visitor, _form("per_visitor", "입도객 1인당 소비"), q.month, q.level, biz, visitor_segment),
            _run(metrics.restaurant_pulse, _form("restaurant_pulse", "동네 식당 체감"), q.month, q.level),
            _run(metrics.local_owner, _form("local_owner", "도민 가게 몫", fixed_level="시" if q.level in ("bjd", "dstrct_se") else None), q.month, q.level, biz, share_segment),
            _run(metrics.store_yield, _form("store_yield", "가게당 관광객 소비"), q.month, q.level, biz, q.segment if q.segment != "전체" else "관광객"),
            _run(models.expected_gap, _form("expected_gap", "기대 대비 실제", fixed_level="읍면동"), q.month, "bjd", biz, model_segment),
            _run(models.cruise_adjusted, _form("cruise_adjusted", "크루즈 효과 보정", fixed_level="읍면동"), q.month, "bjd", biz, model_segment),
            _run(models.dong_types, _form("dong_types", "동네 유형", fixed_level="읍면동"), q.month, "bjd", None, "외국인"),
        ]
    }


def describe() -> dict:
    """Agent 에게 알려주는 데이터 현황."""
    with lock:
        loaded = [{**_loaded(dataset_id), "쓰임": used_for} for dataset_id, used_for in NEEDED]
        months = metrics.card_months(con)
    return {
        "가상데이터": state["mock"],
        "분석 가능한 달": months,
        "데이터": [{"이름": d["title"], "번호": d["id"], "months": d["months"], "쓰임": d["쓰임"], "공개데이터": d["public"]} for d in loaded],
    }


@app.post("/api/ask")
def ask(q: Ask) -> StreamingResponse:
    """질문을 처리하며 진행 상황과 답을 한 줄씩(JSON) 내보낸다."""
    toolbox = Toolbox(con, lock, describe)
    conversation = q.conversation if q.conversation in conversations else uuid.uuid4().hex
    history = conversations.get(conversation, [])

    def events():
        for event in agent.run(q.question, toolbox, history):
            if event["type"] == "answer":
                event["report_id"] = _keep_report(q.question, event, toolbox)
                event["conversation"] = conversation
                event["turn"] = len(history) + 1
                _remember(conversation, q.question, event["text"])
                print("agent usage:", event["mode"], event["usage"], flush=True)  # 운영 비용 확인용. 질문 내용은 남기지 않는다
            yield json.dumps(event, ensure_ascii=False, default=str) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _remember(conversation: str, question: str, answer: str) -> None:
    turns = conversations.pop(conversation, [])   # 다시 넣어 가장 최근 대화가 맨 뒤에 오게 한다
    conversations[conversation] = [*turns, {"q": question, "a": answer}][-agent.HISTORY_TURNS:]
    while len(conversations) > MAX_CONVERSATIONS:
        conversations.pop(next(iter(conversations)))


@app.post("/api/brief")
def brief() -> StreamingResponse:
    """질문 없이 Agent 가 스스로 이번 달 문제 동네를 찾아 브리핑을 쓴다. 진행 상황과 답을 한 줄씩 내보낸다."""
    toolbox = Toolbox(con, lock, describe)
    with lock:
        months = metrics.card_months(con)
    if not months:
        raise HTTPException(409, {"message": "브리핑에 쓸 소비 데이터가 없습니다."})
    month = months[-1]
    title = f"{month[:4]}년 {int(month[4:])}월 관광 낙수 진단 브리핑"

    def events():
        for event in agent.run_brief(toolbox, month):
            if event["type"] == "answer":
                event["report_id"] = _keep_report(title, event, toolbox)
                event["title"] = title
                print("agent usage (brief):", event["mode"], event["usage"], flush=True)
            yield json.dumps(event, ensure_ascii=False, default=str) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _keep_report(question: str, answer: dict, toolbox: Toolbox) -> str:
    report_id = uuid.uuid4().hex
    while len(reports) >= MAX_REPORTS:
        reports.pop(next(iter(reports)))
    reports[report_id] = ReportData(question, answer["text"], answer["mode"], state["mock"], answer["unverified"], answer["note"], sections=toolbox.sections)
    return report_id


@app.get("/api/report/{report_id}")
def report(report_id: str) -> Response:
    data = reports.get(report_id)
    if data is None:
        raise HTTPException(404, {"message": "보고서를 찾지 못했습니다. 질문을 다시 해 주세요."})
    name = f"naksu_report{'_MOCK' if data.mock else ''}_{data.created:%Y%m%d_%H%M}.zip"
    return Response(build_zip(data), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/summary")
def summary(month: str) -> dict:
    """화면 위쪽에 띄우는 제주 전체 요약 3가지."""
    return {
        "tiles": [
            _run(metrics.sales_yoy, _form("sales_yoy", "외국인 관광객 매출"), month, "all", None, "외국인"),
            _run(metrics.sales_yoy, _form("sales_yoy", "내국인 관광객 매출"), month, "all", None, "내국인관광객"),
            _run(metrics.spend_share, _form("spend_share", "외국인 소비 비중"), month, "all", None, "외국인"),
        ]
    }


@app.get("/api/place")
def place(names: str = "", month: str | None = None) -> dict:
    """메인 화면에서 지명을 눌렀을 때 띄우는 카드의 수치. names 는 쉼표로 이은 행정동 이름(비면 제주 전체)."""
    picked = [n.strip() for n in names.split(",") if n.strip()][:12]
    with lock:
        return metrics.place_profile(con, picked, month)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    # 브라우저가 따로 찾는 아이콘 주소. 화면의 로고를 그대로 준다
    return Response((STATIC_DIR / "logo.svg").read_bytes(), media_type="image/svg+xml")


@app.get("/")
def index() -> HTMLResponse:
    # 서버를 켤 때마다 화면 파일 주소가 바뀌어, 브라우저가 예전에 받아 둔 파일을 쓰지 않는다
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("{{v}}", STARTED))


@app.middleware("http")
async def no_stale_static(request: Request, call_next):
    """화면 파일을 고친 뒤에도 브라우저가 예전 파일을 쓰지 않게, 매번 바뀌었는지 확인하게 한다."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
