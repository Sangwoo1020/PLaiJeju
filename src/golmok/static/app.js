// 화면 동작: 데이터 넣기 → 조건 고르기 → 섬 위 기둥과 오른쪽 목록으로 진단 보여주기
import { createScene } from "/static/scene.js";
import { locate } from "/static/areas.js";

const $ = (id) => document.getElementById(id);
const COLUMN_WIDTH = { bjd: 0.85, emd: 1.1, dstrct_se: 2.0, city: 2.8, all: 3.2 };
const SUMMARY = "summary";   // 지표 대신 고를 수 있는 '주의 지역 종합' 화면
const SUMMARY_SHOT = 15;     // 장면의 카메라 구도 번호 (지표 15개 다음)
// 동네 유형 색: 쇼핑 소비형, 숙박·리조트형, 사람만 오는형(산호색 = 문제 쪽), 음식·카페형
const TYPE_COLORS = ["#3d72e0", "#8b5fd3", "#e8573f", "#23a487"];
const state = { status: null, metrics: [], metric: SUMMARY, selected: null, ready: null, flip: false, home: false, pin: null };
// 지표 메뉴: 주제별 묶음. 숫자는 /api/diagnose 가 돌려주는 지표 순서(장면의 카메라 구도 번호와 같다)
const MENU = [
  ["한눈에", [[SUMMARY, "주의 지역 종합"]]],
  ["소비 변화", [[0, "매출 증감"], [1, "객단가"], [2, "소비 비중"], [3, "공백 메우기"]]],
  ["사람 대비 소비", [[4, "낙수 지수"], [8, "입도객 1인당"], [7, "저녁·밤 소비"]]],
  ["크루즈·부담", [[6, "크루즈 효과"], [5, "쓰레기 부담"]]],
  ["동네 상권", [[9, "동네 식당"], [11, "가게당 소비"], [10, "도민 가게 몫"]]],
  ["알고리즘 분석", [[12, "기대 대비 실제"], [13, "크루즈 보정"], [14, "동네 유형"]]],
];

const scene = await createScene($("scene"), { labels: $("labels") });

/** 글자는 항상 textContent 로 넣는다 (CSV 에서 온 지역명을 그대로 HTML 로 쓰지 않기 위해). */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value == null) continue;   // 값이 없는 속성은 넣지 않는다 (hidden: null 은 보이게)
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  }
  node.append(...children.filter((child) => child != null));
  return node;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw Object.assign(new Error(body.detail?.message || "요청을 처리하지 못했습니다."), { detail: body.detail, status: response.status });
  return body;
}

const monthText = (month) => `${month.slice(0, 4)}년 ${Number(month.slice(4))}월`;
const rangeText = (months) => (months.length ? `${months[0].slice(0, 4)}.${months[0].slice(4)} ~ ${months.at(-1).slice(0, 4)}.${months.at(-1).slice(4)}` : "");

function valueText(value, form) {
  if (value == null) return "비공개";
  if (form.shape === "category") return String(value);
  if (form.shape === "share") return `${value.toFixed(1)}%`;
  if (form.shape === "magnitude") return `${Math.round(value).toLocaleString()}${form.unit}`;
  if (form.unit === "%") return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
  return value.toFixed(2);
}
const changeText = (pp) => (pp == null ? "" : `${pp > 0 ? "▲" : pp < 0 ? "▼" : "–"} ${Math.abs(pp).toFixed(1)}%p`);
const extraText = (row, extra) => {
  if (!extra) return null;
  const value = row[extra.key];
  if (value == null) return `${extra.label} -`;
  // 증감(키에 yoy)만 부호를 붙이고, 비중·메움률 같은 수준 값은 그대로 쓴다
  if (extra.unit === "%") return `${extra.label} ${value > 0 && /yoy|lift/.test(extra.key) ? "+" : ""}${value.toFixed(1)}%`;
  return `${extra.label} ${Math.abs(value) < 10 ? value.toFixed(2) : value.toLocaleString()}`;
};

// ---------- 왼쪽: 데이터 ----------
function log(message, problem = false, ...extra) {
  $("log").prepend(el("li", { class: problem ? "is-problem" : "", text: message }, ...extra));
  while ($("log").children.length > 4) $("log").lastChild.remove();
}

function renderDatasets(needed) {
  $("datasets").replaceChildren(...needed.map((d) =>
    el("li", { class: d.rows ? "is-loaded" : "" },
      el("span", { class: "dot" }),
      el("span", { class: "title", text: d.title }),
      el("span", { class: "detail", text: d.rows ? `${rangeText(d.months)} · ${d.rows.toLocaleString()}행${d.public ? " · 공개 데이터" : ""}` : `아직 없음 · ${d.used_for}에 필요` }))));
  $("needed").replaceChildren(...needed.map((d) =>
    el("li", {}, el("span", { class: "title", text: d.title }), el("span", { class: "use", text: d.used_for }))));
}

async function upload(file, datasetId) {
  const query = new URLSearchParams({ filename: file.name, ...(datasetId ? { dataset_id: datasetId } : {}) });
  try {
    const result = await api(`/api/upload?${query}`, { method: "POST", body: file });
    log(result.skipped_duplicate
      ? `${file.name}: 이미 불러온 파일이라 건너뛰었습니다.`
      : `${file.name}: ${result.dataset.title} ${result.rows_added.toLocaleString()}행을 불러왔습니다.`);
  } catch (error) {
    const choices = (error.detail?.choices || []).map((choice) =>
      el("button", { type: "button", text: choice.title, onclick: () => upload(file, choice.id).then(refresh) }));
    log(error.message + (choices.length ? " 어떤 데이터인지 골라 주세요." : ""), true, ...choices);
  }
}

async function addFiles(files) {
  for (const file of files) await upload(file);
  await refresh();
}

async function loadMock(button) {
  button.disabled = true;
  try {
    const result = await api("/api/load-mock", { method: "POST" });
    log(`가상 데이터 ${result.files}개 파일을 불러왔습니다. 수치는 실제 통계가 아닙니다.`);
    await refresh();
  } catch (error) {
    log(error.message, true);
  } finally {
    button.disabled = false;
  }
}

// ---------- 조건 ----------
function fillSelect(select, options, fallback) {
  const keep = select.options.length ? select.value : null;  // 처음 채울 때는 기본값을 쓴다
  select.replaceChildren(...options.map(([value, text]) => el("option", { value, text })));
  select.value = options.some(([value]) => value === keep) ? keep : fallback;
}

function renderQuestion(status) {
  fillSelect($("month"), status.months.map((m) => [m, monthText(m)]).reverse(), status.months.at(-1));
  fillSelect($("level"), Object.entries(status.levels).map(([key, name]) => [key, key === "all" ? name : `${name}별`]), "bjd");
  fillSelect($("biz"), status.biz.map((b) => [b.code, b.name]), "I56");
  fillSelect($("segment"), Object.entries(status.segments), "외국인");
}

// ---------- 위쪽: 요약 ----------
function renderTiles(tiles) {
  $("tiles").replaceChildren(...tiles.filter((tile) => tile.rows?.length).map((tile) => {
    const row = tile.rows[0], value = row[tile.form.value];
    const down = tile.form.shape === "diverging" && value < 0;
    const note = tile.form.change ? `1년 전보다 ${changeText(row[tile.form.change])}` : "전년 같은 달 대비";
    const number = el("span", { class: "tile__number", text: valueText(value, tile.form) });
    const card = el("div", { class: "tile" },
      el("div", { class: "tile__name", text: tile.form.title }),
      el("div", { class: "tile__value" }, number, el("span", { class: "tile__note", text: note })));
    card.style.setProperty("--swatch", down ? "var(--down)" : "var(--sea)");
    if (down) card.classList.add("is-down");
    countUp(number, value, tile.form);
    return card;
  }));
  $("tiles").querySelectorAll(".tile").forEach((tile, index) => tile.style.setProperty("--i", index));
}

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
/** 숫자가 0 에서 값까지 올라간다 */
function countUp(node, value, form) {
  if (reduceMotion || value == null) return;
  const start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / 1100), eased = 1 - Math.pow(1 - k, 3);
    node.textContent = valueText(value * eased, form);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ---------- 오른쪽: 지표 ----------
const rowKey = (row) => (row.bjd ? `${row.dstrct_se}|${row.bjd}` : row.emd_nm ? `emd|${row.emd_nm}` : row.dstrct_se || row.city || "all");
const rowName = (row) => row.bjd || (row.emd_nm ? row.emd_nm.split(" ").slice(1).join(" ") : null) || row.dstrct_se || row.city || "제주 전체";

