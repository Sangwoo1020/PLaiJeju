"""공개 인터넷 데모 서버 보호: GOLMOK_PUBLIC_DEMO=1 일 때만 데이터 변경을 막고 질문 수를 제한한다."""
import json

import golmok.app as server
from fastapi.testclient import TestClient


def test_guard_is_off_by_default():
    assert server.PUBLIC_DEMO is False   # 센터 PC·제출본에서는 꺼져 있어야 한다


def test_public_demo_blocks_data_changes_and_limits_questions(monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_DEMO", True)
    monkeypatch.setattr(server, "IP_LIMIT", 1)
    monkeypatch.setattr(server, "DAILY_LIMIT", 5)
    server.demo_usage.update(day="", total=0, by_ip={})
    monkeypatch.setenv("GOLMOK_OFFLINE", "1")
    client = TestClient(server.app)
    assert client.post("/api/load-mock").status_code == 403
    assert client.post("/api/upload?filename=a.csv", content=b"a,b\n1,2\n").status_code == 403
    first = client.post("/api/ask", json={"question": "제주 전체 외국인 소비는?"})
    assert first.status_code == 200
    second = client.post("/api/ask", json={"question": "또 물어볼게요"})
    event = json.loads(second.text.strip().splitlines()[-1])
    assert event["type"] == "answer" and "하루에 1건" in event["text"]
