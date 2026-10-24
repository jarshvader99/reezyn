"use strict";

const $ = (id) => document.getElementById(id);
const FEED_LIMIT = 6;
const READ_KEY = "reezyn.read_aloud";

const els = {
  pair: $("pair"),
  app: $("app"),
  pairForm: $("pair-form"),
  pairCode: $("pair-code"),
  pairButton: $("pair-button"),
  pairError: $("pair-error"),
  connection: $("connection"),
  pulse: $("pulse"),
  agent: $("agent"),
  state: $("state"),
  project: $("project"),
  current: $("current"),
  banners: $("banners"),
  permission: $("permission"),
  permissionText: $("permission-text"),
  allow: $("allow"),
  deny: $("deny"),
  voice: $("voice"),
  feed: $("feed"),
  empty: $("empty"),
  reply: $("reply"),
  send: $("send"),
  mic: $("mic"),
  recap: $("recap"),
  speak: $("speak"),
  toast: $("toast"),
  footer: $("footer-status"),
};

const AGENTS = {
  claude: "Claude Code",
  codex: "Codex CLI",
  gemini: "Gemini CLI",
  cursor: "Cursor",
  opencode: "OpenCode",
  freebuff: "Freebuff",
  simulation: "Simulation",
  generic: "Custom",
};

const STATES = {
  listening: "listening",
  speaking: "speaking",
  paused: "paused",
  setup: "setup needed",
  error: "error",
  idle: "idle",
};

let latest = null;
let stream = null;
let reconnectTimer = 0;
let firstRender = true;
let lastSpoken = "";
let lastPermission = "";
let toastTimer = 0;
let readPreference = localStorage.getItem(READ_KEY) !== null;
let readAloud = readPreference && localStorage.getItem(READ_KEY) === "1";
let micPressed = false;
let recorder = null;
let micTimer = 0;
let remoteAudioOn = false;
let speechWatchdog = 0;
let speechReleaseTimer = 0;
let speechGeneration = 0;
let anchorContext = null;
let anchorSource = null;

// -- small helpers ------------------------------------------------------

function toast(message, bad = false) {
  if (!message) return;
  els.toast.textContent = message;
  els.toast.classList.toggle("bad", Boolean(bad));
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    els.toast.textContent = "";
    els.toast.classList.remove("bad");
  }, 5000);
}

function connectLabel(text, live = false) {
  els.connection.textContent = text;
  els.connection.classList.toggle("live", live);
}

function showPair(message = "") {
  if (stream) {
    stream.close();
    stream = null;
  }
  els.pairError.textContent = message;
  els.app.classList.add("hidden");
  els.pair.classList.remove("hidden");
  els.pairCode.focus();
}

function showApp() {
  els.pair.classList.add("hidden");
  els.app.classList.remove("hidden");
}

function basename(value) {
  if (!value) return "";
  const clean = String(value).replace(/\/+$/, "");
  const parts = clean.split("/");
  return parts[parts.length - 1] || clean;
}

function agentLabel(value) {
  if (!value) return "No agent yet";
  return AGENTS[value] || String(value).replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
    cache: "no-store",
  });
  if (response.status === 401) {
    showPair("Pairing expired. Enter a new code from the Reezyn menu.");
    throw new Error("Pairing expired.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || data.message || "The Mac did not accept that.");
  }
  return data;
}

// -- pairing ------------------------------------------------------------

els.pairCode.addEventListener("input", () => {
  els.pairCode.value = els.pairCode.value.replace(/\D/g, "").slice(0, 6);
});

els.pairForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = els.pairCode.value.trim();
  if (code.length !== 6) {
    els.pairError.textContent = "Enter the six-digit code from the menu.";
    return;
  }
  els.pairButton.disabled = true;
  els.pairButton.textContent = "Pairing…";
  els.pairError.textContent = "";
  try {
    await api("/api/pair", { code, name: "Phone" });
    els.pairCode.value = "";
    showApp();
    firstRender = true;
    lastSpoken = "";
    lastPermission = "";
    await loadState();
  } catch (error) {
    els.pairError.textContent = error.message;
  } finally {
    els.pairButton.disabled = false;
    els.pairButton.textContent = "Pair";
  }
});

// -- state --------------------------------------------------------------

async function loadState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (response.status === 401) {
      showPair();
      connectLabel("not paired");
      return;
    }
    if (!response.ok) throw new Error("The Mac is not answering.");
    const state = await response.json();
    showApp();
    render(state);
    connect();
  } catch (error) {
    if (latest) {
      connectLabel("offline");
      els.pulse.classList.add("offline");
      toast(error.message, true);
    } else {
      showPair("Could not reach the Mac. Check Tailscale and that Reezyn is running.");
    }
  }
}

