"use strict";

// ───────── 상태 ─────────
const S = {
  data: null, templates: null, narrator: "en-US-JennyNeural",
  mode: "practice", queue: [], idx: 0,
  results: {},          // item_id -> {analysis, grade, error, blobUrl, promise}
  run: null,            // {aborted}
};
const ABORT = Symbol("abort");
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const DIRECTIONS = {
  1: "In this part of the test, you will read aloud the text on the screen. You will have 45 seconds to prepare. Then you will have 45 seconds to read the text aloud.",
  2: "In this part of the test, you will describe the picture on your screen in as much detail as you can. You will have 45 seconds to prepare your response. Then you will have 30 seconds to speak about the picture.",
  3: "In this part of the test, you will answer three questions. You will have three seconds to prepare after you hear each question. You will have 15 seconds to respond to Questions 5 and 6, and 30 seconds to respond to Question 7.",
  4: "In this part of the test, you will answer three questions based on the information provided. You will have 45 seconds to read the information before the questions begin. You will have three seconds to prepare and 15 seconds to respond to Questions 8 and 9. You will hear Question 10 two times, and you will have 30 seconds to respond.",
  5: "In this part of the test, you will give your opinion about a specific topic. Be sure to say as much as you can in the time allowed. You will have 45 seconds to prepare. Then you will have 60 seconds to speak.",
};

// ───────── API ─────────
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}
const ttsCache = new Map();
function ttsUrl(text, voice) {
  const k = voice + "|" + text;
  if (!ttsCache.has(k)) {
    const p = api("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, voice }) })
      .then((j) => j.url);
    p.catch(() => ttsCache.delete(k));
    ttsCache.set(k, p);
  }
  return ttsCache.get(k);
}

// ───────── 오디오 ─────────
let mic = null, actx = null, analyser = null, curAudio = null;
const MIC_KEY = "micDeviceId";
function savedMic() { try { return localStorage.getItem(MIC_KEY) || ""; } catch { return ""; } }
function saveMic(id) { try { id ? localStorage.setItem(MIC_KEY, id) : localStorage.removeItem(MIC_KEY); } catch {} }

async function ensureMic(force = false) {
  if (mic && !force) return;
  if (mic) mic.getTracks().forEach((t) => t.stop());
  if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error("insecure"), { name: "InsecureContext" });
  const base = { echoCancellation: true, noiseSuppression: true };
  const id = savedMic();
  try {
    mic = await navigator.mediaDevices.getUserMedia({ audio: id ? { ...base, deviceId: { exact: id } } : base });
  } catch (e) {
    if (!id || (e.name !== "OverconstrainedError" && e.name !== "NotFoundError")) throw e;
    saveMic("");  // 저장해 둔 마이크가 빠졌으면 기본 마이크로
    mic = await navigator.mediaDevices.getUserMedia({ audio: base });
  }
  if (!actx) actx = new AudioContext();
  if (actx.state === "suspended") await actx.resume();
  analyser = actx.createAnalyser();
  analyser.fftSize = 1024;
  actx.createMediaStreamSource(mic).connect(analyser);
}

function micErrorText(e) {
  switch (e?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "브라우저나 Windows가 마이크를 막고 있습니다.\n① 주소창 왼쪽 아이콘(ⓘ 또는 조절 아이콘) 클릭 → 마이크 '허용' → 새로고침\n② Windows 설정 → 개인 정보 및 보안 → 마이크 → '마이크 액세스'와 '데스크톱 앱이 마이크에 액세스하도록 허용' 켜기";
    case "NotFoundError":
      return "연결된 마이크를 찾지 못했습니다. 마이크(헤드셋)가 꽂혀 있는지 확인하세요.";
    case "NotReadableError":
      return "다른 프로그램이 마이크를 독점하고 있습니다. 줌·디스코드·녹음 프로그램 등을 끄고 다시 시도하세요.";
    case "InsecureContext":
      return "주소가 http://localhost:8765 가 아니면 마이크를 쓸 수 없습니다. 바탕화면 아이콘으로 열어주세요.";
    default:
      return `마이크를 열지 못했습니다 (${e?.name || ""} ${e?.message || ""}).`;
  }
}

