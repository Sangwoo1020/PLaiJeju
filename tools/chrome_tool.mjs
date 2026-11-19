// 문서 제작 도구: Chrome 을 자동으로 띄워 화면을 캡처하거나 HTML 을 PDF 로 만든다. 앱 실행에는 쓰이지 않는다.
//   node tools/chrome_tool.mjs shots <저장 폴더>          실행 중인 앱(localhost:8765)을 캡처. Claude 질문 1건 포함
//   node tools/chrome_tool.mjs pdf <입력.html> <출력.pdf>  HTML 을 A4 PDF 로 저장
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;
const APP = process.env.APP_URL || "http://localhost:8765";
const LIMIT_SECONDS = Number(process.env.CAPTURE_LIMIT || 240);
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function connect() {
  for (let i = 0; i < 60; i++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = pages.find((item) => item.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {
      /* Chrome 이 아직 뜨는 중 */
    }
    await sleep(250);
  }
  throw new Error("Chrome 에 연결하지 못했습니다");
}

function session(url) {
  const socket = new WebSocket(url);
  const waiting = new Map();
  let id = 0;
  const listeners = {};
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id) { (listeners[message.method] || []).forEach((handler) => handler(message.params)); return; }
    const { resolve: done, reject } = waiting.get(message.id);
    waiting.delete(message.id);
    message.error ? reject(new Error(message.error.message)) : done(message.result);
  });
  const opened = new Promise((done) => socket.addEventListener("open", done));
  return {
    opened,
    send: (method, params = {}) => new Promise((done, reject) => {
      waiting.set(++id, { resolve: done, reject });
      socket.send(JSON.stringify({ id, method, params }));
    }),
    on: (method, handler) => { (listeners[method] ||= []).push(handler); },
    close: () => socket.close(),
  };
}

async function withChrome(run) {
  const profile = mkdtempSync(join(tmpdir(), "golmok-chrome-"));   // 사용자의 Chrome 프로필은 건드리지 않는다
  const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--hide-scrollbars", "--no-first-run", "about:blank"], { stdio: "ignore" });
  // 시간이 넘으면 띄운 Chrome 을 닫고 끝낸다 (남은 Chrome 이 다음 캡처의 포트를 막지 않게)
  const limit = setTimeout(() => { console.error(`시간 초과(${LIMIT_SECONDS}초)`); chrome.kill(); rmSync(profile, { recursive: true, force: true }); process.exit(2); }, LIMIT_SECONDS * 1000);
  try {
    const cdp = session(await connect());
    await cdp.opened;
    await run(cdp);
    cdp.close();
  } finally {
    clearTimeout(limit);
    chrome.kill();
    await sleep(500);
    rmSync(profile, { recursive: true, force: true });
  }
}

async function evaluate(cdp, expression) {
  const { result, exceptionDetails } = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
  return result.value;
}

/** 조건이 참이 될 때까지 기다린다 (최대 seconds 초) */
async function until(cdp, expression, seconds = 90) {
  for (let i = 0; i < seconds * 2; i++) {
    if (await evaluate(cdp, expression).catch(() => false)) return;
    await sleep(500);
  }
  throw new Error(`기다리다 시간이 지났습니다: ${expression}`);
}

async function shot(cdp, path) {
  const { data } = await cdp.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(path, Buffer.from(data, "base64"));
  console.log("저장:", path);
}

/** 데이터가 들어 있으면 시작 화면이 먼저 뜬다: 장면이 준비되면 '진단 화면으로'를 눌러 진단 화면으로 들어간다. */
async function enterData(cdp) {
  await until(cdp, "document.getElementById('scene').classList.contains('is-ready') && (document.querySelectorAll('#rows .row').length > 0 || document.getElementById('mock-start').textContent.includes('진단'))", 300);
  await evaluate(cdp, "document.body.classList.contains('mode-intro') && document.getElementById('mock-start').click()");
  await until(cdp, "document.querySelectorAll('#rows .row').length > 0 && document.querySelectorAll('.marker').length > 0", 300);
}

