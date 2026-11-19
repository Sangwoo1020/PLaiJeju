// 보기 전용 데모 사이트에서 화면의 서버 요청(/api/...)을 미리 계산해 둔 파일로 돌려준다.
// tools/build_static_demo.py 가 demo/shim.js 로 복사해 index.html 맨 앞에서 불러온다. 실제 프로그램에서는 쓰이지 않는다.
(() => {
  const realFetch = window.fetch.bind(window);
  const BASE = new URL("..", document.currentScript.src).pathname;   // 사이트가 놓인 경로 (예: /PLaiJeju/)
  const GITHUB = "https://github.com/Sangwoo1020/PLaiJeju";
  let indexPromise;
  const index = () => (indexPromise ||= realFetch(`${BASE}demo-data/index.json`).then((r) => r.json()));
  const load = async (key) => { const file = (await index())[key]; return file ? (await realFetch(`${BASE}demo-data/${file}`)).json() : null; };
  const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const stream = (events, gap = 550) => new Response(new ReadableStream({
    async start(controller) {
      const encoder = new TextEncoder();
      for (const event of events) { await sleep(event.type === "answer" ? 700 : gap); controller.enqueue(encoder.encode(JSON.stringify(event) + "\n")); }
      controller.close();
    },
  }), { headers: { "Content-Type": "application/x-ndjson" } });

  function toast(text) {
    let box = document.getElementById("demo-toast");
    if (!box) { box = Object.assign(document.createElement("div"), { id: "demo-toast" }); document.body.append(box); }
    box.textContent = text; box.classList.add("on");
    clearTimeout(box.timer); box.timer = setTimeout(() => box.classList.remove("on"), 6000);
  }

  async function answerFor(path, body) {
    if (path === "/api/brief") return stream((await load("brief")) || [], 450);
    const answers = (await load("answers")) || {};
    const question = (body.question || "").trim();
    const found = answers[question];
    if (found) return stream([...found.steps, found.answer]);
    const list = Object.keys(answers).map((q, i) => `${i + 1}. ${q}`).join("\n");
    return stream([
      { type: "step", kind: "plan", label: "데모 사이트에서는 미리 받아 둔 실제 답만 보여 줄 수 있습니다" },
      { type: "answer", mode: "offline", text: `이 데모 사이트는 서버와 API 키 없이 돌아가서 새 질문을 Agent 에게 보낼 수 없습니다.\n\n아래 질문은 실제 Claude 로 받은 답을 그대로 다시 보여 줍니다. 질문창에 그대로 넣거나 예시 단추를 눌러 보세요.\n\n${list}\n\n직접 질문하려면 GitHub 에서 프로그램을 내려받아 실행하세요: ${GITHUB}`, note: "데모 사이트 안내", unverified: [], sources: [] },
    ], 300);
  }

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return realFetch(input, init);
    const path = url.pathname;
    const body = init.body ? JSON.parse(init.body) : {};
    if (path === "/api/ask" || path === "/api/brief") return answerFor(path, body);
    if (path === "/api/upload" || path === "/api/load-mock") return json({ detail: { message: "데모 사이트에서는 파일을 불러올 수 없습니다. 센터 CSV 넣기는 실제 프로그램(GitHub)에서 해 보세요." } }, 400);
    if (path === "/api/status") return json(await load("status"));
    if (path === "/api/summary") return json((await load(`summary|${url.searchParams.get("month")}`)) || { tiles: [] });
    if (path === "/api/place") return json((await load(`place|${url.searchParams.get("names") || ""}`)) || { available: false });
    if (path === "/api/diagnose") {
      const { month, level, biz = "", segment } = body;
      const status = await load("status");
      const tries = [[month, level, biz, segment, null], [month, level, "", segment, "이 업종은 데모에 미리 계산해 두지 않아 '모든 업종'으로 보여 줍니다"], [status.months.at(-1), "bjd", "I56", "외국인", "이 조건은 데모에 미리 계산해 두지 않아 기본 조건으로 보여 줍니다"]];
      for (const [m, l, b, s, note] of tries) {
        const found = await load(`diagnose|${m}|${l}|${b}|${s}`);
        if (found) { if (note) toast(note); return json(found); }
      }
      return json({ detail: { message: "데모에 없는 조건입니다." } }, 404);
    }
    return json({ detail: { message: "데모 사이트에서는 쓸 수 없는 기능입니다." } }, 404);
  };

  // 보고서 내려받기 링크는 미리 만들어 둔 zip 으로 돌린다
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[href^='/api/report/']");
    if (!link) return;
    event.preventDefault();
    location.href = `${BASE}demo-data/reports/${link.getAttribute("href").split("/").pop()}.zip`;
  }, true);

  // 데모 표시: 로고 옆 작은 표시(GitHub 링크)와, 처음 열 때 잠깐 뜨는 안내. 화면의 다른 패널을 가리지 않게 둔다
  const style = document.createElement("style");
  style.textContent = `
    .demo-badge { margin-left: 4px; padding: 3px 9px; border-radius: 999px; background: #1a4fa8; color: #fff !important; font-size: 11.5px; font-weight: 800; text-decoration: none; white-space: nowrap; }
    .demo-badge:hover { background: #ff7a1a; }
    #demo-toast { position: fixed; left: 50%; top: 18px; transform: translate(-50%, -12px); z-index: 70; max-width: min(640px, 90vw); padding: 10px 18px; border-radius: 14px; background: rgba(20,40,74,.92); color: #fff;
      font: 600 13.5px/1.5 Pretendard, "Apple SD Gothic Neo", sans-serif; text-align: center; opacity: 0; transition: opacity .3s, transform .3s; pointer-events: none; box-shadow: 0 8px 24px rgba(0,0,0,.2); }
    #demo-toast.on { opacity: 1; transform: translate(-50%, 0); }`;
  document.head.append(style);
  addEventListener("DOMContentLoaded", () => {
    const badge = Object.assign(document.createElement("a"), { className: "demo-badge", href: GITHUB, target: "_blank", rel: "noopener", textContent: "데모 · GitHub",
      title: "보기 전용 데모입니다. 공개 실데이터(최근 3개월)로 미리 계산한 화면이며, Agent 답은 실제 Claude 로 받은 답을 다시 보여 줍니다." });
    document.getElementById("data-toggle")?.before(badge);
    setTimeout(() => toast("보기 전용 데모입니다. 공개 실데이터(최근 3개월)로 미리 계산했고, Agent 답은 실제 Claude 답을 다시 보여 줍니다."), 1200);
  });
})();
