// Versioned by main.js's own URL: the browser refetches this module whenever
// main.js changes, so md.js can never go stale on its own.
import { render, esc } from "/static/md.js?v=2";

const $ = s => document.querySelector(s);
const feed = $("#feed"), qEl = $("#q"), sendEl = $("#send"), scroll = $("#scroll");
let chatId = null, busy = false;

/* ---------- theme ---------- */
const root = document.documentElement;
try{ root.dataset.theme = localStorage.getItem("sw-theme") || "light"; }catch(e){}
$("#theme").onclick = () => {
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  try{ localStorage.setItem("sw-theme", root.dataset.theme); }catch(e){}
};

/* ---------- sidebar ---------- */
$("#collapse").onclick = () => { $("#side").classList.add("collapsed");
                                $("#expand").style.display = "grid"; };
$("#expand").onclick  = () => { $("#side").classList.remove("collapsed");
                                $("#expand").style.display = "none"; };

scroll.addEventListener("scroll", () =>
  $("#top").classList.toggle("scrolled", scroll.scrollTop > 8));

function toast(msg, ms = 2800){
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("on"), ms);
}

/* ---------- boot ---------- */
async function boot(){
  try{
    const b = await (await fetch("/api/boot")).json();
    $("#pill-model").innerHTML = b.health.up
      ? `<b>${b.health.lanes.reason}</b>`
      : `<b style="color:var(--bad)">engine offline</b>`;
    $("#pill-index").innerHTML = `<b>${b.index.chunks || 0}</b> chunks`;
    if(b.health.missing?.length) toast("Models missing: " + b.health.missing.join(", "), 7000);
  }catch(e){ toast("Could not reach the server"); }
  await loadChats();
}

/* ---------- chats ---------- */
async function loadChats(){
  const { chats } = await (await fetch("/api/chats")).json();
  const el = $("#chatlist");
  el.innerHTML = chats.map(c => `
    <div class="chatitem ${c.id === chatId ? "on" : ""}" data-id="${c.id}">
      <span class="t">${esc(c.title)}</span>
      <button class="del" data-del="${c.id}" title="Delete">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
      </button>
    </div>`).join("") ||
    `<div style="padding:8px 12px;color:var(--dim2);font-size:12.5px">No chats yet</div>`;

  el.querySelectorAll(".chatitem").forEach(it => it.onclick = e => {
    if(e.target.closest("[data-del]")) return;
    openChat(it.dataset.id);
  });
  el.querySelectorAll("[data-del]").forEach(b => b.onclick = async e => {
    e.stopPropagation();
    await fetch("/api/chats/" + b.dataset.del, { method: "DELETE" });
    if(chatId === b.dataset.del){ chatId = null; feed.innerHTML = ""; hero(); }
    loadChats();
  });
}

async function newChat(){
  const r = await (await fetch("/api/chats", { method: "POST" })).json();
  chatId = r.id; feed.innerHTML = ""; hero();
  $("#chattitle").textContent = "New chat";
  await loadChats(); qEl.focus();
}
$("#newchat").onclick = newChat;

async function openChat(id){
  chatId = id;
  const c = await (await fetch("/api/chats/" + id)).json();
  $("#chattitle").textContent = c.title || "Chat";
  feed.innerHTML = "";
  for(const m of c.messages || []){
    if(m.role === "user") addUser(m.content);
    else addBotStatic(m);
  }
  await loadChats();
  scroll.scrollTop = scroll.scrollHeight;
}

/* ---------- hero ---------- */
const CARDS = [
  ["Deviation check", "What deviation was found on TK-4102, and is it acceptable against the SOP?"],
  ["Spec compare", "Does the PSV-2041 set pressure match the specification?"],
  ["Summary", "Summarise the inspection report in four lines."],
  ["Read a scan", "Which equipment tags are mentioned in the scanned report?"],
];
function hero(){
  feed.innerHTML = `
    <div class="hero">
      <h1>Sovereign Workbench</h1>
      <p>Plant documents, scans and drawings &mdash; all read on this machine.
         Every answer cites its file and page.</p>
      <div class="cards">
        ${CARDS.map(([t, q]) =>
          `<button class="card" data-q="${esc(q)}"><b>${t}</b>${esc(q)}</button>`).join("")}
      </div>
    </div>`;
  feed.querySelectorAll(".card").forEach(c => c.onclick = () => {
    qEl.value = c.dataset.q; ask();
  });
}

