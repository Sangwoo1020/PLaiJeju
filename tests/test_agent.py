import json
import threading
from pathlib import Path
from types import SimpleNamespace

import anthropic
import duckdb
import pytest

from golmok import agent, metrics
from golmok.guard import GuardedTable
from golmok.loader import load_center_csv
from golmok.tools import TOOLS, Toolbox
from test_metrics import load


def describe():
    return {"가상데이터": True, "데이터": [{"이름": "카드 이용 현황(읍면동)_v02", "months": ["202510", "202610"]}]}


@pytest.fixture
def toolbox(tmp_path):
    con = duckdb.connect(":memory:")
    load(con, tmp_path, "1-08", [
        {"month": "202510", "use_amt": 1_000_000},
        {"month": "202610", "use_amt": 800_000},
        {"month": "202510", "bjd": "구좌읍", "dstrct_se": "제주시 동부", "use_amt": 1_000_000},
        {"month": "202610", "bjd": "구좌읍", "dstrct_se": "제주시 동부", "use_amt": 1_100_000},
        {"month": "202510", "bjd": "한경면", "user_cnt": 5, "use_amt": 100_000},
        {"month": "202610", "bjd": "한경면", "user_cnt": 6, "use_amt": 300_000},
    ])
    return Toolbox(con, threading.Lock(), describe)