async function fillDevices() {
  const sel = $("#mt-device");
  const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  const cur = mic?.getAudioTracks()[0]?.getSettings().deviceId || savedMic();
  sel.innerHTML = devs.map((d, i) =>
    `<option value="${esc(d.deviceId)}" ${d.deviceId === cur ? "selected" : ""}>${esc(d.label || "마이크 " + (i + 1))}</option>`).join("")
    || `<option value="">마이크 없음</option>`;
}

async function micTest() {
  const st = $("#mt-status");
  st.className = "muted"; st.textContent = "마이크 여는 중… (브라우저가 권한을 물으면 '허용')";
  try {
    await ensureMic(true);
    await fillDevices();
    const name = mic.getAudioTracks()[0]?.label || "마이크";
    st.textContent = `「${name}」 사용 중 — 말해보세요. 막대가 안 움직이면 위 목록에서 다른 마이크를 고르세요.`;
    let peak = 0;
    const t0 = performance.now();
    const iv = setInterval(() => {
      const lv = level();
      peak = Math.max(peak, lv);
      $("#mt-bar").style.width = Math.min(lv * 600, 100) + "%";
      if (peak > 0.02 && st.className !== "ok") { st.className = "ok"; st.textContent = `✅ 「${name}」 정상 작동 중입니다.`; }
      if (performance.now() - t0 > 15000) {
        clearInterval(iv); $("#mt-bar").style.width = "0";
        if (peak <= 0.02) { st.className = "bad"; st.textContent = `「${name}」에서 소리가 들어오지 않았습니다. 목록에서 다른 마이크를 고르거나, 마이크 음소거 버튼을 확인하세요.`; }
      }
    }, 100);
  } catch (e) {
    st.className = "bad"; st.textContent = micErrorText(e);
  }
}
$("#mt-start").onclick = micTest;
$("#mt-device").onchange = (e) => { saveMic(e.target.value); micTest(); };
function level() {
  if (!analyser) return 0;
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let s = 0; for (const v of buf) s += v * v;
  return Math.sqrt(s / buf.length);
}
async function beep() {
  if (!actx) actx = new AudioContext();
  const o = actx.createOscillator(), g = actx.createGain();
  o.frequency.value = 880; g.gain.value = 0.15;
  o.connect(g).connect(actx.destination);
  o.start(); o.stop(actx.currentTime + 0.35);
  await sleep(450);
}
async function playUrl(url, rate = 1) {
  return new Promise((resolve) => {
    const a = new Audio(url);
    a.playbackRate = rate;
    curAudio = a;
    a.onended = a.onerror = () => { curAudio = null; resolve(); };
    a.play().catch(() => resolve());
  });
}
async function speak(text, voice, rate = 1) {
  if (!text) return;
  let url;
  try { url = await ttsUrl(text, voice); } catch (e) {
    // 원어민 음성 서버가 막혀도 질문은 반드시 들려준다 → 브라우저 내장 음성으로 대체
    console.warn("TTS 실패, 브라우저 음성으로 대체", e);
    if (S.run?.aborted) return;
    return speakBrowser(text, voice, rate);
  }
  if (S.run?.aborted) return;
  await playUrl(url, rate);
}
function speakBrowser(text, voice, rate = 1) {
  const synth = window.speechSynthesis;
  if (!synth) return;
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    const lang = (voice || "en-US").slice(0, 5);
    const vs = synth.getVoices().filter((v) => v.lang.replace("_", "-").startsWith("en"));
    u.voice = vs.find((v) => v.lang.replace("_", "-") === lang) || vs.find((v) => v.lang.startsWith("en-US")) || vs[0] || null;
    u.lang = u.voice?.lang || lang;
    u.rate = rate;
    u.onend = u.onerror = () => resolve();
    synth.cancel();
    synth.speak(u);
  });
}
function stopAudio() {
  if (curAudio) { curAudio.pause(); curAudio = null; }
  window.speechSynthesis?.cancel();
}

// ───────── 진행 UI ─────────
function setPhase(label, cls = "") { const p = $("#phase"); p.textContent = label; p.className = "phase " + cls; }
function setTimer(left, total) {
  $("#timer").textContent = Math.ceil(left);
  $("#ring-fg").style.strokeDashoffset = String(326.7 * (1 - (total ? left / total : 0)));
}
function chk() { if (S.run?.aborted) throw ABORT; }