// 지표마다 어느 쪽이 문제인지와, 문제일 때 이용자에게 보여줄 한 줄
const PROBLEMS = {
  yoy_pct: { bad: "low", why: "매출이 1년 전보다 줄었습니다" },
  ticket_yoy_pct: { bad: "low", why: "한 번 결제할 때 쓰는 돈이 줄었습니다" },
  share_pct: { bad: "low", why: "관광객 소비가 거의 닿지 않습니다" },
  tourist_yoy_pct: { bad: "low", why: "관광객 소비가 줄었는데 메워지지 않습니다" },
  conversion_index: { bad: "low", why: "사람은 오는데 소비로 이어지지 않습니다" },
  liters_per_1000: { bad: "high", why: "방문객에 비해 쓰레기 부담이 큽니다" },
  lift_pct: { bad: "low", why: "크루즈가 와도 매출이 늘지 않습니다" },
  night_share_pct: { bad: "low", why: "저녁에는 소비가 거의 없습니다. 들렀다 가는 관광입니다" },
  per_visitor_yoy_pct: { bad: "low", why: "입도객에 비해 지역 소비가 줄었습니다" },
  per_store_yoy_pct: { bad: "low", why: "동네 식당의 가게당 매출이 줄었습니다" },
  local_take_pct: { bad: "low", why: "관광객 돈이 도민 가게로 덜 갑니다" },
  yield_index: { bad: "low", why: "가게는 많은데 관광객 소비가 적습니다" },
  gap_pct: { bad: "low", why: "방문객·계절·추세로 본 기대보다 실제 소비가 적습니다" },
  adj_lift_pct: { bad: "low", why: "계절·추세를 빼면, 크루즈가 많이 온 달에도 섬 평균보다 소비가 덜 늘었습니다" },
};
const ALERT_LIMIT = 3;

/** 주의 지역: 문제 쪽으로 가장 치우친 곳 최대 3곳. 표본이 적은 곳은 빼고,
 *  기준선이 있는 지표(증감·지수)는 기준선을 넘어 나쁜 쪽에 있는 곳만 고른다. */
function findAlerts(rows, form) {
  const rule = PROBLEMS[form.value];
  if (!rule) return [];
  const worse = (row) => (rule.bad === "low" ? -row.value : row.value);
  return rows
    .filter((row) => row.value != null && row["신뢰수준"] !== "낮음")
    .filter((row) => form.shape !== "diverging" || (rule.bad === "low" ? row.value < form.baseline : row.value > form.baseline))
    .sort((a, b) => worse(b) - worse(a))
    .slice(0, ALERT_LIMIT);
}

function prepare(metric) {
  const form = metric.form;
  const rows = metric.rows.map((row) => ({ ...row, key: rowKey(row), name: rowName(row), value: row["비공개"] ? null : row[form.value] }));
  const shown = rows.filter((row) => row.value != null);
  if (form.shape === "category") {   // 동네 유형: 높이는 같게, 색으로 유형을 가른다
    shown.sort((a, b) => a.type_id - b.type_id || a.name.localeCompare(b.name, "ko"));
    for (const row of shown) Object.assign(row, { size: 0.55, shade: 0, tone: "category", color: TYPE_COLORS[row.type_id] || "#8796a8" });
    return [...shown, ...rows.filter((row) => row.value == null)];
  }
  const upward = form.shape === "share" || form.shape === "magnitude";
  shown.sort((a, b) => (upward ? b.value - a.value : a.value - b.value));
  const reach = Math.max(...shown.map((row) => Math.abs(row.value - (form.baseline ?? 0))), 0.0001);
  const values = shown.map((row) => row.value), low = Math.min(...values), high = Math.max(...values);
  for (const row of shown) {
    row.shade = high > low ? (row.value - low) / (high - low) : 0.6;  // 색 농도는 지역 간 차이가 보이게 최소~최대로 나눈다
    row.size = form.shape === "share" ? row.value / 100 : form.shape === "magnitude" ? row.value / (high || 1) : Math.abs(row.value - form.baseline) / reach;
    row.tone = upward ? "share" : row.value >= form.baseline ? "up" : "down";
  }
  findAlerts(shown, form).forEach((row, index) => { row.alert = index + 1; });
  return [...shown, ...rows.filter((row) => row.value == null)];
}

function renderRow(row, form, index) {
  const withheld = row.value == null;
  const bar = el("span", { class: `bar bar--${row.tone}` });
  if (!withheld) bar.style.width = `${row.size * (form.shape === "diverging" ? 50 : 100)}%`;
  const rank = withheld ? el("i", { class: "row__rank", text: "–" }) : el("i", { class: "row__rank", text: String(index + 1) });
  const change = form.change ? el("small", { text: changeText(row[form.change]) }) : form.extra ? el("small", { text: extraText(row, form.extra) }) : null;
  const trust = !withheld && row["신뢰수준"] !== "높음" ? el("small", { class: "row__trust", text: row["신뢰수준"] === "낮음" ? "표본 적음" : "표본 보통" }) : null;
  const item = el("li", {},
    el("button", { type: "button", class: `row${withheld ? " is-withheld" : ""}`, "data-key": row.key, onclick: () => select(row.key, true) },
      el("span", { class: "row__name" }, rank, el("b", { text: row.name }), row.alert ? el("em", { class: "row__alert", text: "주의" }) : null, row.bjd && !row.alert ? el("small", { text: row.dstrct_se }) : null),
      withheld
        ? el("span", { class: "row__withheld", text: "비공개 · 표본 10 미만" })
        : form.shape === "category"
          ? Object.assign(el("span", { class: "type-chip", text: row.value }), { style: `--c:${row.color}` })
          : el("span", { class: `track track--${form.shape === "diverging" ? "diverging" : "share"}` }, bar),
      withheld ? null : form.shape === "category"
        ? el("span", { class: "row__value row__value--plain", text: extraText(row, form.extra) || "" })
        : el("span", { class: "row__value", text: valueText(row.value, form) }, change || trust)));
  if (row.tone === "down") item.firstChild.classList.add("is-down");
  if (row.alert) item.firstChild.classList.add("is-alert");
  item.style.setProperty("--i", Math.min(index, 20));
  return item;
}

function renderTabs() {
  // 위 줄: 지표 묶음. 아래 줄: 고른 묶음의 지표. 다른 묶음의 지표 단추도 숨겨 두기만 해서 이름으로 찾을 수 있다
  const groups = MENU.map(([group, items]) => [group, items.filter(([index]) => index === SUMMARY || state.metrics[index])]).filter(([, items]) => items.length);
  const active = (groups.find(([, items]) => items.some(([index]) => index === state.metric)) || groups[0])[0];
  const pick = (index) => { state.metric = index; state.flip = false; renderBoard(); };
  const title = (index) => (index === SUMMARY ? "여러 지표에서 함께 문제로 나온 동네" : state.metrics[index].form?.title || state.metrics[index].title);
  $("tabs").replaceChildren(...[
    el("div", { class: "tabs__groups" }, ...groups.map(([group, items]) => el("button", {
      type: "button", class: `tabs__group${group === "한눈에" ? " tabs__group--summary" : ""}`, "aria-pressed": String(group === active),
      title: items.map(([, label]) => label).join(" · "), text: group, onclick: () => { if (group !== active) pick(items[0][0]); } }))),
    ...groups.filter(([group]) => group !== "한눈에").map(([group, items]) => el("div", { class: "tabs__items", hidden: group !== active ? "" : null },
      ...items.map(([index, label]) => el("button", { type: "button", "aria-pressed": String(index === state.metric), title: title(index), text: label, onclick: () => pick(index) })))),
    active === "한눈에" ? el("p", { class: "tabs__hint", text: "지표마다 문제 쪽에 든 동네를 모아, 여러 지표에서 함께 걸린 곳부터 보여 줍니다." }) : null,
  ].filter(Boolean));
}

// ---------- 주의 지역 종합: 여러 지표에서 함께 문제로 나온 동네 ----------
const NUMERALS = { 1: "일", 2: "이", 3: "삼", 4: "사" };
/** 행정동(일도1동)과 법정동(일도일동)이 같은 동네로 묶이도록 '시 동네' 이름을 만든다. 권역·시 단위 행은 null. */
function placeKey(row) {
  let city, name;
  if (row.bjd) { city = (row.dstrct_se || "").split(" ")[0]; name = row.bjd; }
  else if (row.emd_nm) { [city, ...name] = row.emd_nm.split(" "); name = name.join(" "); }
  else return null;
  return `${city} ${name.replace(/(\d)동$/, (_, digit) => `${NUMERALS[digit] || digit}동`)}`;
}
/** 조건(기준 월 · 지역 단위 · 업종 · 이용자)을 작은 알약 모양으로 나란히 보여 준다. */
function conditionChips(text) {
  return el("div", { class: "meta__condition" }, ...text.split(" · ").filter(Boolean).map((part) => el("span", { text: part })));
}
const menuLabel = (index) => MENU.flatMap(([, items]) => items).find(([i]) => i === index)?.[1] || state.metrics[index].title;