/* ---------- message rendering ---------- */
function addUser(text){
  const d = document.createElement("div");
  d.className = "msg user";
  d.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  feed.appendChild(d);
  return d;
}

function sourcesHTML(items){
  if(!items || !items.length) return "";
  return `
  <div class="srcbar">
    <div class="srchead"><span class="chev">›</span>
      ${items.length} source${items.length > 1 ? "s" : ""}</div>
    <div class="srclist">
      ${items.map(s => `
        <div class="src" id="s-${s.n}">
          <div class="r1"><span class="n">${s.n}</span>
            <span class="nm">${esc(s.source)}</span>
            <span class="sc">${s.score}</span></div>
          <div class="loc">${s.page ? "page " + s.page : "—"}${
            s.heading ? " · " + esc(s.heading) : ""} · ${s.kind}</div>
          <div class="tx">${esc(s.text)}</div>
        </div>`).join("")}
    </div>
  </div>`;
}

function wireSources(node){
  const bar = node.querySelector(".srcbar");
  if(bar) bar.querySelector(".srchead").onclick = () => bar.classList.toggle("open");
  node.querySelectorAll(".src").forEach(c =>
    c.onclick = () => c.classList.toggle("open"));
  node.querySelectorAll(".cit").forEach(a => a.onclick = () => {
    const bar = node.querySelector(".srcbar");
    if(bar) bar.classList.add("open");
    const card = node.querySelector("#s-" + a.dataset.n);
    if(!card) return;
    card.classList.add("open", "hl");
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => card.classList.remove("hl"), 1500);
  });
}

function addBotStatic(m){
  const d = document.createElement("div");
  d.className = "msg bot";
  const fhtml = (m.files && m.files.length)
    ? `<div class="outfiles">${m.files.map(fileCard).join("")}</div>` : "";
  d.innerHTML = `${sourcesHTML(m.sources)}
    <div class="prose">${render(m.content)}</div>${fhtml}
    <div class="meta"><span>${esc(m.model || "")}</span>
      ${m.grounded === false ? '<span class="warn">no corpus match</span>' : ""}
      <span class="acts"><button data-copy title="Copy">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="12" height="12" rx="2"/>
        <path d="M5 15V5a2 2 0 012-2h10"/></svg></button></span></div>`;
  feed.appendChild(d);
  wireSources(d);
  const cp = d.querySelector("[data-copy]");
  if(cp) cp.onclick = () => { navigator.clipboard?.writeText(m.content); toast("Copied"); };
  return d;
}

/* ---------- ask ---------- */

const STEP_ICON = { running: '<span class="spin"></span>', done: "\u2713",
                    warn: "!", fail: "\u2717" };
const FILE_TAG = { docx: "DOC", xlsx: "XLS", pptx: "PPT" };

function fileCard(f){
  return `<a class="outfile" href="/api/deliverables/${encodeURIComponent(f.file)}"
             download title="Download ${esc(f.file)}">
    <span class="ft">${FILE_TAG[f.kind] || "FILE"}</span>
    <span class="fn"><b>${esc(f.title || f.file)}</b>
      <span>${esc(f.file)} \u00b7 ${(f.bytes/1024).toFixed(1)} KB</span></span>
    <span class="dl"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round">
      <path d="M12 3v12M7 12l5 5 5-5M4 21h16"/></svg></span>
  </a>`;
}