class FakeClient:
    """정해 둔 응답을 차례로 돌려주고, 받은 요청을 기록한다."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(*content, stop="end_turn"):
    return SimpleNamespace(content=list(content), stop_reason=stop, usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=50))


def text(value):
    return SimpleNamespace(type="text", text=value)


def call(tool_input, name="compute_metric", id="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=id)


METRIC = {"metric": "sales_yoy", "month": "202610", "level": "bjd", "biz_prefix": None, "segment": None, "focus": []}


def test_tool_schemas_are_strict_and_complete():
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert tool["strict"] and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_tool_result_is_guarded_and_hides_support(toolbox):
    result = toolbox.execute("compute_metric", METRIC)
    rows = {row["bjd"]: row for row in result["rows"]}
    assert rows["한림읍"]["yoy_pct"] == -20.0
    assert rows["한경면"]["yoy_pct"] is None and rows["한경면"]["비공개"]
    assert not any(key in rows["한림읍"] for key in ("users_now", "use_amt", "amt_now"))


def test_focus_narrows_rows_and_reports_unknown_place(toolbox):
    assert [r["bjd"] for r in toolbox.execute("compute_metric", {**METRIC, "focus": ["구좌"]})["rows"]] == ["구좌읍"]
    assert toolbox.view["focus"]["bjd"] == "구좌읍"
    wide = toolbox.execute("compute_metric", {**METRIC, "focus": ["없는동네"]})
    assert wide["rows"] == [] and "없는동네" in wide["참고"]


def test_missing_data_is_a_normal_result_that_names_the_download(toolbox):
    result = toolbox.execute("compute_metric", {**METRIC, "metric": "conversion_index"})
    assert "외국인 유동인구" in result["missing"] and result["필요한 달"] == ["202610"]
    assert result["데이터"].startswith("4-15")


def test_search_finds_center_dataset(toolbox):
    found = toolbox.execute("search_datasets", {"keyword": "탐나는전"})
    assert found["결과"][0]["번호"] == "1-32" and found["결과"][0]["반출"] == "불가"


def test_agent_loop_runs_tools_and_returns_verified_answer(toolbox):
    client = FakeClient([
        reply(text("계획: 매출 증감률부터 봅니다."), call(METRIC), stop="tool_use"),
        reply(text("한림읍 매출은 전년보다 20.0% 줄었습니다.")),
    ])
    events = list(agent.run_claude("한림읍 어때?", toolbox, client))
    assert events[0] == {"type": "step", "kind": "plan", "label": "계획: 매출 증감률부터 봅니다."}
    assert events[1] == {"type": "step", "label": "매출 증감률 계산"}
    assert events[2]["kind"] == "check" and events[2]["ok"] and "1개 중 1개" in events[2]["label"]
    final = events[-1]
    assert final["checked"] == {"numbers": 1, "unverified": 0, "corrected": 0}
    assert final["mode"] == "claude" and final["unverified"] == []
    assert final["view"]["tab"] == 0 and final["usage"]["output_tokens"] == 40
    assert final["sources"] == ["제주빅데이터센터, 카드 이용 현황(읍면동)_v02(2025.10·2026.10)"]

    second = client.requests[1]
    assert second["betas"] == [agent.FALLBACK_BETA] and second["fallbacks"] == "default"
    assert second["output_config"] == {"effort": agent.EFFORT}
    tool_message = second["messages"][-1]
    assert tool_message["role"] == "user" and tool_message["content"][0]["tool_use_id"] == "t1"
    sent = json.loads(tool_message["content"][0]["content"])
    assert sent["지표"].startswith("매출 증감률")


def test_all_tool_results_go_back_in_one_message(toolbox):
    client = FakeClient([
        reply(call(METRIC, id="a"), call({}, name="list_data", id="b"), stop="tool_use"),
        reply(text("끝")),
    ])
    list(agent.run_claude("질문", toolbox, client))
    assert [r["tool_use_id"] for r in client.requests[1]["messages"][-1]["content"]] == ["a", "b"]


def test_bad_tool_input_comes_back_as_error_result(toolbox):
    client = FakeClient([reply(call({**METRIC, "metric": "없음"}), stop="tool_use"), reply(text("끝"))])
    list(agent.run_claude("질문", toolbox, client))
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "없는 지표" in result["content"]


def test_numbers_without_evidence_are_sent_back_once_then_flagged(toolbox):
    wrong = reply(text("한림읍은 20.0% 줄었고 업계 평균은 33.3%, 지수는 1.27입니다. 2026.10 기준입니다."))
    client = FakeClient([reply(call(METRIC), stop="tool_use"), wrong, wrong])
    events = list(agent.run_claude("질문", toolbox, client))
    correction = client.requests[2]["messages"][-1]
    assert correction["role"] == "user" and "1.27, 33.3" in correction["content"]   # 근거 없는 수치를 되돌려 보낸다
    assert [e["ok"] for e in events if e.get("kind") == "check"] == [False, False]
    final = events[-1]
    assert final["unverified"] == ["1.27", "33.3"] and final["checked"]["corrected"] == 1


def test_model_fixes_numbers_after_correction(toolbox):
    client = FakeClient([reply(call(METRIC), stop="tool_use"), reply(text("한림읍은 21% 줄었습니다.")), reply(text("한림읍은 20.0% 줄었습니다."))])
    final = list(agent.run_claude("질문", toolbox, client))[-1]
    assert final["unverified"] == [] and final["text"] == "한림읍은 20.0% 줄었습니다." and final["checked"]["corrected"] == 1


def test_follow_up_questions_carry_earlier_turns(toolbox):
    client = FakeClient([reply(text("네."))])
    history = [{"q": "한림읍 외국인 매출 어때?", "a": "한림읍은 줄었습니다."}]
    list(agent.run_claude("그럼 카페는?", toolbox, client, history))
    sent = client.requests[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"] and sent[0]["content"].startswith("한림읍")


def test_basic_mode_follow_up_keeps_the_place(toolbox, monkeypatch):
    monkeypatch.setattr(agent, "make_client", lambda: None)
    final = list(agent.run("그럼 카페는?", toolbox, [{"q": "구좌 어때?", "a": "..."}]))[-1]
    assert "구좌읍" in final["text"]


def test_refusal_and_endless_loop_fall_back_to_basic_mode(toolbox, monkeypatch):
    monkeypatch.setattr(agent, "make_client", lambda: FakeClient([reply(stop="refusal")]))
    final = list(agent.run("한림 카페 어때?", toolbox))[-1]
    assert final["mode"] == "offline" and "답하지 않았습니다" in final["note"]

    monkeypatch.setattr(agent, "make_client", lambda: FakeClient([reply(call(METRIC), stop="tool_use") for _ in range(agent.MAX_STEPS)]))
    final = list(agent.run("한림 카페 어때?", toolbox))[-1]
    assert final["mode"] == "offline" and "단계" in final["note"]


def test_no_key_uses_basic_mode(toolbox, monkeypatch):
    monkeypatch.setattr(agent, "make_client", lambda: None)
    final = list(agent.run("2026년 10월 구좌 내국인 관광객 상황 어때?", toolbox))[-1]
    assert final["mode"] == "offline" and "API 키" in final["note"]
    assert "가상 데이터" in final["text"] and "구좌읍: +10.0%" in final["text"]
    assert "내국인 유동인구(읍면동)_v02" in final["text"]   # 낙수 지수에 필요한 데이터를 안내한다
    assert final["unverified"] == []


def test_connection_failure_uses_basic_mode(toolbox, monkeypatch):
    request = SimpleNamespace(method="POST", url="https://api.anthropic.com/v1/messages", headers={})
    error = anthropic.APIConnectionError(request=request)
    monkeypatch.setattr(agent, "make_client", lambda: FakeClient([error]))
    final = list(agent.run("구좌 어때?", toolbox))[-1]
    assert final["mode"] == "offline" and "보안 정책" in final["note"]


def test_basic_mode_says_when_place_has_no_data_instead_of_listing_others(toolbox, monkeypatch):
    monkeypatch.setattr(agent, "make_client", lambda: None)
    final = list(agent.run("서귀동 어때?", toolbox))[-1]
    assert "서귀동 의 결과가 없습니다" in final["text"] and "구좌읍" not in final["text"]


def test_basic_mode_understands_words(toolbox):
    assert agent._find_place("한림 카페 어때") == "한림읍"
    assert agent._find_place("제주시 서부 상황") == "제주시 서부"
    assert agent._find_place("대천동 외국인 어때") == "대천동"   # 행정동 이름도 알아듣는다
    assert agent._find_place("이도2동 숙박은?") == "이도2동"
    assert agent._find_month("2025년 10월 매출", ["202610"]) == "202510"
    assert agent._find_month("요즘 어때", ["202510", "202610"]) == "202610"


def test_view_follows_first_metric(toolbox):
    toolbox.execute("compute_metric", {**METRIC, "segment": "내국인관광객"})
    toolbox.execute("compute_metric", {**METRIC, "segment": "도민"})
    assert toolbox.view["segment"] == "내국인관광객" and len(toolbox.sections) == 2


def test_default_segments_fill_in_when_model_leaves_them_empty(toolbox):
    result = toolbox.execute("compute_metric", {**METRIC, "metric": "spend_share"})
    assert result["지표"] == "소비 비중(외국인)"
    assert toolbox.sections[-1].extra is None
    result = toolbox.execute("compute_metric", {**METRIC, "metric": "gap_fill"})
    assert result["지표"] == "내국인 공백 메우기" and toolbox.sections[-1].extra[0] == "fill_pct"


def test_markdown_bold_is_removed_from_answers(toolbox):
    client = FakeClient([reply(call(METRIC), stop="tool_use"), reply(text("**결론**\n한림읍은 **20.0%** 줄었습니다."))])
    final = list(agent.run_claude("질문", toolbox, client))[-1]
    assert final["text"] == "결론\n한림읍은 20.0% 줄었습니다."


def test_basic_mode_compares_two_places(toolbox):
    """기본 모드도 동네 둘을 물으면 둘 다 계산해 나란히 답한다 (평가셋 6번)."""
    events = list(agent.run_offline("구좌읍과 한경면 중 어디가 더 늘었나요?", toolbox, "키 없음"))
    answer = events[-1]["text"]
    assert "구좌읍·한경면" in answer and "- 구좌읍" in answer
    assert agent._find_places("연동과 중문동 중 어디인가요?") == ["연동", "중문동"]


def test_basic_mode_survives_a_metric_that_cannot_be_computed(toolbox):
    """기본 모드에서 한 지표가 계산되지 않아도(공개 소비에 없는 업종) 답은 끝까지 나온다 (Windows 실행 시험 준비 중 발견)."""
    events = list(agent.run_offline("크루즈 효과를 면세점 업종으로 보정해서 보여줘", toolbox, "키 없음"))
    assert events[-1]["type"] == "answer" and events[-1]["text"]
    assert agent._excluded("크루즈 들어온 달에 면세점 말고 동네 가게도 늘었어?", "면세")
    assert not agent._excluded("면세점 매출이 늘었어?", "면세")



def test_unexpected_error_still_ends_with_an_answer(toolbox, monkeypatch):
    """기본 모드 계산에서 예상 못 한 오류가 나도 화면에 빈 답이 남지 않게 안내 답을 내보낸다."""
    monkeypatch.setattr(agent, "make_client", lambda: None)

    def broken(name, arguments):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(toolbox, "execute", broken)
    final = list(agent.run("구좌 어때?", toolbox))[-1]
    assert final["type"] == "answer" and final["text"].startswith("답을 만드는 도중 오류가 나서")
    assert final["unverified"] == [] and "ZeroDivisionError" in final["note"]


def test_unexpected_error_in_a_tool_is_reported_to_claude(toolbox, monkeypatch):
    """Claude 가 부른 도구에서 예상 못 한 오류가 나면 오류 내용을 돌려주고 대화를 이어 간다."""
    client = FakeClient([reply(call(METRIC), stop="tool_use"), reply(text("결론\n계산할 수 없었습니다."))])
    monkeypatch.setattr(agent, "make_client", lambda: client)

    def broken(name, arguments):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(toolbox, "execute", broken)
    final = list(agent.run("구좌 어때?", toolbox))[-1]
    assert final["mode"] != "offline" and "계산할 수 없었습니다" in final["text"]
