"""Agent 평가셋 실행.

사용법:
    python tools/run_eval.py                 # 기본 모드(API 없이)로 전체 문항
    python tools/run_eval.py --online        # 실제 Claude 호출로 '실제 호출 문항'만 (요금 발생)
    python tools/run_eval.py --online --ids 2,4

프로그램에 든 공개 데이터만 넣은 상태로 질문을 돌리고, 문항마다 아래를 검사한다.
- 지표: 기대한 지표(또는 도구) 중 하나 이상을 계산했는가
- 동네: 기대한 동네 이름이 모두 답에 있는가
- 달: 질문에 적힌 달을 도구에 넘겼는가
- 업종: 기대한 업종 앞자리로 계산했는가
- 포함: 기대한 문구가 답에 있는가
- 수치: 답의 % · 소수 수치가 모두 계산 결과에서 확인되는가
결과는 docs/assets/eval_results_<offline|online>.json 에 남기고 표로 출력한다. 질문과 답 전문은 json 에 들어간다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

parser = argparse.ArgumentParser()
parser.add_argument("--online", action="store_true", help="실제 Claude 호출 (요금 발생)")
parser.add_argument("--ids", default="", help="돌릴 문항 번호 (쉼표 구분)")
args = parser.parse_args()
if not args.online:
    os.environ["GOLMOK_OFFLINE"] = "1"

from golmok import agent  # noqa: E402  (app 을 불러오면 공개 데이터가 적재된다)
from golmok.app import con, describe, lock  # noqa: E402
from golmok.tools import SCAN_METRICS, Toolbox  # noqa: E402

# 공개 단가 (USD / 백만 토큰). 결과설명자료와 같은 기준
PRICE = {"input_tokens": 4.0, "output_tokens": 20.0, "cache_creation_input_tokens": 5.0, "cache_read_input_tokens": 0.20}
# 도구 → 그 도구가 사실상 계산하는 지표 (기대 지표 대조용)
TOOL_COVERS = {"scan_issues": set(SCAN_METRICS), "place_summary": {"conversion_index", "sales_yoy", "store_yield", "place_summary"},
               "trend": {"sales_yoy", "trend"}, "compare_periods": {"compare_periods"}}


class RecordingToolbox(Toolbox):
    def __init__(self):
        super().__init__(con, lock, describe)
        self.calls: list[dict] = []

    def execute(self, name, tool_input):
        self.calls.append({"tool": name, "input": tool_input})
        return super().execute(name, tool_input)


def covered(calls: list[dict]) -> set[str]:
    found = set()
    for call in calls:
        if call["tool"] in ("compute_metric", "compare_periods"):
            found.add(call["input"].get("metric"))
        found |= TOOL_COVERS.get(call["tool"], set())
    return found


def check(item: dict, answer: dict, calls: list[dict]) -> dict:
    expect, text = item["기대"], answer["text"]
    results = {"지표": bool(set(expect["지표"]) & covered(calls))}
    if expect.get("동네"):
        results["동네"] = all(name in text for name in expect["동네"])
    if expect.get("달"):
        results["달"] = any(call["input"].get("month") == expect["달"] or call["input"].get("month_a") == expect["달"] or call["input"].get("month_b") == expect["달"] for call in calls)
    if expect.get("업종"):
        results["업종"] = any(str(call["input"].get("biz_prefix") or "").startswith(expect["업종"]) for call in calls)
    if expect.get("답에 포함"):
        results["포함"] = all(word in text for word in expect["답에 포함"])
    results["수치"] = answer["checked"]["unverified"] == 0
    return results


def cost(usage: dict) -> float:
    return sum(usage.get(key, 0) * price / 1e6 for key, price in PRICE.items())


def main() -> None:
    spec = json.loads((ROOT / "tools" / "eval_set.json").read_text(encoding="utf-8"))
    ids = [int(x) for x in args.ids.split(",") if x] or (spec["실제 호출 문항"] if args.online else [item["id"] for item in spec["문항"]])
    items = [item for item in spec["문항"] if item["id"] in ids]
    mode = "online" if args.online else "offline"
    rows = []
    for item in items:
        toolbox = RecordingToolbox()
        started = time.time()
        steps, answer = [], None
        for event in agent.run(item["질문"], toolbox):
            if event["type"] == "step":
                steps.append(event["label"])
            elif event["type"] == "answer":
                answer = event
        elapsed = time.time() - started
        results = check(item, answer, toolbox.calls)
        rows.append({
            "id": item["id"], "질문": item["질문"], "확인점": item["확인점"], "mode": answer["mode"], "note": answer["note"],
            "검사": results, "통과": all(results.values()), "도구 호출": toolbox.calls, "단계": steps,
            "수치": answer["checked"], "확인 못 한 수치": answer["unverified"], "사용량": answer["usage"], "비용 USD": round(cost(answer["usage"]), 3),
            "초": round(elapsed, 1), "답": answer["text"],
        })
        marks = " ".join(f"{k}:{'O' if v else 'X'}" for k, v in results.items())
        print(f"[{item['id']:2d}] {'통과' if rows[-1]['통과'] else '실패'}  {marks}  도구 {len(toolbox.calls)}회  {elapsed:.0f}초  ${rows[-1]['비용 USD']:.2f}  {item['질문']}", flush=True)
    out = ROOT / "docs" / "assets" / f"eval_results_{mode}.json"
    out.write_text(json.dumps({"mode": mode, "model": agent.MODEL if args.online else None, "effort": agent.EFFORT if args.online else None,
                               "통과": sum(r["통과"] for r in rows), "문항": len(rows), "비용 USD": round(sum(r["비용 USD"] for r in rows), 3), "결과": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{sum(r['통과'] for r in rows)}/{len(rows)} 통과, 비용 ${sum(r['비용 USD'] for r in rows):.2f} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