async function ask(){
  const text = qEl.value.trim();
  if(!text || busy) return;
  if(!chatId){ const r = await (await fetch("/api/chats",{method:"POST"})).json();
               chatId = r.id; }
  if(feed.querySelector(".hero")) feed.innerHTML = "";

  busy = true; sendEl.disabled = true;
  qEl.value = ""; qEl.style.height = "auto";
  // The attached-file chips describe what was just ingested. Once the question
  // is sent they are stale, and leaving them made it look like the upload had
  // not finished.
  filesEl.innerHTML = "";
  addUser(text);

  const bot = document.createElement("div");
  bot.className = "msg bot";
  bot.innerHTML = `
    <div class="activity open">
      <div class="act-head"><span class="chev">\u203a</span>
        <span class="lbl">Working</span><span class="el"></span></div>
      <div class="act-body"></div>
    </div>
    <div class="prose"></div>
    <div class="outfiles" hidden></div>
    <div class="meta"></div>`;
  feed.appendChild(bot);
  scroll.scrollTop = scroll.scrollHeight;

  const act   = bot.querySelector(".activity"),
        head  = bot.querySelector(".act-head"),
        body  = bot.querySelector(".act-body"),
        elEl  = bot.querySelector(".el"),
        prose = bot.querySelector(".prose"),
        outEl = bot.querySelector(".outfiles"),
        meta  = bot.querySelector(".meta");

  head.onclick = () => act.classList.toggle("open");

  const t0 = Date.now();
  const tick = setInterval(() => {
    elEl.textContent = ((Date.now() - t0) / 1000).toFixed(1) + "s";
  }, 100);

  let answer = "", srcItems = [], files = [];

  // one row per step id, updated in place as its status changes
  function putStep(ev){
    let row = body.querySelector(`[data-step="${ev.id}"]`);
    if(!row){
      row = document.createElement("div");
      row.dataset.step = ev.id;
      body.appendChild(row);
    }
    row.className = "stp " + ({running:"run",done:"done",warn:"warn",fail:"fail"}[ev.status]);
    row.innerHTML = `<span class="ico">${STEP_ICON[ev.status]}</span>
      <span class="nm">${esc(ev.label)}</span>
      <span class="dt">${ev.detail ? esc(ev.detail) : ""}</span>`;
    // The panel row IS the live indicator - it already carries a spinner and
    // the current label. A second standalone line below it showed the same
    // text twice on screen at the same time.
    body.scrollTop = body.scrollHeight;
  }

  const es = new EventSource(
    `/api/ask?q=${encodeURIComponent(text)}&chat_id=${chatId}`);

  es.addEventListener("step", e => putStep(JSON.parse(e.data)));

  es.addEventListener("plan", e => {
    const d = JSON.parse(e.data);
    head.querySelector(".lbl").textContent =
      d.deliverable ? `Working \u00b7 will produce a ${d.deliverable.toUpperCase()} file`
                    : "Working";
  });

  es.addEventListener("route", e => {
    const d = JSON.parse(e.data);
    head.querySelector(".lbl").textContent =
      head.querySelector(".lbl").textContent.replace("Working", d.model);
  });

  es.addEventListener("sources", e => {
    const d = JSON.parse(e.data);
    srcItems = d.items;
  });

  es.addEventListener("token", e => {
    answer += JSON.parse(e.data).text;
    prose.innerHTML = render(answer) + '<span class="caret"></span>';
    stick();
  });

  es.addEventListener("file", e => {
    const f = JSON.parse(e.data);
    files.push(f);
    outEl.hidden = false;
    outEl.innerHTML = files.map(fileCard).join("");
  });

  es.addEventListener("error", e => {
    let m = "connection lost";
    try{ m = JSON.parse(e.data).message; }catch(_){}
    if(!answer) prose.innerHTML = `<p style="color:var(--bad)">${esc(m)}</p>`;
    finish();
  });

  es.addEventListener("done", e => {
    const d = JSON.parse(e.data);
    prose.innerHTML = render(answer);
    bot.querySelector(".prose").insertAdjacentHTML("beforebegin", sourcesHTML(srcItems));
    const bits = [
      `<span>${d.model}</span>`, `<span>${d.total_s}s</span>`,
      `<span>${d.tok_per_s} tok/s</span>`,
    ];
    if(d.grounded === false) bits.push('<span class="warn">no corpus match</span>');
    if(d.fell_back) bits.push(`<span class="warn">fell back \u2192 ${d.profile}</span>`);
    if(d.retried) bits.push('<span class="warn">think retry</span>');
    if(d.files && d.files.length)
      bits.push(`<span class="ok">${d.files.length} file produced</span>`);
    bits.push(`<span class="ok">${d.sovereignty.external_calls} external calls</span>`);
    bits.push(`<span class="acts"><button data-copy title="Copy">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="12" height="12" rx="2"/>
      <path d="M5 15V5a2 2 0 012-2h10"/></svg></button></span>`);
    meta.innerHTML = bits.join("");
    meta.querySelector("[data-copy]").onclick = () => {
      navigator.clipboard?.writeText(answer); toast("Copied");
    };
    wireSources(bot);
    head.querySelector(".lbl").textContent =
      (d.files && d.files.length)
        ? `${d.model} \u00b7 produced ${d.files.length} file`
        : d.model;
    act.classList.remove("open");        // collapse once finished
    finish(); loadChats(); pollSov();
  });

  function stick(){
    const near = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 160;
    if(near) scroll.scrollTop = scroll.scrollHeight;
  }

  function finish(){
    es.close(); clearInterval(tick);
    elEl.textContent = ((Date.now() - t0) / 1000).toFixed(1) + "s";
    busy = false; sendEl.disabled = false;
    bot.querySelectorAll(".caret").forEach(c => c.remove());
    bot.querySelectorAll(".stp.run").forEach(r => {
      r.className = "stp done";
      r.querySelector(".ico").textContent = "\u2713";
    });
    qEl.focus();
  }
}