// 카운트다운. skippable 이면 '건너뛰기' 버튼으로 끝낼 수 있다.
function countdown(sec, { skippable = false, stopBtn = false, onTick = null } = {}) {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const skip = $("#btn-skip"), stop = $("#btn-stop");
    skip.classList.toggle("hidden", !skippable);
    stop.classList.toggle("hidden", !stopBtn);
    let done = false;
    const finish = () => {
      if (done) return; done = true;
      clearInterval(iv); skip.classList.add("hidden"); stop.classList.add("hidden");
      skip.onclick = stop.onclick = null; resolve();
    };
    skip.onclick = stop.onclick = finish;
    const iv = setInterval(() => {
      const el = (performance.now() - t0) / 1000;
      setTimer(Math.max(sec - el, 0), sec);
      onTick && onTick(el);
      if (el >= sec || S.run?.aborted) finish();
    }, 100);
    setTimer(sec, sec);
  });
}

async function record(sec) {
  const chunks = [];
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
    .find((t) => MediaRecorder.isTypeSupported(t));
  const rec = new MediaRecorder(mic, type ? { mimeType: type } : undefined);
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const stopped = new Promise((r) => (rec.onstop = r));
  rec.start(250);
  let silentSince = performance.now(), spoke = false;
  setPhase("답변 중", "rec");
  await countdown(sec, {
    stopBtn: S.mode === "practice",
    onTick: () => {
      const lv = level();
      $("#meter-bar").style.width = Math.min(lv * 600, 100) + "%";
      const now = performance.now();
      if (lv > 0.02) { silentSince = now; spoke = true; }
      const sil = (now - silentSince) / 1000;
      $("#silence").textContent = sil >= 2 ? (spoke ? `⏸ ${sil.toFixed(1)}초째 침묵` : `⏳ ${sil.toFixed(1)}초째 시작 안 함`) : "";
    },
  });
  rec.stop();
  await stopped;
  $("#meter-bar").style.width = "0"; $("#silence").textContent = "";
  return new Blob(chunks, { type: "audio/webm" });
}

// ───────── 문항 화면 ─────────
function partMeta(p) { return S.data.parts[p].meta; }

function infoTable(info) {
  return `<div class="info-title">${esc(info.title)}</div><div class="info-sub">${esc(info.subtitle)}</div>
  <table class="info"><tr>${info.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>
  ${info.rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</table>`;
}

function stage(html) { $("#stage").innerHTML = html; }

async function runItem(item, firstInSet) {
  const meta = partMeta(item.part);
  $("#run-q").textContent = "Q" + item.qnum;
  $("#run-part").textContent = `Part ${item.part} · ${meta.ko}`;
  setTimer(0, 0); setPhase("대기");

  // 메모장: Part 2·4·5 (Part 3 은 준비 3초라 쓸 시간이 없다). Part 4 는 세 문항이 같은 자료라
  // 세트 안에서 유지하고, Part 2 는 문항마다 사진이 달라 문항 단위로 비운다. (다시 풀기는 같은 키라 유지)
  $("#memo-box").classList.toggle("hidden", ![2, 4, 5].includes(item.part));
  const memoKey = item.part === 2 ? item.id : item.set_id;
  if (S.memoSet !== memoKey) { $("#memo").value = ""; S.memoSet = memoKey; }

  // 파트 안내 (세트 첫 문항)
  if (firstInSet) {
    stage(`<div class="directions">${esc(DIRECTIONS[item.part])}</div>`);
    setPhase("안내", "listen");
    if (S.mode === "exam") { await speak(DIRECTIONS[item.part], S.narrator); chk(); }
  }

  const dir = `<div class="directions">${esc(DIRECTIONS[item.part])}</div>`;
  if (item.part === 1) {
    stage(dir + `<div class="script">${esc(item.script)}</div>`);
    setPhase("준비", "prep"); await beep(); await countdown(item.prep, { skippable: true }); chk();
  } else if (item.part === 2) {
    stage(`<img class="photo" src="/images/${encodeURIComponent(item.image)}">`);
    setPhase("준비", "prep"); await beep(); await countdown(item.prep, { skippable: true }); chk();
  } else if (item.part === 3) {
    const intro = `<div class="directions">${esc(item.intro)}</div>`;
    stage(intro);
    if (firstInSet) { setPhase("듣기", "listen"); await speak(item.intro, S.narrator); chk(); }
    stage(intro + `<div class="question">${esc(item.q)}</div>`);
    setPhase("듣기", "listen"); await speak(item.q, item.voice); chk();
    setPhase("준비", "prep"); await beep(); await countdown(item.prep); chk();
  } else if (item.part === 4) {
    const table = infoTable(item.info);
    stage(table);
    if (firstInSet) {
      setPhase("자료 읽기", "prep"); await countdown(45, { skippable: S.mode === "practice" }); chk();
      setPhase("듣기", "listen"); await speak(item.intro, item.voice); chk();
    }
    stage(table + `<div class="listen">🎧 질문을 들으세요 (화면에 표시되지 않습니다)</div>`);
    setPhase("듣기", "listen"); await speak(item.q, item.voice); chk();
    if (item.play_twice) { await sleep(900); chk(); await speak(item.q, item.voice); chk(); }
    setPhase("준비", "prep"); await beep(); await countdown(item.prep); chk();
  } else if (item.part === 5) {
    stage(dir + `<div class="question">${esc(item.q)}</div>`);
    setPhase("듣기", "listen"); await speak(item.q, S.narrator); chk();
    setPhase("준비", "prep"); await beep(); await countdown(item.prep, { skippable: true }); chk();
  }
  await beep(); chk();
  const blob = await record(item.resp); chk();
  setPhase("완료"); setTimer(0, 0);
  return blob;
}

