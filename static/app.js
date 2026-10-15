"use strict";
const $ = (id) => document.getElementById(id);
let config, events = [], voices = [], recognition, listening = false, busy = false;
let toastTimer;
const localISO = (date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}T${String(date.getHours()).padStart(2,"0")}:${String(date.getMinutes()).padStart(2,"0")}`;
const today = () => localISO(new Date()).slice(0, 10);
const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* Private browsing may deny storage. */ } }
};

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Luna-Token": config.token}, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "요청에 실패했습니다.");
  return result;
}
function notify(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
}
function message(text, kind = "assistant") {
  const item = document.createElement("div");
  item.className = `message ${kind}-message`;
  item.textContent = text;
  $("conversation").append(item);
  while ($("conversation").children.length > 40) $("conversation").firstElementChild.remove();
  $("conversation").scrollTop = $("conversation").scrollHeight;
}
function speak(text, force = false) {
  if (!("speechSynthesis" in window) || (!force && !$("speak-enabled").checked)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = voices.find((item) => item.voiceURI === $("voice-select").value);
  if (voice) utterance.voice = voice;
  utterance.lang = voice?.lang || "ko-KR";
  utterance.rate = 1;
  utterance.pitch = 1;
  utterance.onerror = (event) => {
    if (!["interrupted", "canceled"].includes(event.error)) notify("음성 출력에 실패했습니다. 다른 음성을 선택해 주세요.");
  };
  window.speechSynthesis.speak(utterance);
}
function loadVoices() {
  if (!("speechSynthesis" in window)) {
    $("voice-note").textContent = "이 브라우저는 음성 출력을 지원하지 않습니다.";
    $("test-voice").disabled = true;
    return;
  }
  voices = speechSynthesis.getVoices();
  const saved = storage.get("luna-voice");
  const selected = $("voice-select").value;
  $("voice-select").replaceChildren();
  const korean = voices.filter((item) => item.lang.toLowerCase().startsWith("ko"));
  const preferred = korean.find((item) => /sunhi|heami|yuna|female|선히|희미|유나/i.test(item.name)) || korean[0];
  const available = [...korean, ...voices.filter((item) => !korean.includes(item))];
  for (const voice of available) {
    const option = document.createElement("option");
    option.value = voice.voiceURI;
    option.textContent = `${voice.name} · ${voice.lang}${voice.localService ? " · PC 음성" : " · 온라인"}`;
    $("voice-select").append(option);
  }
  if (available.length) {
    $("voice-select").value = available.find((v) => v.voiceURI === (saved || selected))?.voiceURI || preferred?.voiceURI || available[0].voiceURI;
  }
  $("voice-note").textContent = korean.length
    ? "여성 음성 여부는 ‘목소리 들어보기’로 확인하세요. PC·브라우저에 따라 음성이 달라요."
    : "한국어 음성이 없습니다. Windows 설정 → 시간 및 언어 → 음성에서 한국어 음성을 추가해 주세요.";
}
function showDraft(draft) {
  $("event-form").reset();
  $("event-error").textContent = "";
  const start = new Date();
  start.setMinutes(0, 0, 0);
  start.setHours(start.getHours() + 1);
  const end = new Date(start.getTime() + 3600000);
  $("event-title").value = draft?.title || "";
  $("event-start").value = draft?.start || localISO(start);
  $("event-end").value = draft?.end || localISO(end);
  $("event-dialog").showModal();
  $("event-title").focus();
}
async function send(text) {
  text = text.trim();
  if (!text || busy || !config) return;
  busy = true;
  $("send").disabled = true;
  $("voice-status").textContent = "부탁하신 내용을 확인하고 있어요";
  message(text, "user");
  $("command").value = "";
  try {
    const result = await api("/api/command", {text});
    message(result.message);
    speak(result.message);
    if (result.draft) showDraft(result.draft);
  } catch (error) {
    message(error.message, "error");
    notify(error.message);
  } finally {
    busy = false;
    $("send").disabled = false;
    $("voice-status").textContent = "당신의 이야기를 기다리고 있어요";
  }
}
function renderEvents() {
  const date = $("selected-date").value;
  const selected = events.filter((event) => event.start.slice(0, 10) <= date && event.end.slice(0, 10) >= date
    && event.end > `${date}T00:00`);
  $("events").replaceChildren();
  if (!selected.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const icon = document.createElement("span"); icon.textContent = "☷";
    const title = document.createElement("p"); title.textContent = "여유로운 하루가 기다리고 있어요";
    const hint = document.createElement("small"); hint.textContent = "+ 버튼이나 음성으로 일정을 추가하세요.";
    empty.append(icon, title, hint); $("events").append(empty);
  }
  for (const event of selected) {
    const row = document.createElement("article"); row.className = "event";
    const time = document.createElement("span"); time.className = "event-time"; time.textContent = event.start.slice(11);
    const detail = document.createElement("div"); detail.className = "event-detail";
    const title = document.createElement("span"); title.className = "event-title"; title.textContent = event.title;
    const duration = document.createElement("span"); duration.className = "event-duration";
    duration.textContent = `${event.start.replace("T", " ")} — ${event.end.replace("T", " ")}`;
    detail.append(title, duration);
    const remove = document.createElement("button"); remove.className = "delete-event"; remove.textContent = "×";
    remove.setAttribute("aria-label", `${event.title} 일정 삭제`);
    remove.addEventListener("click", async () => {
      if (!confirm(`‘${event.title}’ 일정을 삭제할까요?`)) return;
      remove.disabled = true;
      try { await api("/api/events/delete", {id: event.id}); await refreshEvents(); notify("일정을 삭제했어요."); }
      catch (error) { notify(error.message); remove.disabled = false; }
    });
    row.append(time, detail, remove); $("events").append(row);
  }
}
async function refreshEvents() { events = await api("/api/events"); renderEvents(); }
function setupRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $("mic").disabled = true;
    $("voice-status").textContent = "음성 인식은 Chrome·Edge에서 이용하세요";
    return;
  }
  recognition = new SpeechRecognition();
  recognition.lang = "ko-KR";
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.onstart = () => {
    listening = true; $("mic").setAttribute("aria-pressed", "true");
    $("mic").setAttribute("aria-label", "음성 인식 중지");
    document.querySelector(".voice-stage").classList.add("listening");
    $("voice-status").textContent = "듣고 있어요. 편하게 말씀해 주세요";
  };
  recognition.onresult = (event) => {
    $("command").value = event.results[0][0].transcript;
    $("command").focus();
    notify("인식한 내용을 확인한 뒤 ↑ 버튼을 눌러 주세요.");
  };
  recognition.onerror = (event) => {
    const errors = {"not-allowed": "브라우저에서 마이크 권한을 허용해 주세요.", "audio-capture": "연결된 마이크를 찾을 수 없습니다.", "network": "브라우저 음성 서비스에 연결하지 못했습니다. 텍스트 입력을 이용해 주세요.", "no-speech": "음성이 감지되지 않았어요. 다시 시도해 주세요.", "service-not-allowed": "브라우저 음성 서비스가 허용되지 않았습니다. Chrome 또는 텍스트 입력을 이용해 주세요."};
    if (event.error !== "aborted") notify(errors[event.error] || `음성 인식 오류: ${event.error}`);
  };
  recognition.onend = () => {
    listening = false; $("mic").setAttribute("aria-pressed", "false");
    $("mic").setAttribute("aria-label", "음성 인식 시작");
    document.querySelector(".voice-stage").classList.remove("listening");
    $("voice-status").textContent = "인식한 명령을 확인하고 보내 주세요";
  };
  $("mic").addEventListener("click", () => {
    if (listening) return recognition.stop();
    window.speechSynthesis?.cancel();
    try { recognition.start(); } catch { notify("음성 인식이 준비 중입니다. 잠시 후 다시 시도해 주세요."); }
  });
}
function updateClock() {
  const date = new Date();
  $("clock").textContent = date.toLocaleTimeString("ko-KR", {hour:"2-digit", minute:"2-digit", hour12:false});
  $("today-label").textContent = date.toLocaleDateString("ko-KR", {year:"numeric", month:"long", day:"numeric", weekday:"long"});
}
async function init() {
  updateClock(); setInterval(updateClock, 1000);
  $("selected-date").value = today();
  $("selected-date").addEventListener("change", renderEvents);
  for (const [id, step] of [["previous-day", -1], ["next-day", 1]]) $(id).addEventListener("click", () => {
    const date = new Date(`${$("selected-date").value || today()}T12:00`);
    date.setDate(date.getDate() + step); $("selected-date").value = localISO(date).slice(0, 10); renderEvents();
  });
  $("go-today").addEventListener("click", () => { $("selected-date").value = today(); renderEvents(); });
  $("new-event").addEventListener("click", () => showDraft(null));
  $("close-dialog").addEventListener("click", () => $("event-dialog").close());
  $("command-form").addEventListener("submit", (event) => { event.preventDefault(); send($("command").value); });
  for (const button of document.querySelectorAll("[data-command]")) button.addEventListener("click", () => send(button.dataset.command));
  $("volume").addEventListener("input", () => { $("volume-value").textContent = `${$("volume").value}%`; });
  $("volume-form").addEventListener("submit", (event) => { event.preventDefault(); send(`볼륨 ${$("volume").value}퍼센트`); });
  $("event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!config) { $("event-error").textContent = "서버 연결을 확인하고 페이지를 새로고침해 주세요."; return; }
    $("save-event").disabled = true;
    try {
      const result = await api("/api/events", {title: $("event-title").value, start: $("event-start").value, end: $("event-end").value});
      $("event-dialog").close(); $("selected-date").value = result.event.start.slice(0, 10);
      message(result.message); speak(result.message); notify(result.message); await refreshEvents();
    } catch (error) { $("event-error").textContent = error.message; }
    finally { $("save-event").disabled = false; }
  });
  $("speak-enabled").checked = storage.get("luna-speak") !== "false";
  $("speak-enabled").addEventListener("change", () => { storage.set("luna-speak", String($("speak-enabled").checked)); if (!$("speak-enabled").checked) window.speechSynthesis?.cancel(); });
  $("voice-select").addEventListener("change", () => storage.set("luna-voice", $("voice-select").value));
  $("test-voice").addEventListener("click", () => speak("안녕하세요. 당신의 개인 비서 루나예요. 오늘 하루도 함께할게요.", true));
  loadVoices();
  if ("speechSynthesis" in window) speechSynthesis.addEventListener("voiceschanged", loadVoices);
  setupRecognition();
  try {
    config = await api("/api/config");
    $("connection").lastChild.textContent = "로컬 서버 연결됨";
    $("mode-label").textContent = config.ai ? "AI 대화 + 로컬 명령" : "로컬 명령";
    if (!config.windows) {
      $("desktop-note").textContent = "현재 서버는 Windows가 아닙니다. 일정·대화는 사용 가능하며, PC 제어는 Windows에서 실행하세요.";
      document.querySelectorAll("[data-desktop]").forEach((button) => { button.disabled = true; });
    }
    await refreshEvents();
  } catch (error) { $("connection").textContent = "연결 실패 · 새로고침해 주세요"; message(error.message, "error"); }
}
init();