/* ---------- composer ---------- */
qEl.addEventListener("keydown", e => {
  if(e.key === "Enter" && !e.shiftKey){ e.preventDefault(); ask(); }
});
qEl.addEventListener("input", () => {
  qEl.style.height = "auto";
  qEl.style.height = Math.min(qEl.scrollHeight, 200) + "px";
});
sendEl.onclick = ask;

/* ---------- uploads ---------- */
const cbox = $("#cbox"), filesEl = $("#files");
$("#attach").onclick = () => $("#fileinput").click();
$("#fileinput").onchange = e => upload([...e.target.files]);

["dragenter","dragover"].forEach(ev => document.addEventListener(ev, e => {
  e.preventDefault(); cbox.classList.add("drag");
}));
document.addEventListener("dragleave", e => {
  if(!e.relatedTarget) cbox.classList.remove("drag");
});
document.addEventListener("drop", e => {
  e.preventDefault(); cbox.classList.remove("drag");
  upload([...(e.dataTransfer?.files || [])]);
});

async function upload(files){
  for(const f of files){
    const chip = document.createElement("div");
    chip.className = "fchip busy";
    chip.innerHTML = `<span>${esc(f.name)}</span><span class="x">·</span>`;
    filesEl.appendChild(chip);
    const fd = new FormData(); fd.append("file", f);
    try{
      const r = await (await fetch("/api/upload", { method:"POST", body: fd })).json();
      if(!r.ok){ toast(r.error || "Ingest failed", 5000); chip.remove(); continue; }
      chip.classList.remove("busy");
      const okIngest = r.indexed !== false && r.stats.chunks > 0;
      chip.innerHTML = `<span>${esc(f.name)}</span>
        <span style="color:var(--${okIngest ? "dim2" : "bad"})">${
          okIngest ? r.stats.chunks + " chunks" : "no text found"}</span>
        <span class="x" title="Remove">✕</span>`;
      if(!okIngest) toast(r.note || `${f.name}: no text could be extracted`, 6000);
      chip.querySelector(".x").onclick = () => chip.remove();
      $("#pill-index").innerHTML = `<b>${r.index.chunks}</b> chunks`;
      toast(`${f.name} indexed — ${r.stats.chunks} chunks`);
    }catch(err){ toast("Upload failed: " + err.message, 5000); chip.remove(); }
  }
}