// 녹음 → 분석 → 채점 파이프라인
function startPipeline(item, blob) {
  const r = { blobUrl: URL.createObjectURL(blob), analysis: null, grade: null, error: null };
  S.results[item.id] = r;
  r.promise = (async () => {
    try {
      const fd = new FormData();
      fd.append("audio", blob, "answer.webm");
      fd.append("item_id", item.id);
      r.analysis = await api("/api/analyze", { method: "POST", body: fd });
      r.onAnalysis && r.onAnalysis();
      r.grade = await api("/api/grade", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rid: r.analysis.rid, mode: S.mode }) });
      // 모범답안 음성은 미리 만들어 둔다
      if (r.grade.model_answer) ttsUrl(r.grade.model_answer, item.voice);
    } catch (e) { r.error = e.message; }
    r.onUpdate && r.onUpdate();
  })();
  return r;
}

async function regrade(itemId) {
  const r = S.results[itemId];
  if (!r?.analysis) return;
  r.error = null; r.onUpdate && r.onUpdate();
  try {
    r.grade = await api("/api/grade", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rid: r.analysis.rid, mode: S.mode }) });
  } catch (e) { r.error = e.message; }
  r.onUpdate && r.onUpdate();
}

// ───────── 피드백 렌더링 ─────────
function transcriptHtml(m) {
  if (!m.marked) return '<span class="muted">(무응답)</span>';
  const lc = new Set((m.low_confidence || []).map((w) => w.toLowerCase()));
  return m.marked.split(" ").map((t) => {
    if (/^\[\d+(\.\d+)?s\]$/.test(t)) return `<span class="p">${esc(t)}</span>`;
    if (/^\(.*\)$/.test(t)) return `<span class="f">${esc(t.slice(1, -1))}</span>`;
    const bare = t.replace(/[,.?!]/g, "").toLowerCase();
    return lc.has(bare) ? `<span class="lc" title="발음 불명확 의심">${esc(t)}</span>` : esc(t);
  }).join(" ");
}

function metricsHtml(m, item) {
  if (m.no_response) return "";
  const box = (label, val, bad) => `<div class="${bad ? "bad" : ""}">${label}<b>${val}</b></div>`;
  const longP = m.pauses.filter((p) => p.dur >= 2).length;
  return `<div class="metrics">
    ${box("답변 시작 지연", m.latency + "초", m.latency >= 3)}
    ${box("2초+ 멈춤", longP + "회", longP >= 2)}
    ${box("필러 (um/uh)", m.filler_count + "회", m.filler_ratio > 0.08)}
    ${box("말하기 속도", m.wpm + " wpm", item.part === 1 ? m.wpm < 100 : m.wpm < 70)}
    ${item.part === 1 && m.script_diff ? box("지문 일치율", m.script_diff.accuracy + "%", m.script_diff.accuracy < 90)
      : box("시간 활용", Math.round(m.coverage * 100) + "%", m.coverage < 0.65)}
  </div>`;
}