function connect() {
  if (stream) return;
  if (!("EventSource" in window)) {
    connectLabel("polling");
    window.setInterval(loadState, 3000);
    return;
  }
  stream = new EventSource("/api/events");
  stream.addEventListener("state", (event) => {
    try {
      render(JSON.parse(event.data));
      connectLabel("live", true);
    } catch (error) {
      // A malformed frame is not worth tearing the stream down for.
    }
  });
  stream.addEventListener("open", () => connectLabel("live", true));
  stream.addEventListener("error", () => {
    connectLabel("reconnecting");
    els.pulse.classList.add("offline");
    if (stream && stream.readyState === EventSource.CLOSED) {
      stream = null;
      window.setTimeout(loadState, 1500);
      return;
    }
    // EventSource retries a 401 forever and never exposes the status. A normal
    // state fetch tells an expired cookie apart from a sleeping Mac.
    window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(() => {
      if (stream) loadState();
    }, 3000);
  });
}

function render(state) {
  latest = state;
  if (!readPreference && typeof state.read_aloud_default === "boolean") {
    readAloud = state.read_aloud_default;
    els.speak.checked = readAloud;
  }
  els.pulse.classList.remove("offline");
  const speaking = Boolean(state.speaking);
  const paused = Boolean(state.paused);
  els.pulse.classList.toggle("speaking", speaking && !paused);
  els.pulse.classList.toggle("paused", paused);

  els.agent.textContent = agentLabel(state.active_agent || state.agent);
  els.state.textContent = STATES[state.state] || state.state || "idle";
  els.state.className = "pill " + (state.state || "");
  els.project.textContent = basename(state.project);

  const current = String(state.current || "");
  const recent = Array.isArray(state.recent) ? state.recent.filter(Boolean) : [];
  const line = speaking && current ? current : (recent[0] || "");
  els.current.textContent = line || "Waiting for the next thought.";

  renderBanners(state);
  renderPermission(state.permission);
  renderVoice(state.voice);
  renderFeed(recent);
  renderRemote(state);

  if (firstRender) {
    firstRender = false;
    lastSpoken = line;
    lastPermission = state.permission ? String(state.permission.text || "") : "";
  } else if (readAloud) {
    if (line && line !== lastSpoken) {
      lastSpoken = line;
      speak(line);
    }
    const permissionText = state.permission ? String(state.permission.text || "") : "";
    if (permissionText && permissionText !== lastPermission) {
      lastPermission = permissionText;
      speak("The agent needs permission. " + permissionText);
    } else if (!permissionText) {
      lastPermission = "";
    }
  }
}

function renderBanners(state) {
  els.banners.replaceChildren();
  if (state.locked) {
    banner("The Mac is locked. Replies wait in the queue until you unlock it.");
  }
  const queued = Number(state.queued || 0);
  if (queued > 0) {
    banner(queued === 1 ? "1 reply is queued." : `${queued} replies are queued.`);
  }
  if (state.can_type === false) {
    banner("Accessibility access is off on the Mac, so sending and permissions are blocked.", true);
  }
  if (state.state === "setup") {
    banner("The voice model is not installed on the Mac.");
  }
  if (state.state === "error" && state.last_error) {
    banner(state.last_error, true);
  }
  if (state.error && state.error !== state.last_error) {
    banner(state.error, true);
  }
}

function banner(text, bad = false) {
  const node = document.createElement("div");
  node.className = "banner" + (bad ? " bad" : "");
  node.textContent = text;
  els.banners.appendChild(node);
}

function renderPermission(permission) {
  if (!permission) {
    els.permission.classList.add("hidden");
    return;
  }
  els.permission.classList.remove("hidden");
  els.permissionText.textContent = permission.text || "The agent is waiting for an answer.";
}

function renderVoice(voice) {
  const partial = voice && voice.partial ? String(voice.partial) : "";
  if (!partial) {
    els.voice.classList.add("hidden");
    els.voice.textContent = "";
    return;
  }
  els.voice.classList.remove("hidden");
  els.voice.textContent = `On the Mac: “${partial}”`;
}

function renderFeed(recent) {
  els.feed.replaceChildren();
  els.empty.classList.toggle("hidden", recent.length > 0);
  recent.slice(0, FEED_LIMIT).forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    els.feed.appendChild(item);
  });
}