async function takeShots(folder) {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    await enterData(cdp);
    await sleep(6500);   // 카메라가 내려오고 기둥이 다 솟을 때까지
    await shot(cdp, join(folder, "app_home.png"));

    await evaluate(cdp, `(async () => {
      const level = document.getElementById('level'); level.value = 'dstrct_se'; level.dispatchEvent(new Event('change', {bubbles: true}));
      await new Promise(r => setTimeout(r, 1500));
      [...document.querySelectorAll('#tabs button')].find(b => b.textContent === '낙수 지수').click();
    })()`);
    await sleep(4000);
    await shot(cdp, join(folder, "app_tourism.png"));

    await evaluate(cdp, `(() => {
      document.getElementById('ask-input').value = process_question;
      document.getElementById('ask-form').requestSubmit();
    })()`.replace("process_question", JSON.stringify(process.env.QUESTION || "외국인 관광객이 늘었는데 돈은 어느 동네에 떨어지나요? 크루즈 효과와 쓰레기 부담도 같이 봐 주세요")));
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      if (await evaluate(cdp, "!document.getElementById('answer-report').hidden")) break;
    }
    await sleep(5000);
    const answer = await evaluate(cdp, `({ steps: [...document.querySelectorAll('#steps li')].map(l => l.textContent), text: document.getElementById('answer-text').textContent, note: document.getElementById('answer-note').textContent, warn: document.getElementById('answer-warn').textContent, report: document.getElementById('answer-report').getAttribute('href') })`);
    writeFileSync(join(folder, "app_answer.json"), JSON.stringify(answer, null, 1));
    await shot(cdp, join(folder, "app_answer.png"));
  });
}

/** 페이지 오류와 화면 상태를 출력한다 (화면이 이상할 때 원인 찾기) */
async function pageState() {
  await withChrome(async (cdp) => {
    const errors = [];
    cdp.on?.("Runtime.exceptionThrown", (event) => errors.push(event.exceptionDetails?.exception?.description || event.exceptionDetails?.text));
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    await sleep(25000);
    console.log(await evaluate(cdp, "JSON.stringify({ body: document.body.className, empty: document.getElementById('empty').hidden, data: document.getElementById('data-panel').hidden, ready: document.getElementById('scene').className, places: document.querySelectorAll('button.place').length, pins: document.querySelectorAll('button.landmark').length, log: document.getElementById('log').textContent, rows: document.querySelectorAll('#rows .row').length, scrim: getComputedStyle(document.querySelector('.scrim')).opacity })"));
    console.log("오류:", errors.join("\n") || "없음");
  });
}

/** 메인 화면 ↔ 진단 화면 오가기 확인 (데이터가 들어 있는 서버에서) */
async function homeFlow(path) {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    const look = "JSON.stringify({ body: document.body.className.replace('is-entering', '').trim(), intro: !document.getElementById('empty').hidden, button: document.getElementById('mock-start').textContent.trim(), rows: document.querySelectorAll('#rows .row').length, board: !document.getElementById('board').hidden })";
    await until(cdp, "!document.getElementById('empty').hidden && document.getElementById('scene').classList.contains('is-ready')", 300);
    console.log("처음 열 때:", await evaluate(cdp, look));
    await evaluate(cdp, "document.getElementById('mock-start').click()");
    await until(cdp, "document.querySelectorAll('#rows .row').length > 0", 300);
    console.log("진단 화면으로:", await evaluate(cdp, look));
    await evaluate(cdp, "document.getElementById('home').click()");
    await sleep(3000);
    console.log("제목 누름:", await evaluate(cdp, look));
    if (path) { await sleep(6000); await shot(cdp, path); }
  });
}

/** 데이터를 넣기 전 시작 화면. 서버에 데이터가 없을 때 찍는다. */
async function takeIntro(path) {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: Number(process.env.W || 1920), height: Number(process.env.H || 1080), deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    await until(cdp, "document.body.classList.contains('mode-intro') && !document.getElementById('empty').hidden && document.querySelectorAll('button.landmark').length > 0 && document.getElementById('scene').classList.contains('is-ready')");
    await sleep(7000);
    await shot(cdp, path);
  });
}

/** 도시 모드: 도심 동네를 골라 실제 건물이 보이도록 확대한 화면 (데이터가 들어 있어야 한다) */
async function takeCity(path, place = "연동") {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    await enterData(cdp);
    await sleep(6000);
    await evaluate(cdp, `[...document.querySelectorAll('#rows .row')].find(r => r.textContent.startsWith(${JSON.stringify(place)})).click()`);
    await sleep(5000);
    await shot(cdp, path);
  });
}

/** 지표 탭 하나: 데이터를 넣은 뒤 탭 이름으로 골라 찍는다. level 을 주면 집계 단위도 바꾼다. */
async function takeTab(path, tab, level) {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: Number(process.env.W || 1920), height: Number(process.env.H || 1080), deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    await enterData(cdp);
    await sleep(5000);
    if (level) {
      await evaluate(cdp, `(() => { const l = document.getElementById('level'); l.value = ${JSON.stringify(level)}; l.dispatchEvent(new Event('change', {bubbles: true})); })()`);
      await sleep(2000);
    }
    await evaluate(cdp, `[...document.querySelectorAll('#tabs button')].find(b => b.textContent === ${JSON.stringify(tab)}).click()`);
    await sleep(5000);
    await shot(cdp, path);
  });
}

