"""평가셋 실행 결과(json)를 docs/평가셋_결과.md 로 정리한다.

사용법: python tools/eval_report.py
docs/assets/eval_results_offline.json (필수), eval_results_online.json (있으면) 을 읽는다.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
CHECK_NAMES = {"지표": "지표 선택", "동네": "동네", "달": "달", "업종": "업종", "포함": "안내 문구", "수치": "수치 대조"}


def load(mode: str) -> dict | None:
    path = ASSETS / f"eval_results_{mode}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def table(data: dict) -> list[str]:
    lines = ["| 번호 | 질문 | 확인점 | 검사 | 도구 호출 | 수치 확인 | 결과 |", "|---|---|---|---|---|---|---|"]
    for r in data["결과"]:
        marks = ", ".join(f"{CHECK_NAMES[k]} {'O' if v else 'X'}" for k, v in r["검사"].items())
        numbers = f"{r['수치']['numbers']}개 중 {r['수치']['numbers'] - r['수치']['unverified']}개"
        lines.append(f"| {r['id']} | {r['질문']} | {r['확인점']} | {marks} | {len(r['도구 호출'])}회 | {numbers} | {'통과' if r['통과'] else '실패'} |")
    return lines


def tools_used(r: dict) -> str:
    names = []
    for call in r["도구 호출"]:
        label = call["tool"] if call["tool"] != "compute_metric" else call["input"].get("metric", "")
        extra = call["input"].get("biz_prefix") or ""
        names.append(f"{label}({extra})" if extra else label)
    return " → ".join(names)


def main() -> None:
    offline, online = load("offline"), load("online")
    out = ["# 평가셋 실행 결과", "",
           "`tools/eval_set.json` 의 10문항을 `tools/run_eval.py` 로 돌린 결과입니다. 프로그램에 든 공개 데이터(2026년 9월까지)만 넣은 상태이며, "
           "문항마다 기대한 지표를 골랐는지, 동네·달·업종을 맞게 넘겼는지, 안내 문구가 있는지, 답의 % · 소수 수치가 모두 계산 결과에서 확인되는지를 코드가 검사합니다. "
           "답변의 해석이 맞는지는 사람이 읽고 판단해야 하며, 아래 '답변 전문'을 그대로 실었습니다.", ""]
    if online:
        out += [f"## 실제 호출 ({online['model']}, 추론 강도 {online['effort']})", "",
                f"{online['문항']}문항 중 {online['통과']}문항 통과. 총 비용 약 ${online['비용 USD']:.2f} (공개 단가: 입력 $4, 출력 $20, 캐시 쓰기 $5, 캐시 읽기 $0.20 / 백만 토큰).", ""]
        out += table(online)
        out += ["", "| 번호 | 도구 호출 순서 | 걸린 시간 | 요청 수 | 출력 토큰 | 비용 |", "|---|---|---|---|---|---|"]
        for r in online["결과"]:
            u = r["사용량"]
            out.append(f"| {r['id']} | {tools_used(r)} | {r['초']:.0f}초 | {u.get('requests', 0)}회 | {u.get('output_tokens', 0):,} | ${r['비용 USD']:.2f} |")
        out.append("")
    out += [f"## 기본 모드 (API 없이, 규칙 기반)", "", f"{offline['문항']}문항 중 {offline['통과']}문항 통과.", ""]
    out += table(offline)
    failed = [r for r in offline["결과"] if not r["통과"]]
    if failed:
        out += ["", "실패한 문항은 기본 모드의 한계입니다: " + "; ".join(f"{r['id']}번은 {', '.join(CHECK_NAMES[k] for k, v in r['검사'].items() if not v)} 검사에서 걸림" for r in failed) + ". "
                "기본 모드는 질문에서 동네 하나만 뽑아 계산하므로 두 동네 비교는 하지 못합니다."]
    if online:
        out += ["", "## 답변 전문 (실제 호출)", ""]
        for r in online["결과"]:
            out += [f"### {r['id']}. {r['질문']}", "", "진행 단계: " + " → ".join(r["단계"]), "", *[f"> {line}" if line else ">" for line in r["답"].splitlines()], ""]
    (ROOT / "docs" / "평가셋_결과.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("docs/평가셋_결과.md")


if __name__ == "__main__":
    main()