function renderRemote(state) {
  const remote = state.remote || {};
  if (remote.url) {
    try {
      const host = new URL(remote.url).host;
      els.footer.textContent = `Private over Tailscale · ${host}`;
    } catch (error) {
      els.footer.textContent = "Private over Tailscale";
    }
  } else {
    els.footer.textContent = "Local and private";
  }
}

// -- replies ------------------------------------------------------------

els.send.addEventListener("click", sendReply);
els.reply.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    sendReply();
  }
});

async function sendReply() {
  const text = els.reply.value.trim();
  if (!text) {
    toast("Write a reply first.", true);
    els.reply.focus();
    return;
  }
  els.send.disabled = true;
  els.send.textContent = "Sending…";
  try {
    const result = await api("/api/say", { text, submit: true });
    toast(result.message || "Sent.");
    if (result.ok || result.queued) {
      els.reply.value = "";
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.send.disabled = false;
    els.send.textContent = "Send";
  }
}

els.allow.addEventListener("click", () => answerPermission(true));
els.deny.addEventListener("click", () => answerPermission(false));

async function answerPermission(allow) {
  els.allow.disabled = true;
  els.deny.disabled = true;
  try {
    const result = await api("/api/permission", { allow });
    toast(result.message || (allow ? "Allowed." : "Denied."));
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.allow.disabled = false;
    els.deny.disabled = false;
  }
}

els.recap.addEventListener("click", async () => {
  els.recap.disabled = true;
  els.recap.textContent = "Recapping…";
  try {
    const result = await api("/api/recap", {});
    toast(result.text || "Recap spoken on the Mac.");
    if (readAloud && result.text) speak(result.text);
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.recap.disabled = false;
    els.recap.textContent = "Recap";
  }
});

// -- read aloud ---------------------------------------------------------

// AirPods follow whichever device starts audio. Reezyn narrates on the Mac all
// the time, so while this page reads aloud it asks the Mac to stay silent and
// refreshes that claim. Otherwise the Mac keeps stealing the headphones and
// the phone's speech comes out of nowhere—or not at all.
function setRemoteListening(enabled) {
  if (!enabled && !remoteAudioOn) return;
  remoteAudioOn = enabled;
  try {
    fetch("/api/read-aloud", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
      cache: "no-store",
      keepalive: true,
    }).catch(() => {
      // The heartbeat retries while read-aloud is on; a failed off is covered
      // by the server's expiry so the Mac is never muted forever.
    });
  } catch (error) {
    // Never let an audio-policy call break the page.
  }
}

function releaseRemoteListening() {
  if (!remoteAudioOn) return;
  try {
    const body = JSON.stringify({ enabled: false });
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/api/read-aloud", new Blob([body], { type: "application/json" }));
    } else {
      fetch("/api/read-aloud", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        keepalive: true,
      }).catch(() => {});
    }
  } catch (error) {
    // The server's TTL is the backstop.
  }
  remoteAudioOn = false;
}

// A near-silent WebAudio tone keeps iOS's audio session on this phone. Without
// one, a route change can leave speechSynthesis thinking it is speaking while
// nothing reaches the speaker.
function startAudioAnchor() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    if (!anchorContext) anchorContext = new AudioContext();
    anchorContext.resume().catch(() => {});
    if (!anchorSource) {
      const gain = anchorContext.createGain();
      gain.gain.value = 0.0002;
      const oscillator = anchorContext.createOscillator();
      oscillator.type = "sine";
      oscillator.frequency.value = 70;
      oscillator.connect(gain);
      gain.connect(anchorContext.destination);
      oscillator.start();
      anchorSource = oscillator;
    }
  } catch (error) {
    // The anchor is best effort; speech still works without it.
  }
}

function stopAudioAnchor() {
  try {
    if (anchorSource) anchorSource.stop();
  } catch (error) {
    // Already stopped.
  }
  anchorSource = null;
  if (anchorContext) {
    anchorContext.close().catch(() => {});
    anchorContext = null;
  }
}

els.speak.checked = readAloud;
els.speak.addEventListener("change", () => {
  readAloud = els.speak.checked;
  readPreference = true;
  localStorage.setItem(READ_KEY, readAloud ? "1" : "0");
  if (readAloud) {
    // iOS only allows audio after a gesture; this is that gesture.
    startAudioAnchor();
    setRemoteListening(true);
    speakNow("Read aloud on.", 0);
    if (latest) {
      lastSpoken = "";
    }
  } else {
    window.clearTimeout(speechWatchdog);
    window.clearTimeout(speechReleaseTimer);
    speechGeneration += 1;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    setRemoteListening(false);
    stopAudioAnchor();
    toast("Read aloud off.");
  }
});

