"""기획서에 넣는 실데이터 차트(SVG)를 만든다.

사용법: python tools/make_plan_charts.py
프로그램에 든 공개 데이터로 Agent 도구(trend, compute_metric)를 불러 값을 얻고 docs/assets/finding_*.svg 로 저장한다.
값은 모두 반출 가드를 거친 응용집계(증감률·비중·지수)다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["GOLMOK_OFFLINE"] = "1"

from golmok.app import con, describe, lock  # noqa: E402
from golmok.tools import Toolbox  # noqa: E402

OUT = ROOT / "docs" / "assets"
INK, SOFT, FAINT, LINE = "#1b2a3b", "#55677d", "#8796a8", "#d5dfe8"
SPEND, VISIT = "#2a78d6", "#eb6834"   # 소비 = 파랑, 방문객 = 주황 (두 차트에서 같은 색)
FONT = "'Apple SD Gothic Neo','Malgun Gothic',sans-serif"
MONTH = "202609"


def svg(width: int, height: int, body: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="{FONT}">'
            + "".join(body) + "</svg>")


def text(x, y, value, size=12, fill=INK, anchor="start", weight=400, halo=False) -> str:
    ring = ' paint-order="stroke" stroke="#fff" stroke-width="5" stroke-linejoin="round"' if halo else ""   # 선 위에 놓이는 글자는 흰 테두리로 띄운다
    return f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" font-weight="{weight}"{ring}>{value}</text>'


def udo_trend(toolbox: Toolbox) -> dict:
    """우도면: 외국인 방문객과 소비의 전년 같은 달 대비 증감 12개월."""
    rows = toolbox.execute("trend", {"places": ["우도면"], "segment": "외국인", "until": MONTH, "months": 12})["rows"]
    width, height, left, right, top, bottom = 870, 236, 54, 24, 36, 30
    low, high = -100, 300
    x = lambda i: left + (width - left - right) * i / (len(rows) - 1)
    y = lambda v: top + (height - top - bottom) * (high - v) / (high - low)
    body = []
    for tick in (-100, 0, 100, 200, 300):
        body.append(f'<line x1="{left}" x2="{width - right}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" stroke="{INK if tick == 0 else LINE}" stroke-width="{1 if tick == 0 else 0.8}"/>')
        body.append(text(left - 8, y(tick) + 4, f"{tick:+d}%" if tick else "0%", 11, FAINT, "end"))
    for i, row in enumerate(rows):
        label = f"{int(row['month'][4:])}월" if i else f"{row['month'][:4]}.{int(row['month'][4:])}"
        if row["month"].endswith("01"):
            label = f"{row['month'][:4]}.1"
        body.append(text(x(i), height - 12, label, 11, FAINT, "middle"))
    for key, color in (("visitor_yoy_pct", VISIT), ("spend_yoy_pct", SPEND)):
        points = " ".join(f"{x(i):.1f},{y(row[key]):.1f}" for i, row in enumerate(rows))
        body.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>')
    by_month = {row["month"]: (i, row) for i, row in enumerate(rows)}
    # (달, 값, 색, 이름, 글자 위치: -1 = 점 왼쪽, 0 = 점 위/아래 가운데, 세로 이동). 선과 겹치지 않는 자리만 고른다
    marks = [("202608", "visitor_yoy_pct", VISIT, "방문객", -1, -10), ("202608", "spend_yoy_pct", SPEND, "소비", 0, 19),
             ("202602", "spend_yoy_pct", SPEND, "소비", 0, -10)]
    for month, key, color, name, side, dy in marks:
        i, row = by_month[month]
        body.append(f'<circle cx="{x(i):.1f}" cy="{y(row[key]):.1f}" r="4.5" fill="{color}" stroke="#fff" stroke-width="2"/>')
        body.append(text(x(i) + (-9 if side < 0 else 0), y(row[key]) + (4 if side < 0 else dy), f"{name} {row[key]:+.1f}%", 12, INK, "end" if side < 0 else "middle", 700, halo=True))
    # 범례 (선 견본 + 이름). 글자는 잉크색, 색은 견본만
    body += [f'<line x1="{left}" x2="{left + 22}" y1="16" y2="16" stroke="{VISIT}" stroke-width="2.2"/>', text(left + 28, 20, "외국인 방문객 (전년 같은 달 대비)", 12, SOFT),
             f'<line x1="{left + 230}" x2="{left + 252}" y1="16" y2="16" stroke="{SPEND}" stroke-width="2.2"/>', text(left + 258, 20, "외국인 카드 소비 (전년 같은 달 대비)", 12, SOFT)]
    (OUT / "finding_udo.svg").write_text(svg(width, height, body), encoding="utf-8")
    return {row["month"]: row for row in rows}


def share_gap(toolbox: Toolbox) -> list[dict]:
    """외국인 방문객이 많은 동네 8곳: 방문객 비중과 소비 비중, 낙수 지수."""
    rows = toolbox.execute("compute_metric", {"metric": "conversion_index", "month": MONTH, "level": "bjd", "segment": "외국인"})["rows"]
    rows = [r for r in rows if not r.get("비공개") and r.get("visitor_share_pct") is not None]
    rows = sorted(rows, key=lambda r: -r["visitor_share_pct"])[:8]
    width, row_h, top, name_w, value_w = 870, 34, 34, 86, 150
    height = top + row_h * len(rows) + 8
    scale = (width - name_w - value_w - 60) / max(max(r["visitor_share_pct"], r["spend_share_pct"]) for r in rows)
    body = [f'<rect x="{name_w}" y="10" width="14" height="6" rx="3" fill="{VISIT}"/>', text(name_w + 20, 17, "외국인 방문객 중 이 동네 몫", 12, SOFT),
            f'<rect x="{name_w + 200}" y="10" width="14" height="6" rx="3" fill="{SPEND}"/>', text(name_w + 220, 17, "외국인 소비 중 이 동네 몫", 12, SOFT),
            text(width, 17, "낙수 지수 (1 = 제주 평균)", 12, SOFT, "end")]
    for i, r in enumerate(rows):
        y0 = top + row_h * i
        body.append(text(name_w - 10, y0 + 19, r["bjd"], 12.5, INK, "end", 700))
        for j, (key, color) in enumerate((("visitor_share_pct", VISIT), ("spend_share_pct", SPEND))):
            w = max(r[key] * scale, 2)
            body.append(f'<rect x="{name_w}" y="{y0 + 5 + j * 11}" width="{w:.1f}" height="8" rx="4" fill="{color}"/>')
            body.append(text(name_w + w + 6, y0 + 12.5 + j * 11, f"{r[key]:.1f}%", 10.5, SOFT))
        index = r["conversion_index"]
        body.append(text(width, y0 + 20, f"{index:.2f}", 14, INK, "end", 800))
        body.append(text(width - 46, y0 + 20, "돈이 덜 떨어짐" if index < 0.8 else ("돈이 더 떨어짐" if index > 1.2 else "평균 수준"), 10.5, FAINT, "end"))
        body.append(f'<line x1="0" x2="{width}" y1="{y0 + row_h}" y2="{y0 + row_h}" stroke="{LINE}" stroke-width="0.6"/>')
    (OUT / "finding_share.svg").write_text(svg(width, height, body), encoding="utf-8")
    return rows


def main() -> None:
    toolbox = Toolbox(con, lock, describe)
    trend = udo_trend(toolbox)
    rows = share_gap(toolbox)
    print("우도 2026.8:", trend["202608"]["visitor_yoy_pct"], trend["202608"]["spend_yoy_pct"], "| 2026.9:", trend["202609"]["visitor_yoy_pct"], trend["202609"]["spend_yoy_pct"])
    for r in rows:
        print(r["bjd"], r["visitor_share_pct"], r["spend_share_pct"], r["conversion_index"])


if __name__ == "__main__":
    main()