const LEVEL_INFO = {
  AL: { desc: "Level 8 · 180~200점", tier: "a" }, IH: { desc: "Level 7 · 160~170점", tier: "a" },
  IM3: { desc: "Level 6 · 140~150점", tier: "b" }, IM2: { desc: "Level 6 · 130점", tier: "b" },
  IM1: { desc: "Level 6 · 110~120점", tier: "b" }, IL: { desc: "Level 5 · 90~100점", tier: "c" },
  NH: { desc: "Level 4 · 60~80점", tier: "c" }, NM: { desc: "Level 3 · 30~50점", tier: "d" },
  NL: { desc: "Level 1~2 · 0~20점", tier: "d" },
};
const SUBS = { pronunciation: "발음", fluency: "유창성", grammar: "문법", vocabulary: "어휘", content: "내용" };

function feedbackHtml(item, r, withActions) {
  const a = r.analysis, g = r.grade;
  let h = `<div class="fb">`;
  if (g) {
    const lv = LEVEL_INFO[g.level];
    h += `<div class="fb-head">${lv ? `<div class="lvl-badge lv-${lv.tier}" title="이 답변 수준을 시험 전체에서 유지할 때의 예상 등급">
        <b>${g.level}</b><span>${lv.desc}</span></div>` : ""}
      <div class="score">${g.score}<small> / ${g.max}</small></div>
      <div><div>${esc(g.summary)}</div>${g.capped ? `<div class="cap-note">내용 기준 ${g.raw_score}점 → 더듬기·지연 때문에 ${g.score}점으로 제한</div>` : ""}</div></div>`;
    if (g.subscores && Object.keys(g.subscores).length)
      h += `<div class="sub">${Object.entries(SUBS).map(([k, l]) => `<div>${l}<b>${g.subscores[k] ?? "-"} / 5</b></div>`).join("")}</div>`;
  } else if (r.error) {
    h += `<div class="err">채점 실패: ${esc(r.error)}</div>`;
    if (r.analysis) h += `<div><button data-regrade="${item.id}">↻ 다시 채점</button></div>`;
  } else {
    h += `<div class="loading"><div class="spinner"></div>${a ? "AI가 채점 중입니다 (10~30초)…" : "음성을 분석하는 중…"}</div>`;
  }
  if (a) {
    const m = a.metrics;
    h += metricsHtml(m, item);
    if (m.issues.length) h += `<ul class="issues">${m.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
    h += `<div><h3>내 답변 <button class="play" data-play-blob="${item.id}" title="내 녹음 듣기">▶</button></h3>
      <div class="transcript">${transcriptHtml(m)}</div>
      <div class="muted" style="font-size:12px">노란색 = 필러, 빨간색 = 멈춤 길이, 물결 밑줄 = 발음 불명확 의심</div></div>`;
    if (m.script_diff && (m.script_diff.missing.length || m.script_diff.substituted.length)) {
      h += `<div><h3>지문과 다르게 읽은 부분</h3>
        ${m.script_diff.substituted.map((s) => `<div class="corr"><span class="o">${esc(s.heard)}</span> → <span class="x">${esc(s.expected)}</span></div>`).join("")}
        ${m.script_diff.missing.length ? `<div class="muted">빠뜨린 단어: ${esc(m.script_diff.missing.join(", "))}</div>` : ""}</div>`;
    }
  }
  if (g) {
    h += `<div class="two"><div><h3>유창성 피드백</h3><div>${esc(g.fluency_feedback)}</div></div>
      <div><h3>다음엔 이것만</h3><div>${esc(g.next_tip)}</div></div></div>`;
    if (g.corrections?.length)
      h += `<div><h3>교정</h3>${g.corrections.map((c) => `<div class="corr"><div class="o">${esc(c.original)}</div>
        <div class="x">${esc(c.fixed)}</div><div class="r">${esc(c.reason)}</div></div>`).join("")}</div>`;
    const ans = g.reading_guide || g.model_answer;
    h += `<div class="model"><h3>${item.part === 1 ? "읽기 가이드 (/ 끊기, 대문자 강세, ↗↘ 억양)" : "만능문장 모범답안"}</h3>
      <div class="en">${esc(ans)}</div>${g.model_answer_ko ? `<div class="ko">${esc(g.model_answer_ko)}</div>` : ""}
      <div class="tools"><button class="primary" data-say="${item.id}" data-rate="1">🔊 원어민 낭독</button>
      <button data-say="${item.id}" data-rate="0.8">🐢 0.8배속</button></div></div>`;
    if (g.templates_used?.length)
      h += `<div class="tpl-used"><h3>사용한 만능문장</h3>${g.templates_used.map((t) => `<span><b>${esc(t.id)}</b> ${esc(t.how)}</span>`).join("")}</div>`;
  }
  if (withActions) {
    const last = S.idx >= S.queue.length - 1;
    h += `<div class="fb-actions"><button id="fb-retry">↺ 다시 풀기</button>
      <button id="fb-next" class="primary">${last ? "연습 끝내기" : "다음 문제 →"}</button></div>`;
  }
  return h + "</div>";
}

document.addEventListener("click", (e) => {
  const say = e.target.closest("[data-say]");
  if (say) {
    const item = findItem(say.dataset.say), g = S.results[item.id]?.grade;
    if (g) { stopAudio(); speak(g.model_answer, item.voice, Number(say.dataset.rate)); }
  }
  const pb = e.target.closest("[data-play-blob]");
  if (pb) { stopAudio(); playUrl(S.results[pb.dataset.playBlob].blobUrl); }
  const rg = e.target.closest("[data-regrade]");
  if (rg) regrade(rg.dataset.regrade);
  const tts = e.target.closest("[data-tts]");
  if (tts) { stopAudio(); speak(tts.dataset.tts, "en-US-JennyNeural"); }
  const go = e.target.closest("[data-go]");
  if (go) { if (S.run && !S.run.aborted && !confirm("진행 중인 시험을 그만둘까요?")) return; abortRun(); show(go.dataset.go); }
});

function findItem(id) {
  for (const p of Object.values(S.data.parts)) for (const s of p.sets) for (const it of s.items) if (it.id === id) return it;
}

// ───────── 모드 실행 ─────────
function abortRun() { if (S.run) S.run.aborted = true; stopAudio(); }

function prefetch(items) {
  for (const it of items) {
    if (it.q) ttsUrl(it.q, it.part === 5 ? S.narrator : it.voice);
    if (it.intro) ttsUrl(it.intro, it.part === 3 ? S.narrator : it.voice);
  }
}

async function startRun(mode, items) {
  try { await ensureMic(); } catch (e) { alert(micErrorText(e)); return; }
  if (actx.state === "suspended") await actx.resume();
  abortRun();
  S.mode = mode; S.queue = items; S.idx = 0; S.results = {}; S.run = { aborted: false }; S.memoSet = null;
  prefetch(items);
  $("#run-mode").textContent = mode === "exam" ? "실전 모드" : "연습 모드";
  show("run");
  const run = S.run;
  try {
    while (S.idx < S.queue.length) {
      const item = S.queue[S.idx];
      const prev = S.queue[S.idx - 1];
      $("#progress-bar").style.width = (S.idx / S.queue.length) * 100 + "%";
      const blob = await runItem(item, !prev || prev.set_id !== item.set_id || item._retry);
      delete item._retry;
      const r = startPipeline(item, blob);
      if (mode === "practice") {
        const action = await practiceFeedback(item, r);
        if (action === "retry") { item._retry = item.index === 0; continue; }
        if (action === "home") break;
      }
      S.idx++;
    }
    if (run.aborted) return;
    if (mode === "exam") { $("#progress-bar").style.width = "100%"; showReport(); }
    else show("home");
  } catch (e) {
    if (e !== ABORT) { console.error(e); alert("오류: " + e.message); }
  } finally {
    if (S.run === run) S.run = null;
  }
}

function practiceFeedback(item, r) {
  return new Promise((resolve) => {
    const render = () => {
      stage(feedbackHtml(item, r, true));
      $("#fb-retry").onclick = () => resolve("retry");
      $("#fb-next").onclick = () => resolve(S.idx >= S.queue.length - 1 ? "home" : "next");
    };
    setPhase("피드백");
    r.onAnalysis = render; r.onUpdate = render; render();
  });
}

function pickRandom(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

function startPractice(part, setId) {
  const sets = S.data.parts[part].sets;
  const set = setId ? sets.find((s) => s.id === setId) : pickRandom(sets);
  startRun("practice", set.items.map((i) => ({ ...i })));
}

function fullTestItems() {
  const items = [];
  for (const p of [1, 2, 3, 4, 5]) {
    const sets = S.data.parts[p].sets;
    if (sets.length) items.push(...pickRandom(sets).items.map((i) => ({ ...i })));
  }
  return items;
}
function startExam() { startRun("exam", fullTestItems()); }

// ───────── 실전 리포트 ─────────
const LEVELS = [[180, "AL", "Level 8"], [160, "IH", "Level 7"], [140, "IM3", "Level 6"], [130, "IM2", "Level 6"],
  [110, "IM1", "Level 6"], [90, "IL", "Level 5"], [60, "NH", "Level 4"], [30, "NM", "Level 3"], [0, "NL", "Level 1-2"]];

function showReport() {
  show("report");
  const render = () => {
    const items = S.queue;
    const done = items.filter((i) => S.results[i.id]?.grade || S.results[i.id]?.error).length;
    let raw = 0, max = 0;
    for (const i of items) { const g = S.results[i.id]?.grade; if (g) { raw += g.score; max += g.max; } }
    const all = done === items.length;
    const pts = max ? Math.round((raw / max) * 200 / 10) * 10 : 0;
    const lv = LEVELS.find(([m]) => pts >= m);
    let h = `<div class="report-top"><div class="card level">
      ${all ? `<div class="muted">예상 점수 (추정)</div><div class="big">${lv[1]}</div><div class="pts">${pts}점 · ${lv[2]}</div>
        <div class="muted" style="font-size:13px;margin-top:8px">원점수 ${raw} / ${max}${max < 35 ? " (Part 2 제외 환산)" : ""}</div>`
      : `<div class="loading" style="justify-content:center"><div class="spinner"></div>채점 중 ${done} / ${items.length}</div>`}
      </div><div class="card"><table class="rows"><tr><th>문항</th><th>파트</th><th>등급</th><th>점수</th><th>시작 지연</th><th>멈춤 2초+</th><th>필러</th><th>총평</th></tr>
      ${items.map((i) => {
        const r = S.results[i.id] || {}, g = r.grade, m = r.analysis?.metrics;
        return `<tr class="click" data-detail="${i.id}"><td>Q${i.qnum}</td><td>${i.part}</td><td><b>${g?.level || "-"}</b></td>
          <td>${g ? `<b>${g.score}</b> / ${g.max}` : r.error ? "실패" : "…"}</td>
          <td>${m && !m.no_response ? m.latency + "초" : "-"}</td><td>${m ? m.pauses.filter((p) => p.dur >= 2).length : "-"}</td>
          <td>${m ? m.filler_count : "-"}</td><td class="muted">${esc(g?.summary || r.error || "")}</td></tr>`;
      }).join("")}</table></div></div>
      <div id="details">${items.map((i) => `<div class="card detail" id="d-${i.id}"><h2>Q${i.qnum} · Part ${i.part}</h2>
        ${i.q ? `<p class="muted">${esc(i.q)}</p>` : ""}${feedbackHtml(i, S.results[i.id] || {}, false)}</div>`).join("")}</div>
      <div style="text-align:center"><button class="primary big" data-go="home">홈으로</button></div>`;
    $("#report").innerHTML = h;
    $("#report").querySelectorAll("[data-detail]").forEach((tr) =>
      (tr.onclick = () => document.getElementById("d-" + tr.dataset.detail).scrollIntoView({ behavior: "smooth" })));
  };
  for (const i of S.queue) { const r = S.results[i.id]; if (r) r.onUpdate = r.onAnalysis = render; }
  render();
}

// ───────── 홈·라이브러리·기록 ─────────
function show(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-" + name).classList.remove("hidden");
  document.querySelectorAll(".nav").forEach((n) => n.classList.toggle("active", n.dataset.go === name));
  if (name === "history") renderHistory();
}

function renderHome() {
  const box = $("#part-buttons");
  box.innerHTML = [1, 2, 3, 4, 5].map((p) => {
    const { meta, sets } = S.data.parts[p];
    const qs = meta.timing.length > 1 ? `Q${meta.first}–${meta.first + meta.timing.length - 1}` : `Q${meta.first}`;
    return `<div class="part-row"><button class="part" data-part="${p}" ${sets.length ? "" : "disabled"}>
      <span><b>Part ${p}</b> · ${meta.ko} <span class="muted">${qs}</span></span>
      <span class="go">${sets.length ? "연습 ▶" : "사진 없음"}</span></button>
      <select data-sel="${p}" ${sets.length ? "" : "disabled"}><option value="">랜덤</option>
      ${sets.map((s, i) => `<option value="${s.id}">세트 ${i + 1}</option>`).join("")}</select></div>`;
  }).join("");
  box.querySelectorAll("[data-part]").forEach((b) =>
    (b.onclick = () => startPractice(Number(b.dataset.part), box.querySelector(`[data-sel="${b.dataset.part}"]`).value)));
  const n = [1, 2, 3, 4, 5].reduce((a, p) => a + (S.data.parts[p].sets.length ? S.data.parts[p].meta.timing.length : 0), 0);
  $("#exam-summary").textContent = `${n}문항 · 약 ${n >= 11 ? 20 : 17}분${S.data.parts[2].sets.length ? "" : " (Part 2는 사진이 없어 제외)"}`;
}

function renderLibrary() {
  const t = S.templates;
  $("#library").innerHTML = `<div class="card"><h2 style="margin-top:0">만능문장 라이브러리</h2>
    <p class="muted">채점 AI가 피드백과 모범답안을 만들 때 이 문장들을 우선 사용합니다. <code>data/templates.json</code>에서 고칠 수 있습니다.</p>
    ${["1", "2", "3", "4", "5"].map((p) => `<div class="lib-part"><h3>${esc(t[p].title)}</h3>
      ${t[p].items.map((i) => `<div class="lib-item"><span class="muted">${esc(i.id)}</span>
        <div><div class="en">${esc(i.en)}</div><div class="ko">${esc(i.ko)}</div></div>
        ${p !== "1" ? `<button class="play" data-tts="${esc(i.en.replace(/_+/g, " "))}">▶</button>` : "<span></span>"}</div>`).join("")}</div>`).join("")}</div>`;
}

async function renderHistory() {
  const box = $("#history");
  box.innerHTML = `<div class="card"><div class="loading"><div class="spinner"></div>불러오는 중</div></div>`;
  const rows = await api("/api/history");
  if (!rows.length) { box.innerHTML = `<div class="card muted">아직 기록이 없습니다.</div>`; return; }
  box.innerHTML = `<div class="card"><h2 style="margin-top:0">채점 기록</h2><table class="rows">
    <tr><th>일시</th><th>모드</th><th>문항</th><th>등급</th><th>점수</th><th>시작 지연</th><th>필러</th><th>총평</th></tr>
    ${rows.map((r) => `<tr><td>${esc(r.at.replace("T", " "))}</td><td>${r.mode === "exam" ? "실전" : "연습"}</td>
      <td>Part ${r.part} · ${esc(r.item_id)}</td><td><b>${esc(r.grade.level || "-")}</b></td><td><b>${r.grade.score}</b> / ${r.grade.max}</td>
      <td>${r.metrics.latency ?? "-"}초</td><td>${r.metrics.filler_count}</td><td class="muted">${esc(r.grade.summary)}</td></tr>`).join("")}
    </table></div>`;
}

$("#btn-shutdown").onclick = async () => {
  if (!confirm("앱을 종료할까요? (채점 중인 문항이 있으면 결과가 사라집니다)")) return;
  abortRun();
  try { await fetch("/api/shutdown", { method: "POST" }); } catch {}
  document.body.innerHTML = `<div style="text-align:center;padding:120px 20px;font-size:18px">
    앱이 종료되었습니다. 이 탭을 닫으세요.<br><span style="color:#6b7385;font-size:14px">다시 쓰려면 바탕화면의 '토익스피킹 코치' 아이콘을 실행하세요.</span></div>`;
};
$("#memo-clear").onclick = () => { $("#memo").value = ""; $("#memo").focus(); };
$("#btn-exam").onclick = startExam;
$("#btn-practice-all").onclick = () => startRun("practice", fullTestItems());
$("#btn-quit").onclick = () => { if (confirm("그만두고 홈으로 갈까요?")) { abortRun(); show("home"); } };

(async () => {
  const [q, t] = await Promise.all([api("/api/questions"), api("/api/templates")]);
  S.data = q; S.narrator = q.narrator; S.templates = t;
  renderHome(); renderLibrary(); show("home");
})();