/** 지표 하나에서 문제 쪽에 든 행: 증감·지수는 기준선보다 나쁜 곳, 비중·값은 가장 나쁜 4분의 1. 표본 적은 곳은 뺀다. */
function problemRows(metric) {
  const form = metric.form, rule = PROBLEMS[form.value];
  if (!rule) return null;   // 동네 유형처럼 좋고 나쁨이 없는 지표는 세지 않는다
  const checked = prepare(metric).filter((row) => row.value != null && row["신뢰수준"] !== "낮음");
  const worse = (row) => (rule.bad === "low" ? -row.value : row.value);
  const flagged = form.shape === "diverging"
    ? checked.filter((row) => (rule.bad === "low" ? row.value < form.baseline : row.value > form.baseline))
    : [...checked].sort((a, b) => worse(b) - worse(a)).slice(0, Math.ceil(checked.length / 4));
  return { checked, flagged };
}

function summarize() {
  const areas = new Map(), regional = [], skipped = [];
  let used = 0;
  state.metrics.forEach((metric, index) => {
    if (metric.missing || !metric.form) { skipped.push(menuLabel(index)); return; }
    const problems = problemRows(metric);
    if (!problems) return;
    const { checked, flagged } = problems;
    if (!checked.length) { skipped.push(menuLabel(index)); return; }
    const flaggedKeys = new Set(flagged.map((row) => row.key));
    if (!checked.some((row) => placeKey(row))) {   // 권역·시 단위로만 나온 지표는 동네 수에 넣지 않고 따로 적는다
      if (flagged.length) regional.push(`${menuLabel(index)}(${flagged.map((row) => row.name).join("·")})`);
      return;
    }
    used += 1;
    for (const row of checked) {
      const key = placeKey(row);
      if (!key) continue;
      const area = areas.get(key) || { key, name: key.split(" ").slice(1).join(" "), region: row.dstrct_se || key.split(" ")[0], row, hits: [], checked: 0 };
      if (row.bjd) area.row = row;   // 지도 위치는 법정동 행을 우선 쓴다
      area.checked += 1;
      if (flaggedKeys.has(row.key)) area.hits.push({ label: menuLabel(index), value: valueText(row.value, metric.form) });
      areas.set(key, area);
    }
  });
  const list = [...areas.values()].filter((area) => area.hits.length).sort((a, b) => b.hits.length - a.hits.length || b.hits.length / b.checked - a.hits.length / a.checked || a.name.localeCompare(b.name, "ko"));
  list.filter((area) => area.hits.length >= 2).slice(0, ALERT_LIMIT).forEach((area, i) => { area.alert = i + 1; });
  return { list, used, regional, skipped, total: areas.size };
}

function renderSummary() {
  const { list, used, regional, skipped, total } = summarize();
  const most = Math.max(1, ...list.map((area) => area.hits.length));
  const picked = (id) => $(id).selectedOptions[0]?.textContent || "";
  $("meta").replaceChildren(
    el("b", { class: "meta__title", text: "주의 지역 종합" }),
    conditionChips([picked("month"), picked("level"), picked("biz"), picked("segment")].filter(Boolean).join(" · ")),
    el("span", { class: "meta__definition", text: `동네 단위로 나온 지표 ${used}개를 함께 보고, 동네마다 문제 쪽에 든 지표 수를 셉니다. 증감·지수는 기준선보다 나쁜 곳, 비중·값은 가장 나쁜 4분의 1입니다.` }),
    el("details", { class: "meta__more" }, el("summary", { text: "세는 방법" }),
      el("small", { text: "표본이 적은 값은 세지 않습니다. 지표마다 계산 단위와 데이터가 달라, 개수는 어디부터 들여다볼지 정하는 길잡이입니다." })));
  const alerts = list.filter((area) => area.alert);
  $("highlights").replaceChildren(alerts.length
    ? el("div", { class: "alert-card", role: "group", "aria-label": "여러 지표에서 걸린 동네" },
      el("div", { class: "alert-card__head" },
        el("span", { class: "alert-card__icon", "aria-hidden": "true", text: "!" }),
        el("b", { text: `여러 지표에서 함께 걸린 동네 ${alerts.length}곳` }),
        el("small", { text: `${total}곳 중` })),
      el("p", { class: "alert-card__why", text: `${alerts[0].name}: ${alerts[0].hits.map((hit) => hit.label).join(", ")}` }),
      el("div", { class: "alert-card__places" }, ...alerts.map((area) => el("button", { type: "button", class: "alert-chip", onclick: () => select(area.key, true) },
        el("i", { text: String(area.alert) }),
        el("b", { text: area.name }),
        el("span", { text: `지표 ${area.hits.length}개` })))))
    : el("div", { class: "alert-card is-calm" },
      el("div", { class: "alert-card__head" },
        el("span", { class: "alert-card__icon", "aria-hidden": "true", text: "✓" }),
        el("b", { text: "여러 지표에서 함께 걸린 동네가 없습니다" }),
        el("small", { text: `${total}곳 중` })),
      el("p", { class: "alert-card__why", text: list.length ? "지표 하나에서만 걸린 동네는 아래 목록에 있습니다." : "문제 쪽에 든 동네가 없습니다." })));
  const legend = [["var(--alert)", "기둥이 높을수록 여러 지표에서 문제로 나온 동네"]];
  $("legend").replaceChildren(...legend.map(([color, text]) => { const item = el("span", { text }); item.style.setProperty("--swatch", color); return item; }));
  $("rows-head").replaceChildren(el("span", { text: "동네" }), el("span", { class: "rows__sort", text: "많은 순 ↓" }), el("span", { text: "걸린 지표" }));
  $("rows").replaceChildren(...list.map((area, index) => {
    const item = el("li", {},
      el("button", { type: "button", class: `row row--summary${area.alert ? " is-alert" : ""}`, "data-key": area.key, title: area.hits.map((hit) => `${hit.label} ${hit.value}`).join("\n"), onclick: () => select(area.key, true) },
        el("span", { class: "row__name" }, el("i", { class: "row__rank", text: String(index + 1) }), el("b", { text: area.name }),
          area.alert ? el("em", { class: "row__alert", text: "주의" }) : el("small", { text: area.region })),
        el("span", { class: "track track--share" }, Object.assign(el("span", { class: "bar bar--alert" }), { style: `width:${(area.hits.length / Math.max(used, 1)) * 100}%` })),
        el("span", { class: "row__value", text: `${area.hits.length}개` }, el("small", { text: `${area.checked}개 중` })),
        el("span", { class: "row__hits", text: area.hits.map((hit) => hit.label).join(" · ") })));
    item.style.setProperty("--i", Math.min(index, 20));
    return item;
  }));
  if (!list.length) $("rows").replaceChildren(el("li", { class: "missing", text: "문제 쪽에 든 동네가 없습니다." }));
  $("notes").textContent = [
    regional.length ? `권역·시 단위로만 나온 지표는 동네 수에 넣지 않았습니다: ${regional.join(", ")}.` : "",
    skipped.length ? `데이터가 없어 빠진 지표: ${skipped.join(", ")}.` : "",
  ].filter(Boolean).join(" ");
  scene.setColumns(list.map((area) => ({
    key: area.key,
    name: area.name,
    valueText: `지표 ${area.hits.length}개`,
    lonLat: locate(area.row),
    height: area.hits.length / most,
    shade: area.hits.length / most,
    tone: "risk",
    alert: area.alert || 0,
    width: COLUMN_WIDTH.bjd,
  })), { shot: SUMMARY_SHOT });
  select(list.some((area) => area.key === state.selected) ? state.selected : null, false);
}