/* ---------- sovereignty ---------- */
async function pollSov(){
  try{
    const s = await (await fetch("/api/sovereignty")).json();
    const bad = s.external_calls > 0;
    $("#sov").classList.toggle("bad", bad);
    $("#sov-t").textContent = bad ? "Leak detected" : "Air-gapped";
    $("#sov-s").textContent = `${s.external_calls} external · ${s.local_calls} local`;
    $("#m-ext").textContent = s.external_calls;
    $("#m-ext").style.color = bad ? "var(--bad)" : "var(--ok)";
    $("#m-loc").textContent = s.local_calls;
    $("#m-log").innerHTML = s.recent.slice().reverse().map(a => {
      const t = new Date(a.ts * 1000).toLocaleTimeString();
      const ext = a.verdict === "EXTERNAL";
      return `<div class="${ext ? "x" : "l"}">${t}  ${
        ext ? (a.blocked ? "BLOCKED" : "ALLOWED") : a.verdict
      }  ${esc(a.host)}:${a.port}</div>`;
    }).join("") || "<div>No socket activity</div>";
  }catch(e){}
}
setInterval(pollSov, 2500);
$("#sov").onclick = () => { $("#modal").classList.add("on"); pollSov(); };
$("#modalx").onclick = () => $("#modal").classList.remove("on");
$("#modal").onclick = e => { if(e.target.id === "modal") $("#modal").classList.remove("on"); };
document.addEventListener("keydown", e => {
  if(e.key === "Escape") $("#modal").classList.remove("on");
});

hero(); boot(); pollSov(); qEl.focus();


/* ==================== folder connector ==================== */
/* PS 26117 asks for "a local knowledge base connector" so the assistant can
   ground itself in the organisation's own manuals. Uploading files one at a
   time is not how an engineer works - the manuals are already in a folder. */

const folderPanel = $("#folderpanel"), folderPath = $("#folderpath"),
      folderPrev = $("#folderprev"), folderBtn = $("#folderbtn"),
      folderScan = $("#folderscan");
let folderReady = null;

folderBtn.onclick = () => {
  const show = folderPanel.hidden;
  folderPanel.hidden = !show;
  folderBtn.classList.toggle("on", show);
  if(show) folderPath.focus();
};

folderPath.addEventListener("keydown", e => {
  if(e.key === "Enter"){ e.preventDefault(); scanFolder(); }
});
folderScan.onclick = () => folderReady ? ingestFolder() : scanFolder();

async function scanFolder(){
  const path = folderPath.value.trim();
  if(!path) return;
  folderReady = null;
  folderPrev.hidden = false;
  folderPrev.className = "fprev";
  folderPrev.textContent = "Looking...";
  try{
    const r = await (await fetch(
      "/api/folder/preview?path=" + encodeURIComponent(path))).json();
    if(r.error){
      folderPrev.className = "fprev err";
      folderPrev.textContent = r.error;
      folderScan.textContent = "Scan";
      return;
    }
    const types = Object.entries(r.by_type)
      .sort((a,b) => b[1]-a[1]).map(([k,v]) => `${v} ${k}`).join("  ");
    folderPrev.innerHTML = `<b>${r.supported}</b> readable of ${r.found} files
      <div class="types">${esc(types)}</div>`;
    folderReady = path;
    folderScan.textContent = `Index ${r.supported}`;
  }catch(err){
    folderPrev.className = "fprev err";
    folderPrev.textContent = "Could not read that path: " + err.message;
  }
}

async function ingestFolder(){
  const path = folderReady;
  folderScan.disabled = true;
  folderScan.textContent = "Indexing...";
  folderPrev.className = "fprev";
  folderPrev.innerHTML = `Reading ${esc(path)} - this runs locally and can take
    a while for scans.<div class="bar"><i style="width:35%"></i></div>`;
  try{
    const r = await (await fetch(
      "/api/folder/ingest?path=" + encodeURIComponent(path),
      {method:"POST"})).json();
    if(r.error){
      folderPrev.className = "fprev err"; folderPrev.textContent = r.error;
    }else{
      const extra = r.skipped_self
        ? ` &middot; skipped ${r.skipped_self} file(s) this system generated` : "";
      folderPrev.innerHTML =
        `Indexed <b>${r.indexed}</b> file(s), <b>${r.chunks}</b> passages in
         ${r.seconds.toFixed(0)}s${extra}`;
      $("#pill-index").innerHTML = `<b>${r.index.chunks}</b> chunks`;
      toast(`Folder indexed - ${r.chunks} passages now searchable`);
      folderReady = null;
    }
  }catch(err){
    folderPrev.className = "fprev err";
    folderPrev.textContent = "Failed: " + err.message;
  }finally{
    folderScan.disabled = false;
    folderScan.textContent = "Scan";
  }
}