/** 시작 화면의 '바로 해 보기' 단추(dong, compare)를 눌러 연 진단 화면. API 를 부르지 않는다 */
async function takeFeature(path, kind, first, second) {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: Number(process.env.W || 1920), height: Number(process.env.H || 1080), deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    await until(cdp, "document.body.classList.contains('mode-intro') && document.getElementById('scene').classList.contains('is-ready') && document.getElementById('mock-start').textContent.includes('진단')", 300);
    await sleep(1500);
    await evaluate(cdp, `document.querySelector('[data-feature=${JSON.stringify(kind)}]').click()`);
    await until(cdp, "!document.getElementById('dong-card').hidden && document.querySelectorAll('.marker').length > 0", 300);
    if (first) {   // 동네를 직접 고른다: 낙수 지수 목록에서 first, 비교면 second 를 더 고른다
      const pick = (name) => `[...document.querySelectorAll('#rows .row')].find(r => r.querySelector('b')?.textContent === ${JSON.stringify(name)}).click()`;
      await sleep(1500);
      await evaluate(cdp, "[...document.querySelectorAll('#tabs button')].find(b => b.textContent === '낙수 지수').click()");
      await sleep(2500);
      await evaluate(cdp, pick(first));
      if (second) { await sleep(1500); await evaluate(cdp, "document.querySelector('.dong-card__pin').click()"); await evaluate(cdp, pick(second)); }
    }
    await sleep(6500);
    await shot(cdp, path);
  });
}

/** 시작 화면에서 지명을 눌러 당겨 본 화면 */
async function takeIntroZoom(path, place, landmark) {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    await until(cdp, "document.body.classList.contains('mode-intro') && !document.getElementById('empty').hidden && document.querySelectorAll('button.landmark').length > 0 && document.getElementById('scene').classList.contains('is-ready')");
    await sleep(5000);
    await evaluate(cdp, `[...document.querySelectorAll('button.place')].find(b => b.textContent === ${JSON.stringify(place)}).click()`);
    await sleep(14000);   // 헤드리스는 GPU 없이 그려서 느리다
    if (landmark) {   // 이어서 랜드마크 이름표를 눌러 설명을 펼친다
      await evaluate(cdp, `[...document.querySelectorAll('button.landmark')].find(b => b.querySelector('b').textContent === ${JSON.stringify(landmark)}).click()`);
      await sleep(14000);
    }
    await shot(cdp, path);
  });
}

async function takeCover(path) {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1240, height: 1754, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: APP });
    await until(cdp, "document.querySelectorAll('.marker').length > 0");
    await sleep(2000);
    await evaluate(cdp, `(() => {
      for (const id of ['board', 'ask', 'data-panel']) document.getElementById(id).hidden = true;
      const style = document.createElement('style');
      style.textContent = '.glass, .top, #tiles, #question, .place-names { display: none !important; }';
      document.head.append(style);
      window.dispatchEvent(new Event('resize'));
    })()`);
    await sleep(3500);
    await shot(cdp, path);
  });
}

async function makePdf(input, output) {
  await withChrome(async (cdp) => {
    await cdp.send("Page.enable");
    await cdp.send("Page.navigate", { url: pathToFileURL(resolve(input)).href });
    await sleep(2500);
    // 쪽 밖으로 넘친 내용이 있으면 알려준다 (아래 여백 15mm 안쪽까지 허용)
    const overflow = await evaluate(cdp, `[...document.querySelectorAll('.page:not(.cover)')].map((page, index) => {
      const limit = page.getBoundingClientRect().bottom - 57;
      const bottom = Math.max(...[...page.children].filter(el => !el.classList.contains('foot')).map(el => el.getBoundingClientRect().bottom));
      return { page: index + 2, spare: Math.round(limit - bottom) };
    })`);
    console.log("쪽별 남은 높이(px):", overflow.map((p) => p.page + ":" + p.spare).join(" "));
    const { data } = await cdp.send("Page.printToPDF", { printBackground: true, preferCSSPageSize: true, displayHeaderFooter: false });
    writeFileSync(output, Buffer.from(data, "base64"));
    console.log("저장:", output);
  });
}

const [command, ...args] = process.argv.slice(2);
if (command === "shots") await takeShots(args[0]);
else if (command === "cover") await takeCover(args[0]);
else if (command === "intro") await takeIntro(args[0]);
else if (command === "city") await takeCity(args[0], args[1]);
else if (command === "introzoom") await takeIntroZoom(args[0], args[1], args[2]);
else if (command === "state") await pageState();
else if (command === "home") await homeFlow(args[0]);
else if (command === "tab") await takeTab(args[0], args[1], args[2]);
else if (command === "feature") await takeFeature(args[0], args[1], args[2], args[3]);
else if (command === "pdf") await makePdf(args[0], args[1]);
else console.log("사용법: node tools/chrome_tool.mjs shots <폴더> | pdf <입력.html> <출력.pdf>");