function renderBoard() {
  $("board").classList.remove("is-switching");
  void $("board").offsetWidth;   // 애니메이션을 다시 시작하게 한다
  $("board").classList.add("is-switching");
  renderTabs();
  if (state.metric === SUMMARY) return renderSummary();
  const metric = state.metrics[state.metric];

  if (metric.missing) {
    $("meta").replaceChildren(el("b", { class: "meta__title", text: metric.title }));
    $("highlights").replaceChildren();
    $("rows-head").replaceChildren();
    $("legend").replaceChildren();
    $("notes").textContent = "";
    $("rows").replaceChildren(el("li", { class: "missing", text: metric.missing }));
    scene.setColumns([], { shot: state.metric });
    return;
  }

  const form = metric.form, rows = prepare(metric);
  const level = form.fixed_level === "권역" ? "dstrct_se" : form.fixed_level === "행정동" ? "emd" : form.fixed_level === "읍면동" ? "bjd" : $("level").value;
  const picked = (id) => $(id).selectedOptions[0]?.textContent || "";
  const where = form.fixed_level ? `${form.fixed_level}별` : picked("level");
  const condition = [picked("month"), where, picked("biz"), metric.title.includes("(") ? null : picked("segment")].filter(Boolean).join(" · ");
  $("meta").replaceChildren(...[
    el("b", { class: "meta__title", text: metric.title }),
    conditionChips(condition),
    el("span", { class: "meta__definition", text: metric.definition }),
    el("details", { class: "meta__more" }, el("summary", { text: "계산 단위와 출처" }), ...[
      form.fixed_level === "권역" ? el("small", { text: "카드와 유동인구의 지역 단위가 달라 항상 권역 단위로 봅니다." }) : null,
      form.fixed_level === "행정동" ? el("small", { text: "종량제봉투와 유동인구는 행정동 단위라 법정동 대신 행정동으로 봅니다." }) : null,
      form.fixed_level === "읍면동" ? el("small", { text: "관광공사 공개 데이터는 소비와 방문객이 같은 읍면동 단위라 읍면동별로 봅니다." }) : null,
      form.fixed_level === "시" ? el("small", { text: "업종별 매장 수가 시 단위로만 있어 제주시·서귀포시로 봅니다." }) : null,
      el("small", { text: `출처: ${metric.sources.join(" / ")}` }),
    ]),
  ].filter(Boolean));
  const typeNames = form.shape === "category" ? [...new Map(rows.filter((row) => row.value != null).map((row) => [row.type_id, row.value])).entries()].sort((a, b) => a[0] - b[0]) : [];
  const legend = form.shape === "category"
    ? typeNames.map(([id, name]) => [TYPE_COLORS[id], name])
    : form.shape === "share"
    ? [["var(--sea)", "비중이 높을수록 기둥이 높고 진합니다"]]
    : form.shape === "magnitude"
      ? [["var(--sea)", "값이 클수록 기둥이 높고 진합니다"]]
      : [["var(--sea)", form.baseline === 1 ? "제주 평균보다 높음" : "늘어남"], ["var(--down)", form.baseline === 1 ? "평균보다 낮음" : "줄어듦"]];
  const shown = rows.filter((row) => row.value != null), hidden = rows.length - shown.length;
  if (state.flip) shown.reverse();
  const ordered = [...shown, ...rows.filter((row) => row.value == null)];
  $("rows").replaceChildren(...ordered.map((row, index) => renderRow(row, form, index)));
  // 요약: 문제가 있는 곳(주의 지역)을 맨 위에 둔다
  const alerts = rows.filter((row) => row.alert).sort((a, b) => a.alert - b.alert);
  const thin = shown.filter((row) => row["신뢰수준"] === "낮음").length;
  const counted = `${shown.length}곳 중${thin ? " · 표본 적은 곳 제외" : ""}`;
  $("highlights").replaceChildren(form.shape === "category"
    ? el("div", { class: "type-cards" }, ...typeNames.map(([id, name]) => {
      const members = shown.filter((row) => row.type_id === id);
      const card = el("div", { class: "type-card" }, el("b", { text: name }), el("span", { text: `${members.length}곳` }),
        el("small", { text: members.slice(0, 4).map((row) => row.name).join(" · ") + (members.length > 4 ? " …" : "") }));
      card.style.setProperty("--c", TYPE_COLORS[id]);
      return card;
    }))
    : alerts.length
    ? el("div", { class: "alert-card", role: "group", "aria-label": "주의 지역" },
      el("div", { class: "alert-card__head" },
        el("span", { class: "alert-card__icon", "aria-hidden": "true", text: "!" }),
        el("b", { text: `주의 지역 ${alerts.length}곳` }),
        el("small", { text: counted })),
      el("p", { class: "alert-card__why", text: PROBLEMS[form.value].why }),
      el("div", { class: "alert-card__places" }, ...alerts.map((row) => el("button", { type: "button", class: "alert-chip", onclick: () => select(row.key, true) },
        el("i", { text: String(row.alert) }),
        el("b", { text: row.name }),
        el("span", { text: valueText(row.value, form) })))))
    : el("div", { class: "alert-card is-calm" },
      el("div", { class: "alert-card__head" },
        el("span", { class: "alert-card__icon", "aria-hidden": "true", text: "✓" }),
        el("b", { text: "눈에 띄는 문제 지역이 없습니다" }),
        el("small", { text: counted })),
      el("p", { class: "alert-card__why", text: form.shape === "diverging" ? (form.baseline === 1 ? "표본이 충분한 지역은 모두 제주 평균 이상입니다." : "표본이 충분한 지역은 모두 늘었습니다.") : "표본이 충분한 지역이 없어 고르지 않았습니다." })));
  const ascending = (form.shape === "diverging") !== state.flip;
  const valueLabel = form.shape === "category" ? "방문 대비 소비" : form.shape === "share" ? "비중" : form.shape === "magnitude" ? "값" : form.baseline === 1 ? "지수" : "증감";
  $("rows-head").replaceChildren(
    el("span", { text: "지역" }),
    form.shape === "category" ? el("span", { class: "rows__sort", text: "유형" })
      : el("button", { type: "button", class: "rows__sort", text: ascending ? "낮은 순 ↑" : "높은 순 ↓", title: "순서 바꾸기", onclick: () => { state.flip = !state.flip; renderBoard(); } }),
    el("span", { text: valueLabel }));
  $("notes").textContent = [...metric.notes, form.value === "per_store_yoy_pct" ? "" : "‘표본 적음’은 근거 이용자 30명 미만, ‘표본 보통’은 30~99명입니다."].filter(Boolean).join(" ");

  const effect = form.value === "conversion_index" ? "drops" : form.value === "lift_pct" ? "flows" : form.value === "night_share_pct" ? "dusk" : null;
  if (effect === "drops") legend.push(["#9fd3ff", "떨어지는 동전 = 지수가 높은 곳"]);
  if (effect === "flows") legend.push(["#5cc8f2", "항구에서 이은 선 = 효과가 큰 곳"]);
  if (effect === "dusk") legend.push(["#3c4f86", "저녁 장면 = 저녁·밤 소비"]);
  $("legend").replaceChildren(...legend.map(([color, text]) => {
    const item = el("span", { text });
    item.style.setProperty("--swatch", color);
    return item;
  }));
  scene.setColumns(rows.map((row) => ({
    key: row.key,
    name: row.name,
    valueText: valueText(row.value, form),
    lonLat: locate(row),
    height: row.value == null ? 0 : row.size,
    shade: row.shade ?? 0,
    tone: row.value == null ? "hidden" : row.tone,
    alert: row.alert || 0,
    color: row.color,
    width: COLUMN_WIDTH[level],
  })), { shot: state.metric, effect });
  select(rows.some((row) => row.key === state.selected) ? state.selected : null, false);
}

function select(key, fromList) {
  state.selected = state.selected === key && fromList ? null : key;
  for (const button of $("rows").querySelectorAll(".row")) {
    const on = button.dataset.key === state.selected;
    button.classList.toggle("is-selected", on);
    if (on && !fromList) button.scrollIntoView({ block: "center" });
  }
  scene.select(state.selected);
  showDong(state.selected);
}

// ---------- 동네 진단 카드: 고른 동네의 지표를 한 장에, 두 동네 비교, Agent 질문으로 잇기 ----------
const COMPARE_COLORS = ["#2a6fdb", "#8b5fd3"];

/** 고른 동네(목록 행 키 또는 종합 화면의 동네 키)가 지표마다 어떤 값인지 모은다. 이미 계산된 값만 쓴다. */
function profileOf(key) {
  let target = null;
  for (const metric of state.metrics) {
    if (metric.missing || !metric.form) continue;
    const row = metric.rows.find((r) => rowKey(r) === key || placeKey(r) === key);
    if (row) { target = { pk: placeKey(row), rk: rowKey(row), row }; break; }
  }
  if (!target) return null;
  const items = [];
  state.metrics.forEach((metric, index) => {
    if (metric.missing || !metric.form) return;
    const form = metric.form, rows = prepare(metric);
    const row = rows.find((r) => (target.pk ? placeKey(r) === target.pk : r.key === target.rk));
    if (!row) return;
    const shown = rows.filter((r) => r.value != null);
    const problems = problemRows(metric);
    const flagged = Boolean(problems && row.value != null && problems.flagged.some((r) => r.key === row.key));
    const upward = form.shape === "share" || form.shape === "magnitude";
    const values = shown.map((r) => r.value), low = Math.min(...values), high = Math.max(...values);
    const spot = (v) => (high > low ? (v - low) / (high - low) : 0.5);
    items.push({
      index, label: menuLabel(index), form, row, flagged, why: PROBLEMS[form.value]?.why,
      text: row.value == null ? "비공개" : form.shape === "category" ? String(row.value) : valueText(row.value, form),
      rank: row.value == null || form.shape === "category" ? null : shown.indexOf(row) + 1, count: shown.length, order: upward ? "높은 순" : "낮은 순",
      spot: row.value == null || form.shape === "category" ? null : spot(row.value),
      base: form.shape === "diverging" && form.baseline > low && form.baseline < high ? spot(form.baseline) : null,
      thin: row["신뢰수준"] === "낮음",
    });
  });
  const row = target.row;
  return { key, name: row.bjd || rowName(row), region: row.dstrct_se || row.city || "", items, place: row.bjd || (row.emd_nm ? row.emd_nm.split(" ").slice(1).join(" ") : null) };
}

