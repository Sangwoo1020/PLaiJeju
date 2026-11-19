"""진단 보고서 내보내기: PDF 보고서 + PNG 차트 + 반출 신청 참고 문서를 zip 하나로 묶는다.

반출 정책(2026.09.01)에 맞춘 형식이다.
- 카드·포스·유동인구 데이터는 CSV·엑셀 반출이 안 되므로 표 파일을 만들지 않는다.
- 시각화는 PNG 로만 낸다.
- 실리는 값은 전부 반출 가드를 거친 응용집계(증감률·비율·지수)다.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")  # 화면 없는 서버에서 그린다
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

SEA, DOWN, INK, SOFT, FAINT, LINE, OTHERS = "#2a6fdb", "#43b6e3", "#1b2a3b", "#55677d", "#8796a8", "#d9e0e6", "#c5ced6"
CHART_MAX_ROWS = 20    # 차트에는 양 끝 값을 합쳐 이만큼만 그린다
TABLE_MAX_ROWS = 12    # PDF 표에 싣는 행 수
MOCK_NOTICE = "가상 데이터입니다. 수치는 난수로 만든 것이며 실제 통계가 아닙니다."
CARD_LIMIT = "카드 데이터는 비씨카드 결제만 담고 있습니다(비씨카드로 결제한 간편결제 포함). 다른 카드, 현금, 중국 간편결제(알리페이·위챗페이)는 빠져 있어 외국인 소비는 실제보다 적게 잡힐 수 있습니다."

# 운영체제별 한글 글꼴. 센터 PC(Windows)는 맑은 고딕을 쓴다.
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/malgunbd.ttf"),
    ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
]
# 반출 가드가 지우는 근거 컬럼. 보고서에 들어오면 안 된다.
FORBIDDEN_COLUMNS = ("amt", "users", "cnt", "use_amt", "user_cnt", "use_cnt", "visitors", "liters_per_day", "passengers", "foreign_visitors", "domestic_visitors", "arrivals", "sale", "stores", "local_amt", "local_stores")


@dataclass
class MetricSection:
    title: str
    definition: str
    condition: str
    value_key: str
    change_key: str | None
    shape: str  # "diverging" | "share" | "magnitude"
    baseline: float
    unit: str
    notes: list[str]
    sources: list[str]
    rows: list[dict]
    context: list[dict] = field(default_factory=list)  # 특정 지역을 물었을 때 비교용으로 함께 그릴 전체 지역
    extra: tuple | None = None  # 표에 함께 싣는 보조값 (컬럼, 이름, 단위)


@dataclass
class ReportData:
    question: str
    answer: str
    mode: str
    mock: bool
    unverified: list[str]
    note: str = ""
    created: datetime = field(default_factory=datetime.now)
    sections: list[MetricSection] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(source for section in self.sections for source in section.sources))


@lru_cache(maxsize=1)
def fonts() -> tuple[str, str]:
    """(본문 글꼴 파일, 굵은 글꼴 파일). 찾은 글꼴을 reportlab 에 등록한다."""
    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists():
            pdfmetrics.registerFont(TTFont("Body", regular))
            pdfmetrics.registerFont(TTFont("Bold", bold if Path(bold).exists() else regular))
            return regular, bold if Path(bold).exists() else regular
    raise FileNotFoundError("한글 글꼴을 찾지 못했습니다. 맑은 고딕이 설치되어 있는지 확인해 주세요.")


def check_release_safe(data: ReportData) -> None:
    """보고서에 근거 컬럼(단순집계)이 섞이지 않았는지 내보내기 직전에 한 번 더 확인한다."""
    for section in data.sections:
        for row in [*section.rows, *section.context]:
            leaked = [key for key in row if any(key == name or key.startswith(f"{name}_") for name in FORBIDDEN_COLUMNS)]
            if leaked:
                raise ValueError(f"{section.title} 에 반출할 수 없는 컬럼이 있습니다: {', '.join(leaked)}")


def format_value(value: float, section: MetricSection) -> str:
    if section.shape == "category":
        return str(value)
    if section.shape == "share":
        return f"{value:.1f}%"
    if section.shape == "magnitude":
        return f"{value:,.0f}{section.unit}"
    return f"{value:+.1f}%" if section.unit == "%" else f"{value:.2f}"


def format_extra(value, unit: str, key: str = "") -> str:
    """보조값. 증감(키에 yoy)만 부호를 붙이고, 비중·메움률 같은 수준 값은 그대로 쓴다."""
    if value is None:
        return "-"
    if unit == "%":
        return f"{value:+.1f}%" if "yoy" in key or "lift" in key else f"{value:.1f}%"
    return f"{value:.2f}" if isinstance(value, float) and abs(value) < 10 else f"{value:,}"


def place(row: dict) -> str:
    return row.get("bjd") or row.get("emd_nm") or row.get("dstrct_se") or row.get("city") or "제주 전체"


def _shown(section: MetricSection, rows: list[dict] | None = None) -> list[dict]:
    rows = [row for row in (section.rows if rows is None else rows) if row.get(section.value_key) is not None and not row.get("비공개")]
    return sorted(rows, key=lambda row: row[section.value_key], reverse=section.shape in ("share", "magnitude"))


def _withheld(section: MetricSection) -> list[str]:
    return [place(row) for row in section.rows if row.get(section.value_key) is None or row.get("비공개")]


def chart_png(section: MetricSection, mock: bool) -> bytes | None:
    """가로 막대 차트 PNG. 값이 없거나 유형(범주) 지표면 None."""
    if section.shape == "category" or not _shown(section):
        return None
    focused = {place(row) for row in _shown(section)} if section.context else set()
    rows = _shown(section, section.context or section.rows)
    if len(rows) > CHART_MAX_ROWS:   # 양 끝과 물어본 지역을 남긴다
        half = CHART_MAX_ROWS // 2
        rows = [row for index, row in enumerate(rows) if index < half or index >= len(rows) - half or place(row) in focused]
    names = [place(row) for row in rows][::-1]
    values = [row[section.value_key] for row in rows][::-1]
    base = 0 if section.shape in ("share", "magnitude") else section.baseline
    bar_colors = [SEA if section.shape in ("share", "magnitude") or value >= base else DOWN for value in values]
    if focused:   # 물어본 지역만 색을 남기고 나머지는 비교용 회색으로
        bar_colors = [color if name in focused else OTHERS for name, color in zip(names, bar_colors)]

    regular, _ = fonts()
    font = font_manager.FontProperties(fname=regular)
    figure, axes = plt.subplots(figsize=(8, 0.26 * len(rows) + 1.3), dpi=170)
    positions = range(len(rows))
    axes.barh(positions, [value - base for value in values], left=base, height=0.6, color=bar_colors)
    axes.axvline(base, color=FAINT, linewidth=1)
    axes.set_yticks(list(positions), names, fontproperties=font, fontsize=10, color=INK)
    axes.tick_params(axis="y", length=0)
    axes.tick_params(axis="x", colors=SOFT, labelsize=9)
    axes.grid(axis="x", color=LINE, linewidth=0.7)
    axes.set_axisbelow(True)
    for spine in axes.spines.values():
        spine.set_visible(False)

    label_all = len(rows) <= 10  # 막대가 많으면 양 끝만 값을 적고 나머지는 표로 본다
    span = (max(values + [base]) - min(values + [base])) or 1
    for index, value in enumerate(values):
        if label_all or index < 3 or index >= len(values) - 3 or names[index] in focused:
            right = value >= base
            axes.text(value + (0.012 if right else -0.012) * span, index, format_value(value, section), va="center", ha="left" if right else "right", fontproperties=font, fontsize=9, color=INK)
    axes.margins(x=0.14)
    title = f"{section.title}  {section.condition}"
    axes.set_title(title, fontproperties=font, fontsize=11, color=INK, loc="left", pad=12)
    if mock:
        figure.text(0.99, 0.01, "가상 데이터", fontproperties=font, fontsize=9, color="#1a4fa8", ha="right")
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()


def _styles() -> dict[str, ParagraphStyle]:
    fonts()
    base = ParagraphStyle("base", fontName="Body", fontSize=10, leading=16, textColor=colors.HexColor(INK), wordWrap="CJK", alignment=TA_LEFT)
    return {
        "body": base,
        "title": ParagraphStyle("title", parent=base, fontName="Bold", fontSize=20, leading=28),
        "h2": ParagraphStyle("h2", parent=base, fontName="Bold", fontSize=13, leading=20, spaceBefore=12, spaceAfter=4),
        "meta": ParagraphStyle("meta", parent=base, fontSize=8.5, leading=13, textColor=colors.HexColor(SOFT)),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=12),
        "warn": ParagraphStyle("warn", parent=base, fontName="Bold", textColor=colors.HexColor("#1a4fa8"), backColor=colors.HexColor("#e8f1fd"), borderPadding=6, spaceAfter=10, spaceBefore=4),
    }


def _text(text: str, style: ParagraphStyle) -> Paragraph:
    # 일부 글꼴에 없는 빼기 기호(U+2212)는 일반 하이픈으로 바꾼다
    return Paragraph(escape(text.replace("\u2212", "-")).replace("\n", "<br/>"), style)


def _table(section: MetricSection, styles: dict) -> Table | None:
    rows = _shown(section)
    if not rows:
        return None
    if len(rows) > TABLE_MAX_ROWS:
        half = TABLE_MAX_ROWS // 2
        rows = rows[:half] + rows[-half:]
    cell = styles["cell"]
    has_change = bool(section.change_key)
    extra = section.extra
    header = ["지역", section.title.split("(")[0], *(["1년 전 대비"] if has_change else []), *([extra[1]] if extra else []), "신뢰 수준"]
    body = [[Paragraph(escape(text), cell) for text in header]]
    for row in rows:
        change = row.get(section.change_key) if has_change else None
        body.append([
            Paragraph(escape(place(row)), cell),
            Paragraph(format_value(row[section.value_key], section), cell),
            *([Paragraph("" if change is None else f"{change:+.1f}%p", cell)] if has_change else []),
            *([Paragraph(escape(format_extra(row.get(extra[0]), extra[2], extra[0])), cell)] if extra else []),
            Paragraph(escape(str(row.get("신뢰수준", ""))), cell),
        ])
    table = Table(body, colWidths=[56 * mm, 36 * mm, *([30 * mm] if has_change else []), *([30 * mm] if extra else []), 22 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf1f4")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _pdf(data: ReportData, charts: dict[int, bytes]) -> bytes:
    styles = _styles()
    story: list = [_text("제주 관광 낙수 진단 보고서", styles["title"])]
    made = "Claude 분석" if data.mode == "claude" else "기본 규칙 분석"
    story.append(_text(f"{data.created:%Y년 %m월 %d일 %H:%M} 작성 / {made}", styles["meta"]))
    story.append(Spacer(1, 6))
    if data.mock:
        story.append(_text(MOCK_NOTICE, styles["warn"]))
    story.append(_text("질문", styles["h2"]))
    story.append(_text(data.question, styles["body"]))
    story.append(_text("진단 결과", styles["h2"]))
    story.append(_text(data.answer, styles["body"]))
    if data.unverified:
        story.append(Spacer(1, 6))
        story.append(_text(f"계산 결과에서 확인되지 않은 수치가 있습니다: {', '.join(f'{n}%' for n in data.unverified)}. 이 값은 인용하지 마세요.", styles["warn"]))

    for index, section in enumerate(data.sections):
        heading = [_text(section.title, styles["h2"]), _text(f"{section.condition}. 계산식: {section.definition}", styles["meta"])]
        if index in charts:
            image = Image(io.BytesIO(charts[index]))
            scale = 170 * mm / image.imageWidth
            image.drawWidth, image.drawHeight = 170 * mm, image.imageHeight * scale
            story.append(KeepTogether([*heading, Spacer(1, 4), image]))
        else:
            story.extend(heading)
        table = _table(section, styles)
        if table:
            story.extend([Spacer(1, 4), table])
        withheld = _withheld(section)
        extra = [*section.notes, *([f"표본이 적어 비공개: {', '.join(withheld[:12])}{' 외' if len(withheld) > 12 else ''}"] if withheld else [])]
        if len(_shown(section)) > TABLE_MAX_ROWS:
            extra.append("표에는 양 끝 값만 실었습니다.")
        if extra:
            story.extend([Spacer(1, 3), _text(" ".join(extra), styles["meta"])])

    story.append(_text("한계", styles["h2"]))
    story.append(_text(f"- {CARD_LIMIT}\n- 신뢰 수준은 근거 이용자(또는 방문 인원) 수 기준입니다. 100 이상이면 높음, 30 이상이면 보통, 그보다 적으면 낮음이고, 10 미만이면 값을 싣지 않습니다.\n- 낙수 지수·크루즈 기항 효과는 비율과 상관이지 인과가 아닙니다. 카드는 월 단위라 입항일의 효과를 직접 가르지 못합니다.\n- 한 달 비교는 계절 요인과 일시적인 행사의 영향을 받을 수 있습니다.", styles["body"]))
    story.append(_text("출처", styles["h2"]))
    story.append(_text("\n".join(data.sources) or "없음", styles["body"]))
    story.append(_text("이 보고서의 수치는 분석 결과이며 공식 통계가 아닙니다. 공식 통계는 해당 주관기관에서 확인해 주세요.", styles["meta"]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Body", 8)
        canvas.setFillColor(colors.HexColor("#1a4fa8" if data.mock else FAINT))
        canvas.drawString(20 * mm, 10 * mm, "가상 데이터 / 실제 통계 아님" if data.mock else "제주 관광 낙수 진단")
        canvas.setFillColor(colors.HexColor(FAINT))
        canvas.drawRightString(190 * mm, 10 * mm, str(document.page))
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title="제주 관광 낙수 진단 보고서").build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _release_note(data: ReportData, files: list[str]) -> str:
    lines = [
        "제주빅데이터센터 반출 신청 참고",
        "반출 신청 화면에 자료별 출처와 활용 사유를 적을 때 아래 내용을 쓰세요. 활용 사유는 실제 목적에 맞게 고쳐 주세요.",
        "",
    ]
    if data.mock:
        lines += [f"[주의] {MOCK_NOTICE} 반출 신청 대상이 아닙니다.", ""]
    lines += ["반출 자료"]
    lines += [f"- {name}" for name in files]
    lines += ["", "출처(원본데이터)"]
    lines += [f"- {source}" for source in data.sources] or ["- 없음"]
    lines += [
        "", "활용 사유(초안)", f"- 다음 질문을 분석한 결과를 정리하기 위함: {data.question}",
        "", "처리 내역",
        "- 실린 수치는 증감률, 비율, 지수 같은 응용집계입니다.",
        "- 근거 표본이 10 미만인 지역은 값을 싣지 않았습니다.",
        "- 원본 행과 합계 금액은 포함하지 않았습니다.",
        "- 시각화는 PNG 파일입니다.",
        "", "최종 반출 여부는 센터 심사로 결정됩니다.",
    ]
    return "\n".join(lines)


def build_zip(data: ReportData) -> bytes:
    """보고서 zip 을 만든다."""
    check_release_safe(data)
    charts = {index: png for index, section in enumerate(data.sections) if (png := chart_png(section, data.mock))}
    chart_names = {index: f"차트/{order:02d}_{data.sections[index].title.replace('/', '_')}.png" for order, index in enumerate(charts, start=1)}
    files = ["진단보고서.pdf", *chart_names.values()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("진단보고서.pdf", _pdf(data, charts))
        for index, png in charts.items():
            archive.writestr(chart_names[index], png)
        archive.writestr("반출신청_참고.txt", _release_note(data, files).encode("utf-8-sig"))
    return buffer.getvalue()
