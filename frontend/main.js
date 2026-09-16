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

/* ---------- step icons ----------
   The activity panel used one dot for every step, so six different kinds of
   work looked identical. Each stage now carries its own mark, and the model
   lanes carry the vendor's own logo, because "which model ran this" is the
   thing the panel exists to answer.

   Every glyph is inline SVG. A CDN icon pack is exactly the dependency the
   air gap forbids - blocked when sealed, and silently, since a missing icon
   renders as nothing rather than as an error.                              */

const QWEN_LOGO = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
  <path d="M12 2.2 3.1 7.3v9.4L12 21.8l8.9-5.1V7.3L12 2.2Zm6.6 13.2L12 19.2
    l-6.6-3.8V8.6L12 4.8l6.6 3.8v6.8Z"/>
  <path d="M12 7.1 7.8 9.5v4.9L12 16.9l4.2-2.5V9.5L12 7.1Z" opacity=".55"/>
</svg>`;

const STEP_SVG = {
  understand: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="11" cy="11" r="7"/><path d="M11 8v3l2 1"/></svg>`,
  route: QWEN_LOGO,
  retrieve: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 21 21"/></svg>`,
  pid: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M3 12h4M17 12h4"/><circle cx="12" cy="12" r="3"/>
      <path d="M12 3v6M12 15v6"/></svg>`,
  actions: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 6h16M4 12h16M4 18h10"/></svg>`,
  compare: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="4" width="7" height="16" rx="1"/>
      <rect x="14" y="4" width="7" height="16" rx="1"/></svg>`,
  skills: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 3 4 7v6c0 4.5 3.4 7.3 8 8 4.6-.7 8-3.5 8-8V7l-8-4Z"/></svg>`,
  answer: QWEN_LOGO,
  code: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="m9 8-5 4 5 4M15 8l5 4-5 4"/></svg>`,
  verify: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 3 4 6v6c0 4.4 3.3 7.4 8 9 4.7-1.6 8-4.6 8-9V6l-8-3Z"/>
      <path d="m9 12 2 2 4-4"/></svg>`,
  verify_claims: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>`,
  spec: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/>
      <path d="M14 3v5h5"/></svg>`,
  file: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/>
      <path d="M9 14h6M9 17h4"/></svg>`,
  scan: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1
        0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M4 12h16"/></svg>`,
  index: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <ellipse cx="12" cy="6" rx="8" ry="3"/>
      <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3
        8-3v-6"/></svg>`,
};

const STEP_FALLBACK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M14.7 6.3a4 4 0 0 1-5 5L5 16v3h3l4.7-4.7a4 4 0 0 1 5-5l2-2-3-3-2 2Z"/>
  </svg>`;

/* The collapsed header carries a stacked, slightly fanned row of the icons for
   the steps that actually ran - so what happened is legible without opening
   the panel. Deduplicated: two retrieval passes are one search, not two. */
function stackIcons(head, body){
  let stack = head.querySelector(".stack");
  if(!stack){
    stack = document.createElement("span");
    stack.className = "stack";
    head.insertBefore(stack, head.querySelector(".lbl"));
  }
  const seen = [];
  body.querySelectorAll(".stp").forEach(r => {
    const id = r.dataset.step;
    if(id && !seen.includes(id)) seen.push(id);
  });
  const show = seen.slice(0, 6);
  stack.innerHTML = show.map((id, i) =>
    `<span class="si" style="rotate:${show.length > 1 ? (i % 2 ? "-8deg" : "8deg") : "0deg"};
       z-index:${i}">${stepIcon(id)}</span>`).join("")
    + (seen.length > show.length
        ? `<span class="si more">+${seen.length - show.length}</span>` : "");
}

function stepIcon(id){
  return STEP_SVG[id] || STEP_FALLBACK;
}

/* Filenames in a step detail were dead text: the panel named the evidence and
   then made you go find it on disk. Anything that looks like an indexed file
   becomes a link that opens it. */
const FILE_IN_TEXT =
  /\b([\w.\-]+\.(?:png|jpe?g|webp|bmp|tiff?|pdf|docx?|xlsx?|pptx?|csv|md|txt|html))\b/gi;

function linkFiles(text){
  return esc(text).replace(FILE_IN_TEXT, (m) =>
    `<a class="srcfile" href="/api/source/${encodeURIComponent(m)}"
        target="_blank" rel="noopener" title="Open ${m}">${m}</a>`);
}