// Dragging a folder from Finder gives a directory entry rather than a file.
document.addEventListener("drop", e => {
  for(const item of (e.dataTransfer?.items || [])){
    const entry = item.webkitGetAsEntry?.();
    if(entry?.isDirectory){
      folderPanel.hidden = false;
      folderBtn.classList.add("on");
      folderPath.value = entry.fullPath || entry.name;
      folderPrev.hidden = false;
      folderPrev.className = "fprev";
      folderPrev.textContent =
        "The browser only reveals the folder NAME, not its full path. " +
        "Paste the full path here, then press Scan.";
      folderPath.focus();
      return;
    }
  }
}, true);


/* ==================== dictation ==================== */
/* The default Web Speech API streams audio to Google's servers. On this
   project that is disqualifying - and worse, our own monitor would not even
   catch it, because the request comes from the browser rather than from our
   process. Chrome 139+ can run recognition ON DEVICE, so we require that mode
   and refuse the microphone altogether when it is unavailable, rather than
   quietly sending someone's plant discussion to a cloud service. */

const micBtn = $("#micbtn"), micLabel = $("#miclabel");
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, recording = false;

async function micReady(){
  if(!SR) return { ok:false, why:"This browser has no speech recognition." };
  if(!SR.available) return { ok:false,
    why:"This browser cannot confirm on-device speech. Dictation is disabled "
      + "because the fallback would send your audio to a cloud service." };
  try{
    const state = await SR.available({ langs:["en-US"], processLocally:true });
    if(state === "available") return { ok:true };
    if(state === "downloadable" || state === "downloading")
      return { ok:false, downloadable:true,
        why:"The on-device speech model is not installed yet." };
    return { ok:false,
      why:"On-device speech is unavailable here, and the cloud fallback is "
        + "not acceptable on an air-gapped workbench." };
  }catch(e){
    return { ok:false, why:"Could not verify on-device speech: " + e.message };
  }
}

micBtn.onclick = async () => {
  if(recording){ rec?.stop(); return; }
  const chk = await micReady();
  if(!chk.ok){
    toast(chk.why, 7000);
    if(chk.downloadable){
      try{
        toast("Downloading the on-device speech model once...", 8000);
        await SR.install({ langs:["en-US"], processLocally:true });
        toast("On-device speech installed - press Speak again.", 5000);
      }catch(e){ toast("Install failed: " + e.message, 6000); }
    }
    return;
  }

  rec = new SR();
  rec.lang = "en-US";
  rec.continuous = true;
  rec.interimResults = true;
  rec.processLocally = true;      // the whole point - never leaves the machine

  const before = qEl.value;
  rec.onstart = () => {
    recording = true;
    micBtn.classList.add("rec");
    micLabel.textContent = "Stop";
    toast("Listening on-device - audio stays on this machine", 3000);
  };
  rec.onresult = ev => {
    let text = "";
    for(let i = ev.resultIndex; i < ev.results.length; i++)
      text += ev.results[i][0].transcript;
    qEl.value = (before ? before + " " : "") + text;
    qEl.style.height = "auto";
    qEl.style.height = Math.min(qEl.scrollHeight, 200) + "px";
  };
  rec.onerror = ev => toast("Dictation error: " + ev.error, 5000);
  rec.onend = () => {
    recording = false;
    micBtn.classList.remove("rec");
    micLabel.textContent = "Speak";
    qEl.focus();
  };
  try{ rec.start(); }catch(e){ toast("Could not start: " + e.message, 5000); }
};

// Say up front whether dictation is possible, rather than after a click.
micReady().then(c => {
  if(!c.ok && !c.downloadable){
    micBtn.disabled = true;
    micBtn.title = c.why;
  }
});
