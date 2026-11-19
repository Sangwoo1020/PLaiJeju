// 시연 영상 녹화 도구: 실행 중인 앱(localhost:8765)을 숨은 Chrome 으로 조작하며 1080p 화면을 연속으로 찍고,
// 화면 위에 자막을 띄운 채 mp4 로 합친다. YouTube 용 자막 파일(.srt)도 함께 만든다. 앱 실행에는 쓰이지 않는다.
//   node tools/record_demo.mjs <출력.mp4>
// 실제 Claude 질문 1건을 보낸다(약 $0.15). 키가 없으면 기본 모드 답이 녹화된다.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9335;
const APP = process.env.APP_URL || "http://localhost:8765";
const OUT = resolve(process.argv[2] || "dist/PLaiJeju_시연영상.mp4");
const QUESTION = process.env.QUESTION || "우도면은 사람만 늘고 돈은 안 떨어지나요?";
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

const work = mkdtempSync(join(tmpdir(), "golmok-video-"));
const frameDir = join(work, "frames");
mkdirSync(frameDir);
const profile = join(work, "profile");
const chrome = spawn(CHROME, ["--headless=new", "--use-angle=metal", "--ignore-gpu-blocklist", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });

let socketUrl;
for (let i = 0; i < 80 && !socketUrl; i++) {
  try { socketUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find((p) => p.type === "page")?.webSocketDebuggerUrl; } catch { /* 뜨는 중 */ }
  if (!socketUrl) await sleep(250);
}
const socket = new WebSocket(socketUrl);
await new Promise((done) => socket.addEventListener("open", done));
let id = 0;
const waiting = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.method === "Page.screencastFrame") { onFrame(message.params); return; }
  if (message.id && waiting.has(message.id)) { const { done, fail } = waiting.get(message.id); waiting.delete(message.id); message.error ? fail(new Error(message.error.message)) : done(message.result); }
});
const send = (method, params = {}) => new Promise((done, fail) => { waiting.set(++id, { done, fail }); socket.send(JSON.stringify({ id, method, params })); });
async function js(expression) {
  const { result, exceptionDetails } = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
  return result.value;
}
async function until(expression, seconds = 120) {
  for (let i = 0; i < seconds * 4; i++) { if (await js(expression).catch(() => false)) return; await sleep(250); }
  throw new Error(`기다리다 시간이 지났습니다: ${expression}`);
}

// ---------- 녹화: Chrome 의 화면 스트리밍(screencast)으로 받은 프레임과 그 시각, 재생 속도를 남긴다 ----------
// (화면을 한 장씩 찍는 방식은 1080p 에서 초당 2~5장뿐이라 스트리밍을 쓴다. 초당 15~19장)
const frames = [];
let recording = false;
let speed = 1;
function onFrame({ data, metadata, sessionId }) {
  socket.send(JSON.stringify({ id: ++id, method: "Page.screencastFrameAck", params: { sessionId } }));
  if (!recording) return;
  const file = join(frameDir, `${String(frames.length).padStart(6, "0")}.jpg`);
  writeFileSync(file, Buffer.from(data, "base64"));
  frames.push({ file, t: (metadata.timestamp ? metadata.timestamp * 1000 : Date.now()), speed });
}
async function startRecording() { recording = true; await send("Page.startScreencast", { format: "jpeg", quality: 72, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 }); }
async function stopRecording() { await sleep(300); recording = false; await send("Page.stopScreencast"); }
const captions = [];   // { t: 실제 시각, text }
async function caption(text) {
  captions.push({ t: Date.now(), text });
  await js(`window.__demo.caption(${JSON.stringify(text)})`);
}