/* ---------- ask ---------- */

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
        <span class="lbl"><span class="shimmer">Working</span></span><span class="el"></span></div>
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

  // The header doubles as the accordion label: shimmering while the run is
  // live, plain once it has something final to say.
  function lblText(txt, live){
    const lbl = head.querySelector(".lbl");
    lbl.innerHTML = live ? `<span class="shimmer"></span>` : "";
    (live ? lbl.firstChild : lbl).textContent = txt;
  }

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
    row.innerHTML = `<span class="rail"><span class="ic">${stepIcon(ev.id)}</span>
        <span class="line"></span></span>
      <span class="nm">${ev.status === "running"
        ? `<span class="shimmer">${esc(ev.label)}</span>` : esc(ev.label)}</span>
      <span class="dt">${ev.detail ? linkFiles(ev.detail) : ""}</span>`;
    stackIcons(head, body);
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
    lblText(d.deliverable
      ? `Working \u00b7 will produce a ${d.deliverable.toUpperCase()} file`
      : "Working", true);
  });

  es.addEventListener("route", e => {
    const d = JSON.parse(e.data);
    lblText(head.querySelector(".lbl").textContent.replace("Working", d.model), true);
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
    const nSteps = body.querySelectorAll(".stp").length;
    lblText((d.files && d.files.length)
      ? `${d.model} \u00b7 produced ${d.files.length} file`
      : `${d.model} \u00b7 ${nSteps} step${nSteps === 1 ? "" : "s"}`, false);
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
      const sh = r.querySelector(".nm .shimmer");
      if(sh) sh.replaceWith(sh.textContent);
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
/* One way in: the machine's own folder dialog.
   There used to be a path box with a Scan button as well, which appeared
   whenever the dialog did not return a path - including on Cancel, because the
   cancel signal was being missed. Two ways to do one thing, and the wrong one
   showed up exactly when the user had just said no. Typing a path still works,
   in the chat: "analyse the documents in ~/Documents/Plant Manuals". */

const folderBtn = $("#folderbtn");

folderBtn.onclick = async () => {
  folderBtn.disabled = true;
  folderBtn.classList.add("on");
  try{
    const r = await (await fetch("/api/folder/choose", {method:"POST"})).json();
    if(r.path){ analyseFolder(r.path); return; }
    if(r.cancelled) return;                 // said no - do nothing at all
    toast((r.error || "Could not open the folder chooser")
          + ' - you can also say it in the chat: "analyse the documents in '
          + '~/Documents/Plant Manuals"', 7000);
  }catch(e){
    toast("Could not open the folder chooser: " + e.message, 5000);
  }finally{
    folderBtn.disabled = false;
    folderBtn.classList.remove("on");
  }
};

/* The in-page folder browser is gone.
   It existed because a browser cannot return a real filesystem path, but the
   backend is a local process and opens the machine's own dialog - so the page
   was showing a second, worse picker behind the real one. The typed path stays
   as the fallback for when no native dialog is available. */

/* ==================== folder analysis ==================== */

function analyseFolder(path){
  if(busy) { toast("Still working on the last question", 3000); return; }
  busy = true; sendEl.disabled = true;
  if(feed.querySelector(".hero")) feed.innerHTML = "";

  const name = path.split("/").filter(Boolean).pop() || path;
  addUser(`Analyse the folder ${name}`);

  const bot = document.createElement("div");
  bot.className = "msg bot";
  bot.innerHTML = `
    <div class="activity open">
      <div class="act-head"><span class="chev">\u203a</span>
        <span class="lbl"><span class="shimmer">Reading ${esc(name)}</span></span><span class="el"></span></div>
      <div class="act-body"></div>
    </div>
    <div class="fprev" id="fscan" hidden></div>
    <div class="prose"><span class="caret"></span></div>
    <div class="meta"></div>`;
  feed.appendChild(bot);
  scroll.scrollTop = scroll.scrollHeight;

  const act = bot.querySelector(".activity"), head = bot.querySelector(".act-head"),
        body = bot.querySelector(".act-body"), elEl = bot.querySelector(".el"),
        fscan = bot.querySelector("#fscan"), prose = bot.querySelector(".prose"),
        meta = bot.querySelector(".meta");
  // The header doubles as the accordion label: shimmering while the run is
  // live, plain once it has something final to say.
  function lblText(txt, live){
    const lbl = head.querySelector(".lbl");
    lbl.innerHTML = live ? `<span class="shimmer"></span>` : "";
    (live ? lbl.firstChild : lbl).textContent = txt;
  }

  head.onclick = () => act.classList.toggle("open");

  const t0 = Date.now();
  const tick = setInterval(() =>
    elEl.textContent = ((Date.now() - t0) / 1000).toFixed(1) + "s", 100);

  let answer = "", srcItems = [];
  function putStep(ev){
    let row = body.querySelector(`[data-step="${ev.id}"]`);
    if(!row){ row = document.createElement("div"); row.dataset.step = ev.id;
              body.appendChild(row); }
    row.className = "stp " + ({running:"run",done:"done",warn:"warn",fail:"fail"}[ev.status]);
    row.innerHTML = `<span class="rail"><span class="ic">${stepIcon(ev.id)}</span>
        <span class="line"></span></span>
      <span class="nm">${ev.status === "running"
        ? `<span class="shimmer">${esc(ev.label)}</span>` : esc(ev.label)}</span>
      <span class="dt">${ev.detail ? linkFiles(ev.detail) : ""}</span>`;
    stackIcons(head, body);
  }

  const es = new EventSource("/api/folder/analyse?path=" + encodeURIComponent(path));
  es.addEventListener("step", e => putStep(JSON.parse(e.data)));

  es.addEventListener("folder_scan", e => {
    const d = JSON.parse(e.data);
    const types = Object.entries(d.by_type).sort((a,b)=>b[1]-a[1])
      .map(([k,v]) => `${v} ${k}`).join("  ");
    fscan.hidden = false;
    fscan.innerHTML = `<b>${d.supported}</b> readable of <b>${d.found}</b> files
      in ${esc(d.root)}<div class="types">${esc(types)}</div>`;
    $("#pill-index").title = d.root;
  });

  es.addEventListener("sources", e => { srcItems = JSON.parse(e.data).items; });
  es.addEventListener("token", e => {
    answer += JSON.parse(e.data).text;
    prose.innerHTML = render(answer) + '<span class="caret"></span>';
    const near = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 160;
    if(near) scroll.scrollTop = scroll.scrollHeight;
  });
  es.addEventListener("error", e => {
    let m = "connection lost";
    try{ m = JSON.parse(e.data).message; }catch(_){}
    if(!answer) prose.innerHTML = `<p style="color:var(--bad)">${esc(m)}</p>`;
    finish();
  });
  es.addEventListener("done", e => {
    const d = JSON.parse(e.data);
    prose.innerHTML = d.empty
      ? "<p>No readable documents in that folder.</p>" : render(answer);
    if(srcItems.length)
      bot.querySelector(".prose").insertAdjacentHTML("beforebegin", sourcesHTML(srcItems));
    const bits = [`<span>${esc(d.model || "")}</span>`,
                  `<span>${d.total_s}s</span>`,
                  `<span>${d.files || 0} file(s)</span>`];
    if(d.chunks) bits.push(`<span>${d.chunks} passages</span>`);
    bits.push(`<span class="ok">${d.sovereignty.external_calls} external calls</span>`);
    meta.innerHTML = bits.map(b => `<span>${b}</span>`).join("");
    wireSources(bot);
    act.classList.remove("open");
    lblText(`${name} \u00b7 ${d.files || 0} files`, false);
    finish(); pollSov();
  });

  function finish(){
    es.close(); clearInterval(tick);
    elEl.textContent = ((Date.now() - t0) / 1000).toFixed(1) + "s";
    busy = false; sendEl.disabled = false;
    bot.querySelectorAll(".caret").forEach(c => c.remove());
    bot.querySelectorAll(".stp.run").forEach(r => {
      r.className = "stp done";
      const sh = r.querySelector(".nm .shimmer");
      if(sh) sh.replaceWith(sh.textContent);
    });
    qEl.focus();
  }
}



/* ==================== dictation ==================== */
/* The default Web Speech API streams audio to Google's servers. On this
   project that is disqualifying, and our own monitor would not even catch it,
   because the request comes from the browser and not from our process - the
   same blind spot that let subprocesses reach the network. Chrome 139+ can run
   recognition ON DEVICE, so we require that and refuse the microphone when it
   is unavailable, rather than quietly shipping a plant discussion to a cloud
   service.

   The button is never silently disabled. An earlier version greyed it out when
   the on-device check failed, so clicking Speak did nothing and said nothing -
   which looks identical to a broken button. It now always responds and names
   the exact reason it cannot listen. */

const micBtn = $("#micbtn"), micLabel = $("#miclabel");
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, recording = false;

async function micStatus(){
  if(!window.isSecureContext)
    return { ok:false, why:"The page is not a secure context, so the browser "
                        + "will not grant microphone access." };
  if(!SR)
    return { ok:false, why:"This browser has no speech recognition. Chrome 139 "
                        + "or newer is needed for on-device dictation." };
  if(typeof SR.available !== "function")
    return { ok:false, why:"This browser cannot run speech recognition on "
                        + "device, and the cloud fallback would send your audio "
                        + "to Google - so dictation stays off." };
  try{
    const state = await SR.available({ langs:["en-US"], processLocally:true });
    if(state === "available")   return { ok:true };
    if(state === "downloading") return { ok:false, why:"The on-device speech "
                                       + "model is still downloading." };
    if(state === "downloadable")
      return { ok:false, downloadable:true,
               why:"The on-device speech model is not installed yet." };
    return { ok:false, why:"On-device speech is unavailable here (" + state
                        + "), and the cloud fallback is not acceptable on an "
                        + "air-gapped workbench." };
  }catch(e){
    return { ok:false, why:"Could not check on-device speech: " + e.message };
  }
}

micBtn.onclick = async () => {
  if(recording){ rec?.stop(); return; }

  const st = await micStatus();
  if(!st.ok){
    toast(st.why, 7000);
    console.warn("[dictation]", st.why);
    if(st.downloadable){
      try{
        toast("Downloading the on-device speech model, once...", 9000);
        await SR.install({ langs:["en-US"], processLocally:true });
        toast("Installed - press Speak again.", 5000);
      }catch(e){ toast("Install failed: " + e.message, 6000); }
    }
    return;
  }

  rec = new SR();
  rec.lang = "en-US";
  rec.continuous = true;
  rec.interimResults = true;
  rec.processLocally = true;          // the whole point: never leaves the machine

  const before = qEl.value.trim();
  // resultIndex points at the first CHANGED result, not the first. Reading from
  // it gives only the newest fragment, so finalised text is kept separately -
  // otherwise every pause restarts the sentence.
  let finalText = "";

  rec.onstart = () => {
    recording = true;
    micBtn.classList.add("rec");
    micLabel.textContent = "Stop";
    toast("Listening on device - audio stays on this machine", 3000);
  };
  rec.onresult = ev => {
    let interim = "";
    for(let i = ev.resultIndex; i < ev.results.length; i++){
      const r = ev.results[i];
      if(r.isFinal) finalText += r[0].transcript;
      else interim += r[0].transcript;
    }
    qEl.value = (before ? before + " " : "") + finalText + interim;
    qEl.style.height = "auto";
    qEl.style.height = Math.min(qEl.scrollHeight, 200) + "px";
  };
  rec.onerror = ev => {
    const why = {
      "not-allowed": "Microphone permission was refused. Allow it for "
                   + "127.0.0.1 in the address bar, then press Speak again.",
      "service-not-allowed": "The browser blocked speech recognition.",
      "no-speech": "Nothing was heard.",
      "audio-capture": "No microphone was found.",
      "network": "Recognition tried to use the network and was stopped.",
    }[ev.error] || ("Dictation error: " + ev.error);
    toast(why, 6000);
    console.warn("[dictation]", ev.error);
  };
  rec.onend = () => {
    recording = false;
    micBtn.classList.remove("rec");
    micLabel.textContent = "Speak";
    qEl.focus();
  };

  try{ rec.start(); }
  catch(e){ toast("Could not start dictation: " + e.message, 5000); }
};

// Say what the microphone can do before it is pressed, in the tooltip only -
// the button stays clickable so the reason is always reachable.
micStatus().then(st => {
  micBtn.title = st.ok
    ? "Dictate - runs on device, audio never leaves this machine"
    : st.why;
});