function speak(text) {
  if (!readAloud || !("speechSynthesis" in window)) return;
  const clean = String(text).replace(/\s+/g, " ").trim().slice(0, 600);
  if (!clean) return;
  startAudioAnchor();
  setRemoteListening(true);
  speakNow(clean, 0);
}

function speakNow(text, attempt) {
  if (!readAloud) return;
  if (!("speechSynthesis" in window)) {
    setRemoteListening(false);
    stopAudioAnchor();
    toast("This browser cannot read aloud.", true);
    return;
  }
  const synth = window.speechSynthesis;
  window.clearTimeout(speechWatchdog);
  const generation = ++speechGeneration;
  try {
    // A cancel/resume pair is the documented way out of the iOS speech stall
    // that follows a Bluetooth route change.
    synth.cancel();
    synth.resume();
  } catch (error) {
    // Older Safari has no resume; speak still works.
  }

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.02;
  utterance.pitch = 1.0;
  utterance.volume = 1;
  utterance.lang = navigator.language || "en-US";
  let started = false;
  utterance.onstart = () => {
    started = true;
    window.clearTimeout(speechWatchdog);
    window.clearTimeout(speechReleaseTimer);
    speechReleaseTimer = 0;
    // Refresh now that audio is really flowing, so this phone owns the route.
    setRemoteListening(true);
  };
  utterance.onend = () => window.clearTimeout(speechWatchdog);
  utterance.onerror = () => window.clearTimeout(speechWatchdog);

  try {
    synth.speak(utterance);
  } catch (error) {
    window.clearTimeout(speechWatchdog);
    return;
  }

  speechWatchdog = window.setTimeout(() => {
    if (generation !== speechGeneration || !readAloud) return;
    if (started) return;
    if (attempt < 1) {
      try {
        synth.cancel();
        synth.resume();
      } catch (error) {
        // Retry anyway.
      }
      window.setTimeout(() => {
        if (generation === speechGeneration && readAloud) speakNow(text, attempt + 1);
      }, 250);
      return;
    }
    // Two attempts produced no start event, usually because the route is
    // still moving between the Mac and the phone. Keep the Mac quiet for a
    // short grace period so the next line can claim the headphones, then let
    // the Mac speak again rather than leaving both devices silent forever.
    toast("Phone audio is switching. The next line will retry.", true);
    window.clearTimeout(speechReleaseTimer);
    speechReleaseTimer = window.setTimeout(() => {
      if (!readAloud) return;
      setRemoteListening(false);
      toast("Phone audio is silent. Turn Read aloud off and on to reconnect.", true);
    }, 20000);
  }, 1800);
}

// -- push to talk -------------------------------------------------------

els.mic.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  micPressed = true;
  startRecording();
});
els.mic.addEventListener("pointerup", (event) => {
  event.preventDefault();
  micPressed = false;
  stopRecording();
});
els.mic.addEventListener("pointercancel", () => {
  micPressed = false;
  stopRecording();
});
els.mic.addEventListener("contextmenu", (event) => event.preventDefault());
window.addEventListener("pointerup", () => {
  if (micPressed || recorder) {
    micPressed = false;
    stopRecording();
  }
});
window.addEventListener("blur", () => {
  if (recorder) {
    micPressed = false;
    stopRecording();
  }
});

async function startRecording() {
  if (recorder) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    toast("The microphone needs an HTTPS page. Open Reezyn through Tailscale, not a local address.", true);
    return;
  }
  els.mic.textContent = "Starting…";
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
  } catch (error) {
    els.mic.textContent = "Hold to talk";
    toast(error && error.name === "NotAllowedError"
      ? "Microphone access is blocked for this page."
      : "Could not open the microphone.", true);
    return;
  }
  if (!micPressed) {
    stream.getTracks().forEach((track) => track.stop());
    els.mic.textContent = "Hold to talk";
    return;
  }

  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) {
    stream.getTracks().forEach((track) => track.stop());
    toast("This browser cannot record audio.", true);
    return;
  }
  const context = new AudioContext();
  try {
    await context.resume();
  } catch (error) {
    // Some browsers need a moment; the context may still run.
  }
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(2048, 1, 1);
  const chunks = [];
  const mute = context.createGain();
  mute.gain.value = 0;
  processor.onaudioprocess = (event) => {
    chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  };
  source.connect(processor);
  processor.connect(mute);
  mute.connect(context.destination);

  recorder = { stream, context, source, processor, mute, chunks };
  els.mic.classList.add("recording");
  els.mic.textContent = "Release to transcribe";
  micTimer = window.setTimeout(() => {
    micPressed = false;
    stopRecording();
  }, 59000);
}