function positionBar(entries, base) {
  const track = el("span", { class: "dong-pos" });
  if (base != null) track.append(Object.assign(el("i", { class: "dong-pos__base", title: "기준선" }), { style: `left:${base * 100}%` }));
  for (const [spot, color, flagged] of entries) {
    if (spot == null) continue;
    const dot = el("b", { class: `dong-pos__dot${flagged ? " is-alert" : ""}` });
    dot.style.left = `${spot * 100}%`;
    dot.style.setProperty("--c", flagged ? "var(--alert)" : color);
    track.append(dot);
  }
  return track;
}

function askAbout(text) {
  $("ask-input").value = text;
  askQuestion(text);
}

let dongAsk = 0;
async function showDong(key) {
  const card = $("dong-card");
  const ask = ++dongAsk;
  const answering = !$("answer").hidden;
  document.body.classList.toggle("dong-open", Boolean(key && !state.home && state.ready && !answering));
  if (!key || state.home || !state.ready || answering) { card.hidden = true; $("ask-input").placeholder = "예: 연동 면세점 매출은 작년보다 늘었나요?"; layout(); return; }
  const profile = profileOf(key);
  if (!profile) { card.hidden = true; document.body.classList.remove("dong-open"); layout(); return; }
  const pinned = state.pin && state.pin !== key ? profileOf(state.pin) : null;
  $("ask-input").placeholder = pinned ? `예: ${pinned.name}과 ${profile.name}을 비교해 주세요` : `예: ${profile.name}은 외국인 소비가 왜 적나요?`;
  card.replaceChildren(...(pinned ? compareView(pinned, profile) : dongView(profile)));
  const fresh = card.hidden || card.dataset.key !== `${state.pin || ""}>${key}`;
  card.dataset.key = `${state.pin || ""}>${key}`;
  card.hidden = false;
  if (fresh) { card.classList.remove("is-in"); void card.offsetWidth; card.classList.add("is-in"); }   // 지표만 바꿀 때는 다시 날아오지 않게
  layout();
  if (pinned || !profile.place) return;
  // 관광공사 공개 데이터의 최근 2년 외국인 소비 흐름 (센터 데이터 동네 이름이 공개 데이터와 다르면 건너뛴다)
  let trend = null;
  try { trend = await api(`/api/place?names=${encodeURIComponent(profile.place)}`); } catch { trend = null; }
  if (ask !== dongAsk || !trend?.available || !trend.trend?.length) return;
  const slot = card.querySelector(".dong-card__trend");
  if (slot) slot.replaceChildren(el("span", { text: `외국인 관광객 소비 흐름 · ${monthText(trend.trend[0].month)} ~ ${monthText(trend.trend.at(-1).month)}` }), sparkline(trend.trend));
}

function dongHead(kind, close) {
  return el("div", { class: "dong-card__head" }, el("span", { class: "dong-card__kind", text: kind }),
    el("button", { type: "button", class: "dong-card__close", "aria-label": "닫기", text: "×", onclick: close }));
}

function dongView(profile) {
  const scored = profile.items.filter((item) => item.form.shape !== "category" && item.rank != null);
  const hits = scored.filter((item) => item.flagged);
  const type = profile.items.find((item) => item.form.shape === "category" && item.row.value != null);
  const current = typeof state.metric === "number" ? state.metric : null;
  const ordered = [...hits, ...scored.filter((item) => !item.flagged)];
  return [
    dongHead("동네 진단", () => select(null, false)),
    el("h3", { text: profile.name }),
    el("p", { class: "dong-card__scope", text: [profile.region, $("month").selectedOptions[0]?.textContent, type ? `유형: ${type.text}` : null].filter(Boolean).join(" · ") }),
    el("div", { class: `dong-card__verdict${hits.length >= 2 ? " is-alert" : hits.length ? " is-warn" : ""}` },
      el("b", { text: hits.length ? `지표 ${scored.length}개 중 ${hits.length}개가 문제 쪽` : `지표 ${scored.length}개 모두 문제 쪽이 아닙니다` }),
      el("span", { text: hits.length ? hits[0].why : "제주 기준선과 비교해 눈에 띄게 나쁜 지표가 없습니다." })),
    el("div", { class: "dong-card__trend" }),
    el("ul", { class: "dong-list" }, ...ordered.map((item) => el("li", {},
      el("button", { type: "button", class: `dong-item${item.flagged ? " is-alert" : ""}${item.index === current ? " is-current" : ""}`, title: item.flagged ? item.why : `${item.order} ${item.rank}위 / ${item.count}곳`,
        onclick: () => { state.metric = item.index; state.flip = false; renderBoard(); } },
        el("span", { class: "dong-item__label", text: item.label }),
        positionBar([[item.spot, COMPARE_COLORS[0], item.flagged]], item.base),
        el("span", { class: "dong-item__value", text: item.text }, el("small", { text: item.thin ? "표본 적음" : `${item.order} ${item.rank}위` })))))),
    el("p", { class: "dong-card__note", text: "막대는 제주 동네 사이의 위치입니다. 세로선은 기준선(평균 1 또는 0%)입니다. 지표를 누르면 오른쪽 목록이 그 지표로 바뀝니다." }),
    el("div", { class: "dong-card__actions" },
      el("button", { type: "button", class: "dong-card__ask", onclick: () => askAbout(hits.length
        ? `${profile.name}에서 문제로 나온 지표는 ${hits.slice(0, 3).map((item) => item.label).join(", ")}입니다. 왜 그런지 원인을 확인해 주세요.`
        : `${profile.name}의 관광 소비 상황을 진단해 주세요.`) },
        el("span", { "aria-hidden": "true", text: "✦" }), "이 동네 Agent에게 묻기"),
      el("button", { type: "button", class: "dong-card__pin", onclick: () => { state.pin = profile.key; renderPinHint(profile); } }, "비교에 추가")),
    el("p", { class: "dong-card__hint", hidden: "" }),
  ];
}

function renderPinHint(profile) {
  const hint = $("dong-card").querySelector(".dong-card__hint");
  hint.textContent = `${profile.name}을 담았습니다. 목록이나 지도에서 비교할 동네를 하나 더 누르세요.`;
  hint.hidden = false;
}

function compareView(a, b) {
  const byIndex = new Map(b.items.map((item) => [item.index, item]));
  const pairs = a.items.filter((item) => byIndex.has(item.index) && item.form.shape !== "category").map((item) => [item, byIndex.get(item.index)]);
  const hitsA = pairs.filter(([x]) => x.flagged).length, hitsB = pairs.filter(([, y]) => y.flagged).length;
  const name = (profile, color, hits) => el("div", { class: "dong-vs__name" }, Object.assign(el("i"), { style: `background:${color}` }), el("b", { text: profile.name }), el("small", { text: `문제 쪽 ${hits}개` }));
  return [
    dongHead("두 동네 비교", () => { state.pin = null; showDong(state.selected); }),
    el("div", { class: "dong-vs" }, name(a, COMPARE_COLORS[0], hitsA), el("span", { class: "dong-vs__mid", text: "vs" }), name(b, COMPARE_COLORS[1], hitsB)),
    el("ul", { class: "dong-list dong-list--compare" }, ...pairs.map(([x, y]) => el("li", {},
      el("button", { type: "button", class: "dong-item dong-item--compare", onclick: () => { state.metric = x.index; state.flip = false; renderBoard(); } },
        el("span", { class: "dong-item__label", text: x.label }),
        el("span", { class: `dong-item__pair${x.flagged ? " is-alert" : ""}`, style: `--c:${COMPARE_COLORS[0]}`, text: x.text }),
        el("span", { class: `dong-item__pair${y.flagged ? " is-alert" : ""}`, style: `--c:${COMPARE_COLORS[1]}`, text: y.text }),
        positionBar([[x.spot, COMPARE_COLORS[0], false], [y.spot, COMPARE_COLORS[1], false]], x.base))))),
    el("p", { class: "dong-card__note", text: "빨간 값은 그 지표에서 문제 쪽에 든 값입니다. 막대 위 점 두 개가 제주 동네 사이에서 두 동네의 위치입니다." }),
    el("div", { class: "dong-card__actions" },
      el("button", { type: "button", class: "dong-card__ask", onclick: () => askAbout(`${a.name}과 ${b.name}을 비교해 주세요. 관광객 소비가 어느 쪽에 더 떨어지는지와 그 이유를 알려 주세요.`) },
        el("span", { "aria-hidden": "true", text: "✦" }), "두 동네 Agent에게 비교 묻기"),
      el("button", { type: "button", class: "dong-card__pin", onclick: () => { state.pin = null; showDong(state.selected); } }, "비교 끝내기")),
  ];
}
scene.onSelect((key) => select(key, false));
scene.onZoom((name) => {   // 지명·섬을 눌러 당겨 본 동안: 시작 화면 제목을 비켜 두고 되돌아가기 단추를 띄운다
  document.body.classList.toggle("is-zoomed", Boolean(name));
  $("zoom-back").hidden = !name;
  $("zoom-back").querySelector("b").textContent = name || "";
  showPlace(name);
  layout();
});