// ---------- 화면에 얹는 자막·커서·제목 카드 ----------
const OVERLAY = `(() => {
  const style = document.createElement('style');
  style.textContent = \`
    #demo-cap { position: fixed; left: 50%; bottom: 46px; transform: translateX(-50%); z-index: 99999; max-width: 980px; padding: 14px 30px; border-radius: 18px;
      background: rgba(14, 32, 56, 0.82); color: #fff; font: 600 27px/1.45 Pretendard, 'Apple SD Gothic Neo', sans-serif; letter-spacing: -0.01em; text-align: center;
      box-shadow: 0 10px 40px rgba(0,0,0,.25); transition: opacity .35s; pointer-events: none; white-space: pre-line; }
    #demo-cap:empty { opacity: 0; }
    #demo-cap b { color: #ffb15c; font-weight: 800; }
    #demo-cursor { position: fixed; z-index: 100000; width: 26px; height: 26px; margin: -13px 0 0 -13px; border-radius: 50%; left: 960px; top: 560px;
      background: rgba(255,255,255,.9); border: 3px solid #2a6fdb; box-shadow: 0 4px 14px rgba(0,0,0,.3); pointer-events: none;
      transition: left .8s cubic-bezier(.4,0,.2,1), top .8s cubic-bezier(.4,0,.2,1), transform .15s; opacity: 0; }
    #demo-cursor.on { opacity: 1; } #demo-cursor.press { transform: scale(.7); }
    .demo-ripple { position: fixed; z-index: 99998; width: 70px; height: 70px; margin: -35px 0 0 -35px; border-radius: 50%; border: 3px solid #2a6fdb; pointer-events: none; animation: demo-rip .6s ease-out forwards; }
    @keyframes demo-rip { from { transform: scale(.3); opacity: .9 } to { transform: scale(1.4); opacity: 0 } }
    #demo-card { position: fixed; inset: 0; z-index: 100001; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px;
      background: radial-gradient(circle at 50% 40%, rgba(233,242,251,.86), rgba(190,218,244,.9)); backdrop-filter: blur(14px); transition: opacity .8s; font-family: Pretendard, 'Apple SD Gothic Neo', sans-serif; color: #14284a; text-align: center; }
    #demo-card.off { opacity: 0; pointer-events: none; }
    #demo-card img { width: 128px; height: 128px; border-radius: 34px; box-shadow: 0 18px 40px -16px rgba(26,79,168,.6); }
    #demo-card h1 { margin: 6px 0 0; font-size: 84px; font-weight: 850; letter-spacing: -0.04em; color: #1a4fa8; }
    #demo-card h1 em { font-style: normal; color: #ff7a1a; }
    #demo-card h2 { margin: 0; font-size: 40px; font-weight: 800; letter-spacing: -0.03em; }
    #demo-card p { margin: 0; font-size: 26px; color: #45607f; font-weight: 600; }
  \`;
  document.head.append(style);
  const cap = Object.assign(document.createElement('div'), { id: 'demo-cap' });
  const cursor = Object.assign(document.createElement('div'), { id: 'demo-cursor' });
  document.body.append(cap, cursor);
  window.__demo = {
    caption(text) { cap.innerHTML = text; },
    async point(el) {
      el.scrollIntoView?.({ block: 'nearest' });
      const r = el.getBoundingClientRect();
      cursor.classList.add('on');
      cursor.style.left = (r.left + r.width / 2) + 'px'; cursor.style.top = (r.top + r.height / 2) + 'px';
      await new Promise(done => setTimeout(done, 900));
    },
    async click(el) {
      await this.point(el);
      const r = el.getBoundingClientRect();
      const ripple = Object.assign(document.createElement('div'), { className: 'demo-ripple' });
      ripple.style.left = (r.left + r.width / 2) + 'px'; ripple.style.top = (r.top + r.height / 2) + 'px';
      document.body.append(ripple); setTimeout(() => ripple.remove(), 700);
      cursor.classList.add('press'); setTimeout(() => cursor.classList.remove('press'), 160);
      el.click();
    },
    card(html) {
      let card = document.getElementById('demo-card');
      if (!card) { card = Object.assign(document.createElement('div'), { id: 'demo-card' }); document.body.append(card); }
      card.innerHTML = html; card.classList.remove('off');
    },
    hideCard() { document.getElementById('demo-card')?.classList.add('off'); },
  };
})()`;
const LOGO = `<img src="/static/logo.svg" alt="">`;
const find = (selector, text) => `[...document.querySelectorAll(${JSON.stringify(selector)})].find(e => e.textContent.trim() === ${JSON.stringify(text)})`;
const click = (expr) => js(`window.__demo.click(${expr})`);
const point = (expr) => js(`window.__demo.point(${expr})`);

