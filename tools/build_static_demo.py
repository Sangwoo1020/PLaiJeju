"""보기 전용 데모 사이트(GitHub Pages 용)를 만든다. 앱 실행에는 쓰이지 않는다.

서버 없이 정적 파일만 올릴 수 있는 곳에서 화면을 보여 주기 위해, 프로그램에 든 공개 실데이터로
서버 응답(/api/status, /api/diagnose, /api/summary, /api/place)을 미리 계산해 JSON 파일로 저장하고,
화면의 fetch 를 그 파일로 돌리는 작은 스크립트(demo/shim.js)를 끼워 넣는다.

Agent 질문은 서버와 API 키가 있어야 하므로, 평가셋 10문항을 실제 Claude 로 답한 기록
(docs/assets/eval_results_online.json)을 그대로 다시 보여 준다. 화면 이동과 보고서는 그때 Agent 가
부른 도구 호출을 같은 데이터로 다시 실행해 만든다(API 를 부르지 않는다). 자동 브리핑은 API 없이
도는 기본 모드 답이다.

    python tools/build_static_demo.py <출력 폴더> [--base /저장소이름/] [--domain 도메인]

--base 는 사이트가 놓일 경로다. github.io/PLaiJeju/ 처럼 저장소 이름이 붙으면 /PLaiJeju/ 를 준다.

출력 폴더에는 공개 데이터 원본(CSV)을 넣지 않고, 화면이 쓰는 응용집계 결과만 들어간다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["GOLMOK_OFFLINE"] = "1"   # 데모 만들 때는 API 를 절대 부르지 않는다 (브리핑은 기본 모드)
os.environ["ANTHROPIC_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import golmok.app as server  # noqa: E402
from golmok import agent  # noqa: E402
from golmok.report import ReportData  # noqa: E402
from golmok.tools import Toolbox  # noqa: E402

MONTHS = 3                                    # 최근 몇 달을 미리 계산할지
BIZ = ["", "I56", "G47130", "I55", "G47"]     # 모든 업종, 음식점·카페·주점, 면세점, 숙박, 소매
DEMO_EXAMPLES = [1, 4, 2, 3]                  # 질문창 예시 단추로 쓸 평가셋 문항 번호


def main(out: Path, base: str = "/", domain: str | None = None) -> None:
    if out.exists():
        shutil.rmtree(out)
    data = out / "demo-data"
    (data / "d").mkdir(parents=True)
    (data / "reports").mkdir()
    index: dict[str, str] = {}

    def save(key: str, body: bytes | dict, suffix: str = ".json") -> str:
        name = f"d/{len(index):04d}{suffix}"
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str).encode()
        (data / name).write_bytes(raw)
        index[key] = name
        return name

    with TestClient(server.app) as client:
        status = client.get("/api/status").json()
        months = status["months"][-MONTHS:]
        status["months"] = months             # 미리 계산한 달만 고를 수 있게
        status["mock"] = False
        save("status", status)
        latest = months[-1]

        def diagnose(month: str, level: str, biz: str, segment: str) -> dict:
            key = f"diagnose|{month}|{level}|{biz}|{segment}"
            if key not in index:
                body = client.post("/api/diagnose", json={"month": month, "level": level, "biz": biz, "segment": segment}).json()
                save(key, body)
                return body
            return json.loads((data / index[key]).read_bytes())

        places: set[str] = set()
        for month in months:
            save(f"summary|{month}", client.get("/api/summary", params={"month": month}).json())
            for level in status["levels"]:
                for segment in status["segments"]:
                    for biz in BIZ:
                        body = diagnose(month, level, biz, segment)
                        if level == "bjd":
                            for metric in body["metrics"]:
                                for row in metric.get("rows") or []:
                                    if row.get("bjd"):
                                        places.add(row["bjd"])
        print(f"진단 {sum(k.startswith('diagnose') for k in index)}개 조합")

        # 동네 카드·시작 화면 장소 카드의 최근 흐름
        app_js = (ROOT / "src/golmok/static/app.js").read_text(encoding="utf-8")
        old_jeju = json.loads(re.search(r"const OLD_JEJU = (\[[^\]]*\]);", app_js).group(1))
        groups = {""}
        for names in re.findall(r"names: (\[[^\]]*\]|OLD_JEJU)", app_js):
            groups.add(",".join(old_jeju if names == "OLD_JEJU" else json.loads(names)))
        groups |= places
        for names in sorted(groups):
            save(f"place|{names}", client.get("/api/place", params={"names": names}).json())
        print(f"장소 {len(groups)}곳")

        # 실제 Claude 답 다시 보여 주기: 기록된 도구 호출을 같은 데이터로 다시 실행해 화면 조건과 보고서를 만든다
        online = json.loads((ROOT / "docs/assets/eval_results_online.json").read_text(encoding="utf-8"))
        answers = {}
        for item in online["결과"]:
            toolbox = Toolbox(server.con, server.lock, server.describe)
            for call in item["도구 호출"]:
                try:
                    toolbox.execute(call["tool"], call["input"])
                except Exception as error:   # 기록 당시와 데이터가 같아 실패하지 않지만, 실패해도 데모는 만든다
                    print("도구 다시 실행 실패:", item["id"], call["tool"], error)
            view = toolbox.view
            if view:
                diagnose(view["month"], view["level"], view.get("biz") or "", view["segment"])
            report_id = f"q{item['id']}"
            server.reports[report_id] = ReportData(item["질문"], item["답"], "claude", False, [], item.get("note") or "", sections=toolbox.sections)
            zipped = client.get(f"/api/report/{report_id}")
            (data / "reports" / f"{report_id}.zip").write_bytes(zipped.content)
            answers[item["질문"]] = {
                "steps": [_step(label) for label in item["단계"]],
                "answer": {
                    "type": "answer", "mode": "claude", "text": item["답"], "unverified": item.get("확인 못 한 수치") or [],
                    "sources": toolbox.sources, "view": view, "report_id": report_id, "turn": 1,
                    "note": f"데모 사이트: {online['model']} 로 실제로 받은 답(2026-11-17 평가셋)을 다시 보여 줍니다. 답의 수치 {item['수치']['numbers']}개는 계산 결과와 모두 대조했습니다.",
                },
            }
        save("answers", answers)
        examples = [next(i["질문"] for i in online["결과"] if i["id"] == n) for n in DEMO_EXAMPLES]

        # 자동 브리핑: API 없이 도는 기본 모드
        toolbox = Toolbox(server.con, server.lock, server.describe)
        brief = list(agent.run_brief(toolbox, latest))
        for event in brief:
            if event["type"] == "answer":
                server.reports["brief"] = ReportData("자동 브리핑", event["text"], event["mode"], False, event["unverified"], event["note"], sections=toolbox.sections)
                (data / "reports" / "brief.zip").write_bytes(client.get("/api/report/brief").content)
                event.update(report_id="brief", title=f"{latest[:4]}년 {int(latest[4:])}월 관광 낙수 진단 브리핑",
                             note="데모 사이트: API 없이 도는 기본 모드(규칙 기반) 브리핑입니다. 실제 프로그램에서는 Claude 가 스스로 문제 동네를 파고듭니다.")
        save("brief", brief)

    (data / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    # 화면 파일: 원본 그대로 복사하고, 질문 예시만 데모에서 답할 수 있는 문항으로 바꾼다
    shutil.copytree(ROOT / "src/golmok/static", out / "static", ignore=shutil.ignore_patterns(".DS_Store"))
    js = out / "static/app.js"
    js.write_text(re.sub(r"const EXAMPLES = \[.*?\];", "const EXAMPLES = " + json.dumps(examples, ensure_ascii=False) + ";", js.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    (out / "demo").mkdir()
    shutil.copy(ROOT / "tools/demo_shim.js", out / "demo/shim.js")
    html = (ROOT / "src/golmok/static/index.html").read_text(encoding="utf-8").replace("{{v}}", "demo")
    html = html.replace('<link rel="icon"', f'<script src="{base}demo/shim.js"></script>\n  <link rel="icon"', 1)
    if base != "/":   # 화면 파일의 /static/ 주소를 사이트 경로 아래로 옮긴다 (외부 라이브러리 vendor/ 는 그대로)
        html = html.replace('"/static/', f'"{base}static/')
        for path in [*out.glob("static/*.js"), *out.glob("static/*.css")]:
            text = path.read_text(encoding="utf-8")
            path.write_text(re.sub(r'(["\'`(])/static/', lambda m: m.group(1) + base + "static/", text), encoding="utf-8")
    html = html.replace("<title>PLaiJeju · 제주 관광 낙수 진단</title>", "<title>PLaiJeju 데모 · 제주 관광 낙수 진단</title>")
    (out / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(ROOT / "src/golmok/static/logo.svg", out / "favicon.svg")
    (out / ".nojekyll").write_text("")
    (out / "404.html").write_text(html, encoding="utf-8")
    if domain:
        (out / "CNAME").write_text(domain + "\n")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"완성: {out} ({size / 1e6:.1f}MB, 응답 {len(index)}개)")


def _step(label: str) -> dict:
    if label.startswith("계획"):
        return {"type": "step", "kind": "plan", "label": label}
    if label.startswith("수치 검증"):
        return {"type": "step", "kind": "check", "ok": "찾지 못한" not in label, "label": label}
    if label in agent.TOOL_STEP_NAMES.values() or re.search(r"(계산|확인|요약|비교|검색|훑기)(:|$| )", label):
        return {"type": "step", "label": label}
    return {"type": "step", "kind": "plan", "label": label}   # 도구 사이에 Claude 가 남긴 메모


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?", default=str(ROOT / "dist/demo_site"))
    parser.add_argument("--base", default="/")
    parser.add_argument("--domain")
    args = parser.parse_args()
    main(Path(args.out).resolve(), args.base if args.base.endswith("/") else args.base + "/", args.domain)