function stopRecording() {
  if (!recorder) return;
  const session = recorder;
  recorder = null;
  window.clearTimeout(micTimer);
  const rate = session.context.sampleRate;
  els.mic.classList.remove("recording");
  els.mic.textContent = "Transcribing…";
  els.mic.disabled = true;
  try {
    session.processor.disconnect();
    session.source.disconnect();
    session.mute.disconnect();
    session.stream.getTracks().forEach((track) => track.stop());
    session.context.close();
  } catch (error) {
    // Cleanup is best effort; the audio is already captured.
  }

  const total = session.chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  if (total < rate * 0.25) {
    els.mic.textContent = "Hold to talk";
    els.mic.disabled = false;
    toast("Hold the button while you speak.", true);
    return;
  }
  const samples = mergeChunks(session.chunks, total);
  const pcm = downsample(samples, rate, 16000);
  transcribe(pcm);
}

function mergeChunks(chunks, total) {
  const merged = new Float32Array(total);
  let offset = 0;
  chunks.forEach((chunk) => {
    merged.set(chunk, offset);
    offset += chunk.length;
  });
  return merged;
}

function downsample(samples, inputRate, outputRate) {
  const ratio = inputRate / outputRate;
  const length = Math.max(1, Math.floor(samples.length / ratio));
  const output = new Int16Array(length);
  if (ratio > 1) {
    // Speech at 16 kHz: averaging blocks is a cheap, gentle low-pass.
    for (let i = 0; i < length; i += 1) {
      const start = Math.floor(i * ratio);
      const end = Math.min(samples.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      let count = 0;
      for (let j = start; j < end; j += 1) {
        sum += samples[j];
        count += 1;
      }
      output[i] = toInt16(count ? sum / count : samples[start] || 0);
    }
    return output;
  }
  for (let i = 0; i < length; i += 1) {
    const position = i * ratio;
    const lower = Math.floor(position);
    const upper = Math.min(samples.length - 1, lower + 1);
    const fraction = position - lower;
    output[i] = toInt16(samples[lower] * (1 - fraction) + samples[upper] * fraction);
  }
  return output;
}

function toInt16(value) {
  const clamped = Math.max(-1, Math.min(1, value || 0));
  return clamped < 0 ? Math.round(clamped * 32768) : Math.round(clamped * 32767);
}

async function transcribe(pcm) {
  try {
    const response = await fetch("/api/transcribe", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Sample-Rate": "16000",
      },
      body: pcm.buffer,
      cache: "no-store",
    });
    if (response.status === 401) {
      showPair("Pairing expired. Enter a new code from the Reezyn menu.");
      return;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Could not transcribe that.");
    const text = String(data.text || "").trim();
    if (!text) {
      toast("Didn't catch that. Try again a little closer.", true);
      return;
    }
    els.reply.value = (els.reply.value ? els.reply.value.trim() + " " : "") + text;
    els.reply.focus();
    toast("Heard: “" + text + "”");
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.mic.textContent = "Hold to talk";
    els.mic.disabled = false;
  }
}

// -- lifecycle ----------------------------------------------------------

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    if (readAloud) {
      // Coming back from the lock screen is the other moment iOS drops speech
      // synthesis; re-arm both the audio session and the current line.
      startAudioAnchor();
      if ("speechSynthesis" in window) window.speechSynthesis.resume();
      lastSpoken = "";
    }
    if (latest) loadState();
  }
});

window.addEventListener("pagehide", () => {
  window.clearTimeout(speechWatchdog);
  window.clearTimeout(speechReleaseTimer);
  speechGeneration += 1;
  releaseRemoteListening();
  stopAudioAnchor();
});

window.addEventListener(
  "pointerdown",
  () => {
    // A tap is the one thing iOS accepts as permission to start an audio
    // session, including after the page was restored with read-aloud on.
    if (!readAloud) return;
    startAudioAnchor();
    if ("speechSynthesis" in window) window.speechSynthesis.resume();
  },
  { capture: true }
);

window.setInterval(() => {
  if (!stream && latest) {
    loadState();
  }
  if (readAloud && remoteAudioOn) {
    // Only keep the claim alive once this page has actually started reading.
    // A restored preference that iOS has not unlocked must not mute the Mac.
    setRemoteListening(true);
    if (anchorContext && anchorContext.state !== "running") {
      anchorContext.resume().catch(() => {});
    }
  }
}, 15000);

loadState();