// ---------- 메인 화면 지명 카드: 누른 곳의 동네가 관광공사 공개 데이터로 어떤지 ----------
const OLD_JEJU = ["일도1동", "일도2동", "이도1동", "삼도1동", "삼도2동", "건입동", "용담1동"];
const PLACES = {
  "제주시": { names: OLD_JEJU, label: "제주시 원도심", kind: "도심", note: "일도·이도·삼도·건입·용담1동을 묶어 봅니다. 관광 소비가 몰리는 신제주(연동·노형)와 대비되는 옛 중심 상권입니다." },
  "서귀포시": { names: ["송산동", "정방동", "중앙동", "천지동"], label: "서귀포 원도심", kind: "도심", note: "올레시장이 있는 서귀포 원도심 네 동을 묶어 봅니다." },
  "한라산": { names: [], label: "제주 전체", kind: "섬 전체", note: "한라산은 상권이 아니라서 제주 전체 값을 보여 줍니다." },
  "성산일출봉": { names: ["성산읍"], kind: "관광지", note: "일출봉과 광치기해변이 있는 동쪽 관광지입니다." },
  "우도": { names: ["우도면"], kind: "섬", note: "외국인 방문이 크게 늘며 쓰레기·혼잡이 함께 보도된 섬입니다." },
  "산방산": { names: ["안덕면"], kind: "관광지", note: "산방산·용머리해안과 대형 리조트가 있는 서남쪽 면입니다." },
  "추자도": { names: ["추자면"], kind: "섬", note: "본섬과 떨어진 북쪽 섬 마을입니다." },
  "제주국제공항": { names: ["용담2동"], kind: "관문", note: "관광객 대부분이 들어오는 관문입니다. 공항이 있는 용담2동 기준입니다." },
  "함덕해수욕장": { names: ["조천읍"], kind: "관광지", note: "함덕해수욕장이 있는 조천읍 기준입니다." },
  "협재해수욕장": { names: ["한림읍"], kind: "관광지", note: "협재해수욕장이 있는 한림읍 기준입니다." },
  "중문관광단지": { names: ["중문동"], kind: "관광단지", note: "대형 리조트가 모인 단지입니다. 소비가 단지 밖 동네로 이어지는지가 쟁점입니다." },
  "표선해수욕장": { names: ["표선면"], kind: "관광지", note: "표선해수욕장이 있는 표선면 기준입니다." },
  "제주항 크루즈터미널": { names: ["건입동"], kind: "크루즈 관문", note: "크루즈 관광객이 내리는 곳입니다. 터미널이 있는 건입동 기준입니다." },
  "강정항 크루즈터미널": { names: ["대천동"], kind: "크루즈 관문", note: "서귀포 크루즈 기항지입니다. 강정동이 속한 대천동 기준입니다." },
  // 도심 랜드마크 (도시 모드 이름표)
  "제주드림타워": { names: ["노형동"], kind: "관광 소비", note: "복합리조트가 있는 노형동 기준입니다." },
  "신라면세점 제주점": { names: ["연동"], kind: "관광 소비", note: "시내면세점이 있는 연동 기준입니다." },
  "누웨마루거리": { names: ["연동"], kind: "관광 소비", note: "외국인 쇼핑 거리가 있는 연동 기준입니다." },
  "중문 리조트 호텔": { names: ["중문동"], kind: "관광 소비", note: "중문관광단지가 있는 중문동 기준입니다." },
  "동문재래시장": { names: ["일도1동"], kind: "도민 상권", note: "원도심 전통시장이 있는 일도1동 기준입니다." },
  "칠성로·중앙지하상가": { names: ["일도1동", "삼도2동"], kind: "도민 상권", note: "원도심 상점가가 걸친 일도1동·삼도2동 기준입니다." },
  "서귀포매일올레시장": { names: ["중앙동"], kind: "도민 상권", note: "올레시장이 있는 중앙동 기준입니다." },
  "제주민속오일시장": { names: ["도두동"], kind: "도민 상권", note: "오일시장이 있는 도두동 기준입니다." },
};
let placeAsk = 0;

// 장소 사진(위키미디어 공용의 자유 라이선스 사진). 프로그램에 들어 있어 인터넷 없이 보인다. 사진이 없는 곳은 카드가 사진 없이 뜬다
let placePhotos = {};
fetch("/static/places/photos.json").then((r) => (r.ok ? r.json() : {})).then((list) => { placePhotos = list; }).catch(() => {});
function placePhoto(name, head) {
  const photo = placePhotos[name];
  if (!photo) return null;
  const image = el("img", { src: `/static/places/${photo.file}`, alt: `${name} 사진`, loading: "lazy", decoding: "async" });
  const figure = el("figure", { class: "place-card__photo" }, image, head,
    el("figcaption", { title: `${photo.title} · ${photo.source}`, text: `사진: ${photo.author} · ${photo.license}` }));
  image.addEventListener("error", () => { figure.before(head); figure.remove(); });   // 파일이 없으면 사진 없는 카드로 되돌린다
  return figure;
}

const signed = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);
function verdict(p) {
  const c = p.conversion_index;
  if (c == null) return null;
  if (c < 0.8) return ["is-low", "사람에 비해 외국인 소비가 덜 떨어지는 곳"];
  if (c > 1.25) return ["is-high", "외국인 소비가 몰리는 곳"];
  return ["is-mid", "제주 평균 수준"];
}
function sparkline(trend) {
  const w = 300, h = 54, pad = 4, n = trend.length;
  if (n < 2) return null;
  const pts = trend.map((t, i) => [pad + (i / (n - 1)) * (w - pad * 2), h - pad - t.level * (h - pad * 2)]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("class", "place-card__spark");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "최근 2년 외국인 관광객 소비 흐름");
  svg.innerHTML = `<defs><linearGradient id="spark-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#2a6fdb" stop-opacity="0.28"/><stop offset="1" stop-color="#2a6fdb" stop-opacity="0"/></linearGradient></defs>
    <path d="${line} L${pts.at(-1)[0]} ${h} L${pts[0][0]} ${h} Z" fill="url(#spark-fill)"/>
    <path class="place-card__line" d="${line}" fill="none" stroke="#2a6fdb" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" pathLength="1"/>
    <circle cx="${pts.at(-1)[0]}" cy="${pts.at(-1)[1]}" r="3.6" fill="#2a6fdb" stroke="#fff" stroke-width="1.6"/>`;
  return svg;
}
function stat(label, value, sub, tone = "") {
  return el("div", { class: `place-stat ${tone}` }, el("span", { text: label }), el("b", { text: value }), el("small", { text: sub }));
}