try {
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await send("Page.bringToFront");   // 앞에 있지 않은 탭은 화면을 드물게 그려 프레임이 끊긴다
  await send("Page.navigate", { url: APP });
  await until("document.body.classList.contains('mode-intro') && document.getElementById('scene').classList.contains('is-ready') && document.querySelectorAll('button.landmark').length > 0", 300);
  await js(OVERLAY);
  await js(`window.__demo.card(${JSON.stringify(`${LOGO}<h1>PL<em>ai</em>Jeju</h1><h2>제주 관광 낙수 진단 Agent</h2><p>제주 AI·빅데이터 활용 AI Agent 개발 챌린지 · DataPlayLabs</p>`)})`);
  await sleep(3500);   // 장면이 자리 잡을 때까지 (아직 녹화 전)

  await startRecording();

  // 1. 제목 → 시작 화면
  await sleep(3500);
  await js("window.__demo.hideCard()");
  await caption("외국인·크루즈 관광객은 늘었는데, <b>돈은 어느 동네에 떨어질까요?</b>");
  await sleep(5500);
  await caption("카드 소비와 유동인구를 <b>동네 단위</b>로 겹쳐, 사람 몫과 돈 몫이 어긋나는 곳을 찾습니다");
  await sleep(5000);

  // 2. 진단 화면
  await caption("센터 데이터가 없어도, 프로그램에 든 <b>공개 실데이터(2026년 9월)</b>로 바로 진단합니다");
  await click("document.getElementById('mock-start')");
  await until("document.querySelectorAll('#rows .row').length > 0 && document.querySelectorAll('.marker').length > 0", 120);
  await sleep(5500);
  await caption("여러 지표에서 <b>함께 문제로 나온 동네</b>일수록 3D 섬 위 기둥이 높고 진합니다");
  await sleep(5500);

  if (process.env.STOP_BEFORE_ASK) { await stopRecording(); throw Object.assign(new Error("시험 모드: 질문 전에 멈춤"), { quiet: true }); }

  // 3. 말로 묻기 (실제 Claude 호출)
  await caption("이제 Agent 에게 <b>말로 물어봅니다</b>");
  await click("document.getElementById('ask-input')");
  for (let i = 1; i <= QUESTION.length; i++) {
    await js(`document.getElementById('ask-input').value = ${JSON.stringify(QUESTION.slice(0, i))}`);
    await sleep(70);
  }
  await sleep(600);
  await click("document.getElementById('ask-send')");
  await sleep(1500);
  await caption("Agent 가 <b>계획</b>을 세우고, 필요한 <b>계산 도구</b>를 스스로 골라 부릅니다");
  await until("document.querySelectorAll('#steps li').length >= 3", 90);
  speed = 2.5;   // 기다리는 구간은 빠르게
  await caption("동네 요약 → 월별 추이 → 기대 대비 실제 소비… <b>원인을 좁혀 갑니다</b> (빠르게 재생)");
  await until("!document.getElementById('answer-report').hidden || [...document.querySelectorAll('#steps li')].some(li => /수치 검증/.test(li.textContent))", 150);
  speed = 1;   // 검증 장면은 제 속도로 보여 준다
  await caption("답의 모든 수치를 <b>계산 결과와 대조</b>합니다. 근거 없는 수치는 되돌려 고쳐 쓰게 합니다");
  await point("document.getElementById('steps')");
  await sleep(4500);
  await until("!document.getElementById('answer-report').hidden", 120);
  await caption("답이 나오면 <b>섬과 목록이</b> 답의 동네·지표로 바뀝니다");
  await sleep(6000);
  await js("document.querySelector('#answer').scrollTo?.({ top: 260, behavior: 'smooth' })");
  await caption("우도는 <b>8월 한 달만</b> 방문객이 소비보다 크게 늘었다고, 근거 수치와 한계까지 답합니다");
  await sleep(7000);
  await point("document.getElementById('answer-report')");
  await caption("답마다 근거·계산식·출처가 붙은 <b>반출용 PDF 보고서</b>를 받을 수 있습니다");
  await sleep(5000);

  // 4. 동네 진단 카드
  await click("document.getElementById('answer-close')");
  await sleep(1500);
  await caption("지표 메뉴에서 <b>낙수 지수</b>를 고르면, 사람에 비해 돈이 덜 떨어지는 동네가 보입니다");
  await click(find("#tabs button", "사람 대비 소비") + " || " + find("#tabs button", "낙수 지수"));
  await sleep(1200);
  await click(find("#tabs button", "낙수 지수"));
  await sleep(4500);
  await caption("동네를 누르면 <b>동네 진단 카드</b>가 열립니다. 지표별 값과 제주 안 순위가 한 장에");
  await click(`[...document.querySelectorAll('#rows .row')].find(r => r.querySelector('b')?.textContent === '연동')`);
  await until("!document.getElementById('dong-card').hidden", 30);
  await sleep(6500);

  // 5. 두 동네 비교
  await caption("<b>비교에 추가</b>를 누르고 다른 동네를 고르면, 두 동네를 나란히 봅니다");
  await click("document.querySelector('.dong-card__pin')");
  await sleep(800);
  await click(`[...document.querySelectorAll('#rows .row')].find(r => r.querySelector('b')?.textContent === '노형동')`);
  await sleep(4500);
  await caption("문제 쪽에 든 값은 <b>빨갛게</b> 짚고, 단추 하나로 Agent 에게 이어서 물을 수 있습니다");
  await sleep(5500);

  // 6. 알고리즘
  await caption("<b>회귀·이중차분·군집</b> 알고리즘으로 섬 전체의 계절·추세를 걷어 낸 값도 봅니다");
  await click(find("#tabs button", "알고리즘 분석"));
  await sleep(1200);
  await click(find("#tabs button", "기대 대비 실제"));
  await sleep(6500);

  // 7. 마무리
  await caption("");
  await js(`window.__demo.card(${JSON.stringify(`${LOGO}<h1>PL<em>ai</em>Jeju</h1><h2>계산은 코드가, 판단은 Claude 가</h2><p>센터 PC 에서 run.bat 하나로 실행 · 키가 없어도 기본 모드로 끝까지</p><p>github.com/Sangwoo1020/PLaiJeju</p>`)})`);
  await sleep(6000);

  await stopRecording();
} catch (error) {
  if (!error.quiet) throw error;
  const span = (frames.at(-1).t - frames[0].t) / 1000;
  console.log(`시험 모드: ${frames.length}장 / ${span.toFixed(1)}초 = ${(frames.length / span).toFixed(1)} fps`);
  socket.close(); chrome.kill(); rmSync(work, { recursive: true, force: true }); process.exit(0);
} finally {
  socket.close();
  chrome.kill();
}

// ---------- 합치기: 찍은 간격(÷ 재생 속도)대로 이어 붙여 30fps mp4 로 ----------
let lines = "";
const outTimes = [];
let clock = 0;
for (let i = 0; i < frames.length; i++) {
  const next = frames[i + 1]?.t ?? frames[i].t + 33;
  const duration = Math.max(0.001, (next - frames[i].t) / 1000 / frames[i].speed);
  outTimes.push(clock);
  clock += duration;
  lines += `file '${frames[i].file}'\nduration ${duration.toFixed(4)}\n`;
}
lines += `file '${frames.at(-1).file}'\n`;
const list = join(work, "list.txt");
writeFileSync(list, lines);
mkdirSync(resolve(OUT, ".."), { recursive: true });
const ff = spawnSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list, "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", OUT], { stdio: "inherit" });
if (ff.status !== 0) throw new Error("ffmpeg 실패");

// 실제 시각 → 영상 시각
const toVideo = (t) => { let i = frames.findIndex((f) => f.t >= t); if (i < 0) i = frames.length - 1; return outTimes[Math.max(0, i)]; };
const stamp = (s) => { const ms = Math.round(s * 1000); const p = (n, w = 2) => String(n).padStart(w, "0"); return `${p(Math.floor(ms / 3600000))}:${p(Math.floor(ms / 60000) % 60)}:${p(Math.floor(ms / 1000) % 60)},${p(ms % 1000, 3)}`; };
let srt = "";
captions.forEach((c, i) => {
  if (!c.text) return;
  const start = toVideo(c.t);
  const end = i + 1 < captions.length ? toVideo(captions[i + 1].t) : clock;
  srt += `${i + 1}\n${stamp(start)} --> ${stamp(Math.max(start + 0.5, end - 0.05))}\n${c.text.replace(/<[^>]+>/g, "")}\n\n`;
});
const srtPath = OUT.replace(/\.mp4$/, ".srt");
writeFileSync(srtPath, srt);
rmSync(work, { recursive: true, force: true });
console.log(`프레임 ${frames.length}장, 영상 ${clock.toFixed(1)}초 -> ${OUT}`);
console.log(`자막 -> ${srtPath}`);