async function showPlace(name) {
  const card = $("place-card"), info = name && PLACES[name];
  const ask = ++placeAsk;
  if (!info || !document.body.classList.contains("mode-intro")) { card.hidden = true; layout(); return; }
  let p;
  try { p = await api(`/api/place?names=${encodeURIComponent(info.names.join(","))}`); } catch { p = { available: false }; }
  if (ask !== placeAsk) return;   // 그사이 다른 곳을 눌렀으면 버린다
  const where = info.label || info.names.join("·");
  const head = el("div", { class: "place-card__head" },
    el("span", { class: "place-card__kind", text: info.kind }),
    el("button", { type: "button", class: "place-card__close", "aria-label": "닫기", text: "×", onclick: () => scene.resetZoom() }));
  const top = placePhoto(name, head) || head;   // 사진이 있으면 종류 표시와 닫기 단추를 사진 위에 얹는다
  if (!p.available) {
    card.replaceChildren(top, el("h3", { text: name }), el("p", { class: "place-card__note", text: info.note }),
      el("p", { class: "place-card__empty", text: "관광공사 공개 소비 데이터가 없어 수치를 보여 줄 수 없습니다." }));
    card.hidden = false;
    return;
  }
  const v = verdict(p);
  const whole = !info.names.length;
  const stats = [
    stat("외국인 관광객 소비", signed(p.spend_yoy), "전년 같은 달 대비", p.spend_yoy < 0 ? "is-down" : ""),
    stat("외국인 방문객", signed(p.visitors_yoy), "전년 같은 달 대비", p.visitors_yoy < 0 ? "is-down" : ""),
    whole ? stat("외국인 소비 비중", p.foreign_share == null ? "–" : `${p.foreign_share.toFixed(1)}%`, "개인 카드 소비 중")
      : stat("낙수 지수", p.conversion_index == null ? "–" : p.conversion_index.toFixed(2), "방문 대비 소비 · 1 = 평균", v ? v[0] : ""),
    whole ? stat("읍면동", "43곳", "진단 화면에서 비교")
      : stat("가게당 관광객 소비", p.store_yield == null ? "–" : p.store_yield.toFixed(2), "가게 수 대비 · 1 = 평균", p.store_yield != null && p.store_yield < 0.8 ? "is-low" : ""),
  ];
  const months = p.trend.length ? `${monthText(p.trend[0].month)} ~ ${monthText(p.trend.at(-1).month)}` : "";
  const area = p.areas?.[0];
  card.replaceChildren(...[   // replaceChildren 은 null 을 글자로 넣으므로 빈 항목을 거른다
    top,
    el("h3", { text: name }),
    el("p", { class: "place-card__scope", text: `${where === name ? "" : `${where} · `}${monthText(p.month)} · 관광공사 공개 데이터` }),
    v ? el("p", { class: `place-card__verdict ${v[0]}`, text: v[1] }) : null,
    el("div", { class: "place-card__stats" }, ...stats),
    el("div", { class: "place-card__trend" }, el("span", { text: `외국인 관광객 소비 흐름 · ${months}` }), sparkline(p.trend)),
    el("p", { class: "place-card__note", text: info.note }),
    area && state.ready ? el("button", { type: "button", class: "place-card__go", onclick: () => openPlace(area) }, "진단 화면에서 이 동네 보기", el("i", { "aria-hidden": "true", text: "→" })) : null,
    el("p", { class: "place-card__source", text: `출처: ${p.source}. BC카드 결제·SKT 방문 기준이며 현금·해외 간편결제는 빠집니다.` }),
  ].filter(Boolean));
  card.hidden = false;
  card.classList.remove("is-in"); void card.offsetWidth; card.classList.add("is-in");
  layout();
}

async function openPlace(area) {
  scene.resetZoom();
  state.metric = 4;   // 낙수 지수: 공개 데이터면 읍면동 단위로 보인다
  // 카드와 같은 조건(업종 전체, 외국인 관광객)으로 맞춰 수치가 같게 보이게 한다
  $("biz").value = "";
  $("segment").value = "외국인";
  $("level").value = "bjd";
  state.home = false;
  renderHomeButtons();
  setMode(true);
  layout();
  await diagnose();
  select(`${area.dstrct_se}|${area.bjd}`, false);
}
$("zoom-back").addEventListener("click", () => scene.resetZoom());

// ---------- 질문 ----------
const EXAMPLES = ["외국인 소비가 가장 많이 늘어난 동네는 어디인가요?", "크루즈 입항이 많은 달에 음식점 매출도 늘었나요?", "내국인 관광객이 줄어든 만큼 외국인이 채우고 있나요?", "우도·성산의 쓰레기 부담은 얼마나 늘었나요?"];

function showAnswer(open) {
  $("answer").hidden = !open;
  $("examples").hidden = open;
  $("ask").classList.toggle("is-answering", open);
  showDong(state.selected);   // 답변 창이 열려 있는 동안은 동네 카드를 접어 둔다
  layout();   // 열린 답변 창이 가리지 않게 섬을 옮긴다
}

async function applyView(view) {
  if (!view) return;
  $("month").value = view.month;
  $("level").value = view.level;
  $("biz").value = [...$("biz").options].some((option) => option.value === view.biz) ? view.biz : "";
  $("segment").value = view.segment;
  state.metric = view.tab;
  await diagnose();
  if (view.focus) select(rowKey(view.focus), false);
}

function renderAnswer(event) {
  $("answer-text").textContent = event.text;
  $("answer-note").textContent = event.note || "";
  $("answer-note").hidden = !event.note;
  $("answer-warn").textContent = event.unverified.length ? `이 수치는 계산 결과에서 확인되지 않았습니다: ${event.unverified.join(", ")}` : "";
  $("answer-warn").hidden = !event.unverified.length;
  $("answer-sources").textContent = event.sources.length ? `출처: ${event.sources.join(" / ")}` : "";
  $("answer-sources").hidden = !event.sources.length;
  $("answer-report").hidden = !event.report_id;
  if (event.report_id) $("answer-report").href = `/api/report/${event.report_id}`;
}

function renderStep(event) {
  // 계획(Agent 가 쓴 다음 할 일), 도구 실행, 수치 검증을 구분해 보여준다
  const kind = event.kind || "tool";
  const item = el("li", { class: `step step--${kind}${event.ok === false ? " is-warn" : ""}` },
    el("span", { class: "step__tag", text: kind === "plan" ? "계획" : kind === "check" ? "검증" : "실행" }),
    el("span", { text: event.label.replace(/^계획:\s*/, "") }));
  $("steps").append(item);
}

function renderConversation(event) {
  // 이어지는 질문: 몇 번째 질문인지 보여주고 새 대화로 돌아갈 수 있게 한다
  const box = $("answer-thread");
  if (!event || !event.turn || event.turn < 2) { box.hidden = true; return; }
  box.replaceChildren(el("span", { text: `앞선 질문 ${event.turn - 1}개를 이어서 답했습니다` }),
    el("button", { type: "button", class: "link", text: "새 대화", onclick: () => { state.conversation = null; box.hidden = true; $("ask-input").placeholder = "예: 연동 면세점 매출은 작년보다 늘었나요?"; } }));
  box.hidden = false;
}

async function askQuestion(question) {
  return streamAgent("/api/ask", { question, conversation: state.conversation || null });
}

async function runBrief() {
  return streamAgent("/api/brief", null);
}

// Agent 가 일하는 동안 지난 시간과 예상 시간을 보여 준다 (질문 보통 20~40초, 자동 브리핑 1분 안팎)
let activeProgress = null;
function startProgress(brief) {
  activeProgress?.stop();
  const box = $("answer-progress"), bar = box.querySelector("i"), text = box.querySelector(".progress__text");
  const expected = brief ? 60 : 30, range = brief ? "보통 1분 안팎" : "보통 20~40초";
  const started = performance.now();
  let steps = 0, checking = false;
  const seconds = () => Math.round((performance.now() - started) / 1000);
  const tick = () => {
    const sec = seconds();
    // 처음엔 빠르게, 예상 시간에 가까울수록 천천히 차오른다. 검증 단계에 들어가면 거의 끝
    const ratio = checking ? 0.93 : Math.min(0.88, 1 - Math.exp((-1.6 * sec) / expected));
    bar.style.width = `${Math.round(ratio * 100)}%`;
    const phase = checking ? "답의 수치를 검증하는 중" : steps ? `${steps}단계 진행` : "계획을 세우는 중";
    text.textContent = sec > expected * 2 ? `${phase} · ${sec}초 지남 · 평소보다 오래 걸립니다. 조금만 더 기다려 주세요` : `${phase} · ${sec}초 지남 · ${range} 걸립니다`;
  };
  box.hidden = false;
  box.classList.remove("is-done");
  tick();
  const timer = setInterval(tick, 1000);
  const progress = {
    step(event) { steps += 1; if (event.kind === "check") checking = true; tick(); },
    done() { clearInterval(timer); bar.style.width = "100%"; box.classList.add("is-done"); const sec = seconds(); text.textContent = sec < 1 ? "바로 답했습니다" : `${sec}초 걸려 답했습니다`; },
    stop() { clearInterval(timer); box.hidden = true; },
  };
  activeProgress = progress;
  return progress;
}

async function streamAgent(url, body) {
  const send = $("ask-send"), briefButton = $("brief");
  send.disabled = true;
  if (briefButton) briefButton.disabled = true;
  $("steps").replaceChildren();
  $("answer-title").textContent = url === "/api/brief" ? "Agent 자동 브리핑" : "";
  $("answer-title").hidden = url !== "/api/brief";
  renderAnswer({ text: "", note: "", unverified: [], sources: [] });
  renderConversation(null);
  showAnswer(true);
  const progress = startProgress(url === "/api/brief");
  try {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : "{}" });
    if (!response.ok) throw new Error(url === "/api/brief" ? "브리핑을 만들지 못했습니다. 데이터를 확인해 주세요." : "질문을 처리하지 못했습니다. 다시 시도해 주세요.");
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    let answered = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines.filter(Boolean)) {
        const event = JSON.parse(line);
        if (event.type === "step") { renderStep(event); progress.step(event); }
        else {
          answered = true;
          progress.done();
          renderAnswer(event);
          if (event.title) $("answer-title").textContent = event.title;
          if (event.conversation) {
            state.conversation = event.conversation;
            $("ask-input").placeholder = "이어서 물어보세요. 예: 그럼 거기 음식점은요?";
          }
          renderConversation(event);
          await applyView(event.view);
        }
      }
    }
    if (!answered) throw new Error("답을 받지 못했습니다.");
  } catch (error) {
    // 서버가 꺼졌거나 연결이 끊긴 경우. 브라우저의 영문 오류 대신 무엇을 하면 되는지 알려 준다
    const lost = error instanceof TypeError || error.message === "답을 받지 못했습니다.";
    const note = lost ? "답을 받지 못했습니다. 프로그램 창(검은 창)이 닫혔다면 run.bat 을 다시 실행한 뒤 이 화면을 새로 고쳐 주세요." : error.message;
    renderAnswer({ text: "", note, unverified: [], sources: [] });
    progress.stop();
  } finally {
    send.disabled = false;
    if (briefButton) briefButton.disabled = false;
  }
}

$("examples").replaceChildren(
  el("button", { type: "button", id: "brief", class: "examples__brief", title: "질문하지 않아도 Agent 가 스스로 문제 동네를 찾고 원인을 확인해 브리핑을 씁니다", onclick: () => runBrief() },
    el("span", { "aria-hidden": "true", text: "✦" }), "이번 달 자동 브리핑"),
  ...EXAMPLES.map((text) => el("button", { type: "button", text, onclick: () => { $("ask-input").value = text; askQuestion(text); } })));
$("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("ask-input").value.trim();
  if (question) askQuestion(question);
});
$("answer-close").addEventListener("click", () => showAnswer(false));

// ---------- 흐름 ----------
async function diagnose() {
  const question = { month: $("month").value, level: $("level").value, biz: $("biz").value, segment: $("segment").value };
  const [result, summary] = await Promise.all([
    api("/api/diagnose", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(question) }),
    api(`/api/summary?month=${question.month}`),
  ]);
  state.metrics = result.metrics;
  renderTiles(summary.tiles);
  renderBoard();
}

async function refresh() {
  const status = (state.status = await api("/api/status"));
  const ready = status.months.length > 0;
  const first = state.ready === null;
  const changed = ready !== state.ready || first;
  state.ready = ready;
  // 처음 열 때는 데이터가 있어도 메인 화면부터 보여준다. 데이터를 새로 불러오면 진단 화면으로 간다
  if (changed) state.home = first && ready;
  renderHomeButtons();
  renderDatasets(status.needed);
  $("mock-badge").hidden = !status.mock;
  $("mock").hidden = status.mock;
  if (changed) setMode(ready && !state.home);
  layout();
  if (!ready) {
    $("tiles").replaceChildren();
    scene.setColumns([]);
    return;
  }
  renderQuestion(status);
  if (!state.home) await diagnose();   // 메인 화면에 있는 동안에는 진단을 미뤄 둔다
}

function layout() {
  const board = $("board"), data = $("data-panel"), ask = $("ask"), intro = $("empty");
  if (!state.ready || state.home) {   // 시작 화면: 제목 오른쪽 공간에 섬을 둔다
    const place = $("place-card");
    const right = !place.hidden ? place.getBoundingClientRect().right + 16   // 지명 카드가 떠 있으면 그 오른쪽에 섬을 둔다
      : intro.hidden || document.body.classList.contains("is-zoomed") ? 0 : intro.getBoundingClientRect().right;
    scene.setInsets(Math.max(right * 0.85, data.hidden ? 0 : data.offsetWidth + 24), 0);
    return;
  }
  const answering = !ask.hidden && !$("answer").hidden;
  const dong = $("dong-card");
  if (!dong.hidden && !ask.hidden) dong.style.maxHeight = `${Math.max(220, ask.getBoundingClientRect().top - dong.getBoundingClientRect().top - 14)}px`;
  const left = answering ? ask.offsetWidth + 24 : !dong.hidden ? dong.offsetWidth + 24 : data.hidden ? 0 : data.offsetWidth + 24;
  scene.setInsets(left, board.hidden ? 0 : board.offsetWidth + 40);
}
function showData(open) {
  if (open && !$("dong-card").hidden) select(null, false);   // 데이터 창과 동네 카드가 겹치지 않게
  $("data-panel").hidden = !open;
  $("data-toggle").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("data-open", open);
  layout();
}

/** 시작 화면 ↔ 진단 화면 전환. 제목이 빠지고, 카메라가 내려오고, 패널이 차례로 들어온다. */
let entering = null;
let leaving = null;
function setMode(ready) {
  document.body.classList.toggle("mode-intro", !ready);
  document.body.classList.toggle("mode-data", ready);
  clearTimeout(entering);
  if (ready) {
    const intro = $("empty");
    if (!intro.hidden && !reduceMotion) {
      intro.classList.add("is-leaving");
      leaving = setTimeout(() => { intro.hidden = true; intro.classList.remove("is-leaving"); }, 650);
    } else intro.hidden = true;
    showData(false);
    document.body.classList.add("is-entering");
    entering = setTimeout(() => document.body.classList.remove("is-entering"), 3200);
  } else {
    clearTimeout(leaving);   // 진단 화면으로 가다가 바로 돌아와도 소개 글이 숨지 않게
    $("empty").classList.remove("is-leaving");
    $("empty").hidden = false;
    showData(false);   // 시작 화면에서는 제목을 가리지 않게 접어 둔다. "센터 CSV 넣기"로 연다
    document.body.classList.remove("is-entering");
  }
  $("board").hidden = $("question").hidden = $("ask").hidden = !ready;
  scene.setMode(ready ? "data" : "intro");
}
$("data-toggle").addEventListener("click", () => showData($("data-panel").hidden));
$("intro-data").addEventListener("click", () => { showData(true); $("files").click(); });
window.addEventListener("resize", layout);
$("question").addEventListener("change", diagnose);
$("question").addEventListener("submit", (event) => event.preventDefault());
$("files").addEventListener("change", (event) => { addFiles([...event.target.files]); event.target.value = ""; });
$("mock").addEventListener("click", (event) => loadMock(event.currentTarget));
$("mock-start").addEventListener("click", (event) => (state.ready ? leaveHome() : loadMock(event.currentTarget)));
$("home").addEventListener("click", goHome);

/** 메인 화면으로: 불러온 데이터는 그대로 두고 화면만 바꾼다 */
function goHome() {
  if (!state.ready || state.home) return;
  state.home = true;
  scene.setColumns([]);
  renderHomeButtons();
  setMode(false);
  layout();
}
function leaveHome() {
  state.home = false;
  renderHomeButtons();
  setMode(true);
  layout();
  if (state.metrics.length) renderBoard(); else diagnose();
}
/** 데이터가 이미 있으면 메인 화면의 첫 단추가 "진단 화면으로"가 된다 */
function renderHomeButtons() {
  const button = $("mock-start");
  button.firstChild.textContent = state.ready ? "진단 화면으로" : "가상 데이터로 시작";
}

// ---------- 시작 화면의 "바로 해 보기": 기능 네 가지를 한 번에 열어 본다 ----------
async function enterDiagnosis() {
  if (!state.ready) return false;
  if (state.home) { state.home = false; renderHomeButtons(); setMode(true); layout(); }
  if (state.metrics.length) renderBoard(); else await diagnose();
  return true;
}
async function tryFeature(kind) {
  if (!(await enterDiagnosis())) return;
  if (kind === "ask") { setTimeout(() => $("ask-input").focus(), 900); return; }
  if (kind === "brief") { runBrief(); return; }
  state.metric = SUMMARY; state.flip = false; state.pin = null;
  renderBoard();
  const top = summarize().list;   // 여러 지표에서 함께 걸린 동네부터
  if (!top.length) return;
  if (kind === "compare" && top.length > 1) state.pin = top[0].key;
  select(kind === "compare" && top.length > 1 ? top[1].key : top[0].key, false);
}
for (const button of document.querySelectorAll("[data-feature]")) button.addEventListener("click", () => tryFeature(button.dataset.feature));

const drop = $("drop");
for (const type of ["dragenter", "dragover"]) drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("is-over"); });
for (const type of ["dragleave", "drop"]) drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.remove("is-over"); });
drop.addEventListener("drop", (event) => addFiles([...event.dataTransfer.files]));

$("notes").title = "눌러서 전부 보기";
$("notes").addEventListener("click", () => $("notes").classList.toggle("is-open"));
refresh().catch((error) => log(error.message, true));
