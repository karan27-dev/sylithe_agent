// Versioned by main.js's own URL: the browser refetches this module whenever
// main.js changes, so md.js can never go stale on its own.
import { render, esc } from "/static/md.js?v=2";

const $ = s => document.querySelector(s);
const feed = $("#feed"), qEl = $("#q"), sendEl = $("#send"), scroll = $("#scroll");
let chatId = null, busy = false;

/* Working context - uploads, the drawing in play, the chosen folder - is kept
   per chat on the server. Anything that adds to it has to say WHICH chat, so
   a file dropped here never surfaces in a different conversation. Uploading
   before the first message is normal, so a chat is created on demand rather
   than only when a question is sent. */
let chatPending = null;
async function needChat(){
  if(chatId) return chatId;
  // Dropping three files at once fired three of these in parallel, and each
  // one saw chatId still null - three chats created, the uploads split
  // between them, and the question asked in whichever won. One in-flight
  // request, awaited by everyone.
  if(!chatPending){
    chatPending = (async () => {
      const r = await (await fetch("/api/chats", {method:"POST"})).json();
      chatId = r.id;
      await loadChats();
      return chatId;
    })().finally(() => { chatPending = null; });
  }
  return chatPending;
}

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

// A path's last segment, on either slash - a folder chosen through the
// Windows native picker or the in-page browser arrives as "C:\Users\...",
// and splitting on "/" alone left the whole path on screen instead of just
// the folder name.
const baseName = p => (p || "").replace(/[\\/]+$/, "").split(/[\\/]/)
  .filter(Boolean).pop() || p;

function toast(msg, ms = 2800){
  const t = $("#toast"); t.textContent = msg; t.classList.add("on");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("on"), ms);
}

/* ---------- boot ---------- */
async function boot(){
  try{
    const b = await (await fetch("/api/boot?chat_id="
      + encodeURIComponent(chatId || ""))).json();
    $("#pill-model").innerHTML = b.health.up
      ? `<b>${b.health.lanes.reason}</b>`
      : `<b style="color:var(--bad)">engine offline</b>`;
    $("#pill-index").innerHTML = `<b>${b.index.chunks || 0}</b> chunks`;
    if(b.profiles) renderTiers(b.profiles);
    if(b.folder && !FOLDER){ FOLDER = b.folder; renderChips(); }
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

/* ---------- context chips ----------
   What the answer will be built from, next to where the question is typed:
   which engine, and which folder. Both were already in the product and both
   were somewhere else on the page - the tier in the header, the folder only
   in a toast that had long since gone. */
let FOLDER = null;

function renderChips(){
  const box = $("#chips");
  if(!box) return;
  const prof = (PROFILES || []).find(p => p.active);
  const engine = prof
    ? (prof.sovereign ? (prof.reach === "local" ? "Local" : "Plant LAN")
                      : "External")
    : "Local";
  const cls = prof && !prof.sovereign ? "chip warn" : "chip";
  box.innerHTML = `
    <span class="${cls}" title="${prof ? esc(prof.endpoint) : ""}">
      <svg viewBox="0 0 24 24"><rect x="2" y="4" width="20" height="13" rx="2"/>
        <path d="M2 20h20"/></svg>${engine}</span>
    ${FOLDER ? `<span class="chip" title="${esc(FOLDER)}">
      <svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
      ${esc(baseName(FOLDER))}
      <b data-drop title="Stop using this folder">\u00d7</b></span>` : ""}
    <button class="chip add" id="addfolder" title="Point at a folder on this machine">
      <svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>
        <path d="M12 11v4M10 13h4"/></svg></button>`;
  const drop = box.querySelector("[data-drop]");
  if(drop) drop.onclick = () => { FOLDER = null; renderChips(); };
  $("#addfolder").onclick = () => $("#folderbtn").click();
}

/* ---------- tier selector ----------
   Switching tier changes which engine answers, and the badge beside it says
   where that engine is. A LAN GPU node is inside the plant and the seal holds;
   a rented box on a public address is not, and the UI has to say so rather
   than let a green "0 external calls" imply something it cannot promise. */
function renderTiers(profiles){
  PROFILES = profiles;
  const box = $("#tiersel");
  box.innerHTML = profiles.map(p =>
    `<button data-tier="${esc(p.name)}" class="${p.active ? "on" : ""}"
       title="${esc(p.endpoint)}">${esc(p.name.replace(/^tier-/i, ""))}</button>`
  ).join("");
  box.querySelectorAll("button").forEach(b => {
    b.onclick = () => setTier(b.dataset.tier);
  });
  showReach(profiles.find(p => p.active));
  renderChips();
}

function showReach(p){
  const el = $("#pill-reach");
  if(!p){ el.textContent = "\u2014"; return; }
  if(p.sovereign){
    el.className = "reach ok";
    el.textContent = p.reach === "local" ? "on this machine" : "on the plant LAN";
    el.title = `Engine at ${p.endpoint} - inside the plant, the seal holds`;
  }else{
    el.className = "reach ext";
    el.textContent = "leaves this machine";
    el.title = `Engine at ${p.endpoint} is outside the plant. `
             + `Under seal these calls are refused.`;
  }
}

/* ---------- which models are actually in use ----------
   The header can only show one model name, and the product runs five at once
   on different lanes - which is the whole design, and was invisible. Clicking
   the pill lists every lane on every tier, so "what is this thing running" is
   one click rather than a YAML file. */
let PROFILES = [];

function renderModelCard(){
  const box = $("#modelcard");
  const LANE_NOTE = {
    router: "picks the lane", reason: "answers and judges",
    code:   "writes code that is then executed",
    vision: "reads scans, photos, handwriting",
    embed:  "indexes and searches documents",
  };
  box.innerHTML = `<h4>Models in use</h4>` + PROFILES.map(p => `
    <div class="tierblk">
      <div class="hdr"><b>${esc(p.name)}</b>
        <span class="tag ${p.sovereign ? "ok" : "ext"}">${
          p.sovereign ? (p.reach === "local" ? "this machine" : "plant LAN")
                      : "external"}</span>
        ${p.active ? '<span class="tag ok">active</span>' : ""}</div>
      ${Object.entries(p.lanes).map(([lane, model]) => `
        <div class="lane"><i>${esc(lane)}</i>
          <span>${esc(model)}<br><em>${esc(LANE_NOTE[lane] || "")}</em></span>
        </div>`).join("")}
    </div>`).join("");
}

$("#pill-model").onclick = e => {
  e.stopPropagation();
  const box = $("#modelcard");
  box.hidden = !box.hidden;
  if(!box.hidden) renderModelCard();
};
document.addEventListener("click", e => {
  const box = $("#modelcard");
  if(!box.hidden && !box.contains(e.target)) box.hidden = true;
});

async function setTier(name){
  try{
    const r = await (await fetch("/api/profile?name=" + encodeURIComponent(name),
                                 {method:"POST"})).json();
    if(!r.ok){ toast(r.error || "Could not switch tier"); return; }
    const all = await (await fetch("/api/profiles")).json();
    renderTiers(all.profiles);
    $("#pill-model").innerHTML = `<b>${esc(r.health.lanes.reason)}</b>`;
    if(!r.sovereign){
      toast(`${name}: engine is OUTSIDE this machine. `
            + (r.sealed ? "The seal will refuse these calls."
                        : "Data will leave the box."), 9000);
    }else{
      toast(`${name} \u00b7 ${esc(r.health.lanes.reason)}`);
    }
  }catch(e){ toast("Could not switch tier"); }
}

/* ---------- usage dashboard ----------
   The deployment is one workbench per engineer's PC, so the first question a
   plant IT manager asks after "is our data safe" is "who is using this, how
   much, and what does it cost". Each instance knows only its own traffic;
   a fleet view is these files gathered on a share, deliberately not a service
   every PC phones home to - that would be a network dependency in a product
   whose claim is that there is none.

   Sections are chosen to answer the questions actually asked: what did it
   cost, who is heavy, who is not using it at all, which lane is burning the
   budget, and is today unlike the other days. */
let USAGE_DAYS = 30;



const fmtUsd = n => n >= 1 ? "$" + n.toFixed(2)
                  : n > 0 ? "$" + n.toFixed(4) : "$0.00";

function uvRows(list, total, mine){
  if(!list.length) return `<tr><td colspan="5" class="uv-empty">Nothing yet.</td></tr>`;
  return list.map(r => {
    const tok = r.prompt_tokens + r.output_tokens;
    const pct = total ? Math.round(tok / total * 100) : 0;
    const me = r.key === mine ? " me" : "";
    return `<tr>
      <td class="${me.trim()}">${esc(r.key)}${me ? " (this machine)" : ""}
        <div class="uv-bar"><i style="width:${pct}%"></i></div></td>
      <td class="num">${fmtInt(r.calls)}</td>
      <td class="num">${fmtTok(tok)}</td>
      <td class="num">${pct}%</td>
      <td class="num">${fmtUsd(r.cost_usd)}</td>
    </tr>`;
  }).join("");
}

function uvTable(title, why, list, total, mine, unit){
  return `<div class="uv-sec"><h3>${esc(title)}</h3><p class="why">${why}</p>
    <table class="uv-tab"><thead><tr>
      <th>${esc(unit)}</th><th class="num">calls</th><th class="num">tokens</th>
      <th class="num">share</th><th class="num">cost</th>
    </tr></thead><tbody>${uvRows(list, total, mine)}</tbody></table></div>`;
}

async function showUsage(){
  const view = $("#usageview");
  view.hidden = false;
  view.innerHTML = `<div class="uv-sub">Reading usage\u2026</div>`;
  let d;
  try{
    d = await (await fetch("/api/usage?days=" + USAGE_DAYS)).json();
  }catch(e){ view.innerHTML = `<div class="uv-empty">Could not read usage.</div>`; return; }

  const t = d.total, tok = t.tokens || 0, cap = d.capacity || {};
  const peak = Math.max(1, ...d.daily.map(x => x.prompt_tokens + x.output_tokens));
  const hourPeak = Math.max(1, ...(cap.by_hour || []).map(h => h.seconds));
  const util = Math.round((cap.utilisation || 0) * 100);
  const hosted = d.tiers.filter(x => x.key !== "tier-S")
                        .reduce((a, x) => a + x.cost_usd, 0);
  const cur = n => n >= 1 ? n.toFixed(2) : n > 0 ? n.toFixed(4) : "0.00";

  view.innerHTML = `
    <div class="uv-head"><h2>Fleet usage and capacity</h2>
      <div class="uv-range">
        ${[1, 7, 30, 90].map(n =>
          `<button data-d="${n}" class="${n === USAGE_DAYS ? "on" : ""}">${
            n === 1 ? "today" : n + "d"}</button>`).join("")}
      </div>
    </div>
    <div class="uv-sub">${fmtInt(cap.machines_seen || 0)} PC(s) reporting
      \u00b7 reading <b>${esc(d.usage_dir || "this machine only")}</b>
      \u00b7 last ${d.days} day${d.days === 1 ? "" : "s"}</div>

    <div class="uv-cards">
      <div class="uv-card"><div class="k">node utilisation</div>
        <div class="v">${util}%</div>
        <div class="n">${(cap.engine_seconds / 3600).toFixed(1)}h of engine time
          over ${(cap.window_hours || 0).toFixed(1)}h</div></div>
      <div class="uv-card"><div class="k">busiest hour</div>
        <div class="v">${cap.busiest_hour == null ? "\u2014"
          : String(cap.busiest_hour).padStart(2, "0") + ":00"}</div>
        <div class="n">${(cap.busiest_hour_seconds / 60).toFixed(0)} engine-minutes</div></div>
      <div class="uv-card"><div class="k">requests</div>
        <div class="v">${fmtInt(t.calls)}</div>
        <div class="n">${fmtInt(t.tokens_per_call)} tokens each on average</div></div>
      <div class="uv-card"><div class="k">tokens</div>
        <div class="v">${fmtTok(tok)}</div>
        <div class="n">${fmtTok(t.prompt_tokens)} in \u00b7 ${fmtTok(t.output_tokens)} out</div></div>
      <div class="uv-card"><div class="k">cost to run</div>
        <div class="v">${cur(t.cost_usd)}</div>
        <div class="n">${hosted > 0
          ? cur(hosted) + " of it billed by a vendor"
          : "all on plant hardware"}</div></div>
    </div>

    <div class="uv-sec"><h3>When the node is busy</h3>
      <p class="why">Engine-seconds by hour of day. This is the number that
        decides whether one GPU node is enough - a flat day means headroom, a
        wall at 10:00 means people are queueing behind each other.</p>
      <div class="uv-spark">${(cap.by_hour || []).map(h =>
        `<i style="height:${Math.max(2, h.seconds / hourPeak * 100)}%"
           title="${String(h.hour).padStart(2, "0")}:00 \u00b7 ${
             (h.seconds / 60).toFixed(1)} engine-min \u00b7 ${h.calls} calls"></i>`
        ).join("") || '<span class="uv-empty">No traffic recorded yet.</span>'}</div>
    </div>

    <div class="uv-sec"><h3>Daily</h3>
      <p class="why">A day unlike the others is usually a misconfiguration or a
        script, not a busy engineer - which is the point of watching it.</p>
      <div class="uv-spark">${d.daily.map(x => {
        const v = x.prompt_tokens + x.output_tokens;
        return `<i style="height:${Math.max(2, v / peak * 100)}%"
          title="${esc(x.key)} \u00b7 ${fmtTok(v)} tokens \u00b7 ${cur(x.cost_usd)}"></i>`;
      }).join("") || '<span class="uv-empty">No days recorded yet.</span>'}</div>
    </div>

    ${uvTable("By PC",
      "One workbench per engineer's machine, so a row is a person's load on "
      + "the shared node. The lightest rows matter as much as the heaviest: a "
      + "PC at the bottom is a deployment nobody is using."
      + ((cap.machines_idle || []).length
          ? " Idle right now: " + cap.machines_idle.map(esc).join(", ") + "."
          : ""),
      d.machines, tok, d.this_machine, "machine")}

    ${uvTable("By person", "Who the OS says was signed in. Taken from the login "
      + "rather than asked for, so it cannot be typed wrong or borrowed.",
      d.users, tok, d.this_user, "user")}

    ${uvTable("By model", "What each model is doing to the node. Models with no "
      + "per-token price are charged for the seconds they occupied it - free at "
      + "the invoice, not free at the wall.", d.models, tok, null, "model")}

    ${uvTable("By lane", "Which job is eating the node. A router burning engine "
      + "time means a large model is doing a small model's work.",
      d.lanes, tok, null, "lane")}

    ${uvTable("On-premise against hosted", "The whole argument, in one table.",
      d.tiers, tok, null, "tier")}

    <div class="uv-sec"><h3>How cost is worked out</h3>
      <p class="why">There is no vendor invoice for a model the plant hosts, so
        cost is derived: a node costing ${fmtInt(d.onprem?.node_cost || 0)} over
        ${d.onprem?.life_years || 0} years, drawing ${d.onprem?.draw_watts || 0} W
        at ${d.onprem?.power_per_kwh || 0} per kWh, is about
        <b>${cur((d.onprem_rate || 0) * 3600)} per engine-hour</b>. Change those
        four numbers in models.yaml and every figure above moves with them.</p>
    </div>
  `;

  view.querySelectorAll(".uv-range button").forEach(b => {
    b.onclick = () => { USAGE_DAYS = +b.dataset.d; showUsage(); };
  });
}

$("#usage").onclick = () => {
  const v = $("#usageview");
  if(v.hidden) showUsage(); else v.hidden = true;
};

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
  feed.classList.remove("home");
  for(const m of c.messages || []){
    if(m.role === "user") addUser(m.content);
    else addBotStatic(m);
  }
  await loadChats();
  scroll.scrollTop = scroll.scrollHeight;
}

const fmtInt = n => (n || 0).toLocaleString();
const fmtTok = n => n >= 1e6 ? (n / 1e6).toFixed(2) + "M"
                  : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n || 0);

/* ---------- home ----------
   The old home was a title, a sentence, and four example questions. The
   examples were the wrong thing to put in front of someone on their second
   day: by then they know what to ask, and the cards just pushed the composer
   down the page. What is actually useful on opening is a greeting and a
   straight answer to "what has this thing been doing" - the same panel the
   fleet admin shows, scoped to this machine. */

let HOME_TAB = "overview", HOME_DAYS = 30, HOME = null;

/* Whoever the OS says is signed in, unless they have told us otherwise. Taken
   from the login rather than asked for on first run: a name box on a blank
   screen is a chore, and the machine already knows. */
const WHO = () => {
  const set = (localStorage.getItem("sy-name") || "").trim();
  if(set) return set;
  const u = (HOME && HOME.this_user) || "";
  if(!u || u === "unknown") return "";
  return u.split(/[.\-_ ]/).filter(Boolean)
          .map(w => w[0].toUpperCase() + w.slice(1)).join(" ");
};

function partOfDay(){
  const h = new Date().getHours();
  return h < 5 ? "Still up" : h < 12 ? "Good morning"
       : h < 17 ? "Good afternoon" : "Good evening";
}

function greeting(){
  const who = WHO();
  return who ? `${partOfDay()}, ${esc(who)}` : "What are we looking at today?";
}

function heat(daily, days){
  /* A cell per day, oldest first, in week columns. Squares rather than a line
     because the question it answers is "how often", not "how much" - and a
     gap in a grid is easier to see than a dip in a chart. */
  const byDay = new Map(daily.map(d => [d.key, d.prompt_tokens + d.output_tokens]));
  const top = Math.max(1, ...byDay.values());
  const cells = [];
  const start = new Date(); start.setDate(start.getDate() - (days - 1));
  for(let i = 0; i < days; i++){
    const d = new Date(start); d.setDate(start.getDate() + i);
    const key = d.toISOString().slice(0, 10);
    const v = byDay.get(key) || 0;
    const lvl = v === 0 ? 0 : v > top * 0.66 ? 3 : v > top * 0.33 ? 2 : 1;
    cells.push(`<i class="l${lvl}" title="${key} \u00b7 ${
      v ? fmtTok(v) + " tokens" : "nothing"}"></i>`);
  }
  return `<div class="heat">` + cells.join("") + `</div>`;
}

// A round top and an even step, so the grid lines read as a scale rather
// than wherever the tallest bar happened to land.
function niceStep(rough){
  const mag = Math.pow(10, Math.floor(Math.log10(rough || 1)));
  const norm = (rough || 1) / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}
function fmtAxis(v){
  if(!v) return "0";
  if(v >= 1e6) return (Math.round(v / 1e5) / 10).toFixed(1).replace(/\.0$/, "") + "M";
  if(v >= 1e3) return Math.round(v / 1e3) + "k";
  return String(Math.round(v));
}

const MODEL_BLUES = ["#5b7fe0", "#8aa3ec", "#3f5cc4", "#b9c6f2", "#7093e6"];

function modelsTab(h){
  const rows = h.models;
  const total = h.total.tokens || 1;
  const days = [...new Set(h.daily_by_model.map(r => r.day))].sort();
  const colour = {};
  rows.forEach((r, i) => { colour[r.key] = MODEL_BLUES[i % MODEL_BLUES.length]; });
  const dayTotal = d => h.daily_by_model
    .filter(r => r.day === d).reduce((a, r) => a + r.tokens, 0);
  const peak = Math.max(1, ...days.map(dayTotal));

  const step = niceStep(peak / 5);
  const top = Math.ceil(peak / step) * step;
  const ticks = [];
  for(let v = top; v >= 0; v -= step) ticks.push(v);

  // A handful of evenly spaced dates under the axis, not one per bar -
  // thirty-odd date labels in the same width would just collide.
  const xcount = Math.min(days.length, 7);
  const xidx = new Set(Array.from({length: xcount}, (_, i) =>
    Math.round(i * (days.length - 1) / Math.max(1, xcount - 1))));

  return `
    <div class="mwrap">
      <div class="myaxis">${ticks.map(t => `<span>${fmtAxis(t)}</span>`).join("")}</div>
      <div class="mplot">
        <div class="mchart">${days.map((d, i) => {
          const parts = h.daily_by_model.filter(r => r.day === d);
          const hgt = dayTotal(d) / top * 100;
          return `<span class="bcol" title="${d} \u00b7 ${fmtTok(dayTotal(d))} tokens">
            <span class="stack" style="height:${Math.max(1.5, hgt)}%">${
              parts.map(pr => `<i style="flex:${pr.tokens};background:${
                colour[pr.model] || "var(--line2)"}"></i>`).join("")}</span></span>`;
        }).join("")}</div>
        <div class="mxaxis">${days.map((d, i) => `<span>${
          xidx.has(i)
            ? new Date(d + "T00:00:00").toLocaleDateString(undefined,
                {month: "short", day: "numeric"})
            : ""}</span>`).join("")}</div>
      </div>
    </div>
    <div class="mlegend">${rows.map(r => `
      <div class="lrow">
        <span class="dot" style="background:${colour[r.key]}"></span>
        <span class="nm">${esc(r.key)}</span>
        <span class="io">${fmtTok(r.prompt_tokens)} in \u00b7 ${
          fmtTok(r.output_tokens)} out</span>
        <span class="pc">${((r.prompt_tokens + r.output_tokens)
          / total * 100).toFixed(1)}%</span>
      </div>`).join("")}</div>`;
}

function overviewTab(h){
  const t = h.total;
  const hr = h.peak_hour;
  const cells = [
    ["Requests", fmtInt(t.calls)],
    ["Tokens", fmtTok(t.tokens)],
    ["Engine time", (t.seconds / 60).toFixed(0) + "m"],
    ["Active days", fmtInt(h.active_days)],
    ["Peak hour", hr == null ? "\u2013"
      : (hr % 12 || 12) + (hr < 12 ? " AM" : " PM")],
    ["Most used", h.top_model || "\u2013"],
  ];
  return `
    <div class="hcells">${cells.map(([k, v]) =>
      `<div class="hcell"><span class="k">${k}</span><b>${v}</b></div>`).join("")}</div>
    ${heat(h.daily, h.days)}
    <div class="hnote">${t.calls
      ? `${fmtInt(Math.round(t.tokens / Math.max(1, t.calls)))} tokens per request
         on average, all of it on this machine.`
      : "Nothing run on this machine yet."}</div>`;
}

function renderHome(){
  const box = feed.querySelector(".usage");
  if(!box || !HOME) return;
  box.querySelectorAll("[data-tab]").forEach(b =>
    b.classList.toggle("on", b.dataset.tab === HOME_TAB));
  box.querySelectorAll("[data-hd]").forEach(b =>
    b.classList.toggle("on", +b.dataset.hd === HOME_DAYS));
  box.querySelector(".ubody").innerHTML =
    HOME_TAB === "models" ? modelsTab(HOME) : overviewTab(HOME);
  // The name arrives with the usage payload, after the heading was first
  // painted. Rewriting it here beats holding the whole screen back for it.
  const h1 = feed.querySelector(".hero h1");
  if(h1) h1.innerHTML = `<img class="brand-mark" src="/static/sylithe-logo.png" alt="">${greeting()}`;
}

async function hero(){
  feed.classList.add("home");          // escape the centred conversation column
  feed.innerHTML = `
    <div class="hero">
      <h1><img class="brand-mark" src="/static/sylithe-logo.png" alt="">${greeting()}</h1>
      <div class="usage">
        <div class="uhead">
          <div class="tabs">
            <button data-tab="overview" class="on">Overview</button>
            <button data-tab="models">Models</button>
          </div>
          <div class="days">
            <button data-hd="7">7d</button>
            <button data-hd="30" class="on">30d</button>
            <button data-hd="90">90d</button>
          </div>
        </div>
        <div class="ubody"><div class="uskel">
          <div class="hcells">${Array(6).fill(
            `<div class="hcell"><span class="k"> </span><b> </b></div>`
          ).join("")}</div>
          <div class="heat">${Array(35).fill("<i></i>").join("")}</div>
        </div></div>
      </div>
    </div>`;

  const box = feed.querySelector(".usage");
  box.querySelectorAll("[data-tab]").forEach(b =>
    b.onclick = () => { HOME_TAB = b.dataset.tab; renderHome(); });
  box.querySelectorAll("[data-hd]").forEach(b =>
    b.onclick = async () => { HOME_DAYS = +b.dataset.hd; await loadHome(); });
  await loadHome();
}

async function loadHome(){
  try{
    HOME = await (await fetch("/api/usage?days=" + HOME_DAYS)).json();
  }catch(e){ HOME = null; }
  if(!HOME){
    const b = feed.querySelector(".ubody");
    if(b) b.innerHTML = `<div class="hnote">Usage is not available.</div>`;
    return;
  }
  renderHome();
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
  await needChat();
  if(feed.querySelector(".hero")) feed.innerHTML = "";
  feed.classList.remove("home");
  feed.classList.remove("home");

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
      const r = await (await fetch("/api/upload?chat_id="
        + encodeURIComponent(await needChat()),
        { method:"POST", body: fd })).json();
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
    await needChat();
    const r = await (await fetch("/api/folder/choose", {method:"POST"})).json();
    if(r.path){ await attachFolder(r.path); return; }
    if(r.cancelled) return;                 // said no - do nothing at all
    // No native dialog on this OS (or it failed) - fall back to the browser
    // built from the same directory listing the picker uses, instead of just
    // telling the user to type a path they may not know by heart.
    openFolderBrowser();
  }catch(e){
    toast("Could not open the folder chooser: " + e.message, 5000);
  }finally{
    folderBtn.disabled = false;
    folderBtn.classList.remove("on");
  }
};

/* ---- fallback folder browser ----
   Windows and Linux have no `osascript`, so /api/folder/choose always
   reports the native dialog is unavailable there - which used to just be a
   toast telling the user to type a path from memory. The backend already
   had folder.places()/listdir() for a browser-safe path picker; nothing
   used them. This wires them into an actual modal. */
let FB_PATH = null, FB_PLACES = null;

async function openFolderBrowser(startPath){
  $("#foldermodal").classList.add("on");
  if(!FB_PLACES){
    try{ FB_PLACES = (await (await fetch("/api/folder/places")).json()).places || [];
    }catch(e){ FB_PLACES = []; }
    $("#fplaces").innerHTML = FB_PLACES.map(p =>
      `<button data-p="${esc(p.path)}">${esc(p.label)}</button>`).join("");
    $("#fplaces").querySelectorAll("[data-p]").forEach(b =>
      b.onclick = () => fbList(b.dataset.p));
  }
  fbList(startPath || FB_PLACES[0]?.path || "~");
}

async function fbList(path){
  $("#fpathinput").value = "";
  $("#fdirs").innerHTML = `<div class="dirempty">Loading…</div>`;
  let d;
  try{ d = await (await fetch("/api/folder/list?path=" + encodeURIComponent(path))).json();
  }catch(e){ d = { error: e.message }; }
  if(d.error){
    $("#fdirs").innerHTML = `<div class="dirempty">${esc(d.error)}</div>`;
    $("#fusehere").disabled = true;
    return;
  }
  FB_PATH = d.path;
  $("#fcrumbs").innerHTML = d.crumbs.map((c, i) => `${i ? '<span class="sep">/</span>' : ""}
    <button data-p="${esc(c.path)}">${esc(c.name)}</button>`).join("");
  $("#fcrumbs").querySelectorAll("[data-p]").forEach(b =>
    b.onclick = () => fbList(b.dataset.p));
  $("#fdirs").innerHTML = d.dirs.length
    ? d.dirs.map(dd => `<button class="dirrow" data-p="${esc(dd.path)}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
        </svg>
        <span>${esc(dd.name)}</span>
        <span class="docs">${dd.docs < 0 ? "no access"
          : dd.docs ? dd.docs + " doc" + (dd.docs === 1 ? "" : "s") : ""}</span>
      </button>`).join("")
    : `<div class="dirempty">No subfolders here.</div>`;
  $("#fdirs").querySelectorAll("[data-p]").forEach(b =>
    b.onclick = () => fbList(b.dataset.p));
  $("#fpicked").textContent = d.supported
    ? `${d.supported} readable document${d.supported === 1 ? "" : "s"} directly in this folder`
    : "No readable documents directly in this folder — open a subfolder, or use it anyway";
  $("#fusehere").disabled = false;
}

$("#fpathgo").onclick = () => { if($("#fpathinput").value.trim()) fbList($("#fpathinput").value.trim()); };
$("#fpathinput").addEventListener("keydown", e => {
  if(e.key === "Enter" && $("#fpathinput").value.trim()) fbList($("#fpathinput").value.trim());
});
$("#fusehere").onclick = async () => {
  if(!FB_PATH) return;
  $("#foldermodal").classList.remove("on");
  await attachFolder(FB_PATH);
};
$("#fmodalx").onclick = () => $("#foldermodal").classList.remove("on");
$("#foldermodal").onclick = e => {
  if(e.target.id === "foldermodal") $("#foldermodal").classList.remove("on");
};
document.addEventListener("keydown", e => {
  if(e.key === "Escape") $("#foldermodal").classList.remove("on");
});

/* Choosing a folder ATTACHES it. It does not ask a question.
   Selecting one used to post "Analyse the folder X" into the chat and stream a
   summary nobody had asked for, so the first thing in every conversation was a
   message the user did not write, and being rid of it meant starting a new
   chat. A folder is context, like an attachment: it goes on the chip row and
   waits to be used. */
async function attachFolder(path){
  FOLDER = path;
  renderChips();
  const name = baseName(path);
  toast(`Reading ${name}\u2026`);
  const cid = await needChat();
  // A folder can be a few thousand files now (was capped at 400 - a real
  // "Documents" folder cleared that easily), and docling takes real seconds
  // per file. One static "Reading..." toast for the whole run reads as a
  // hang on anything past a handful of files, so poll the same progress the
  // backend already tracked internally and keep the toast alive with it.
  const poll = setInterval(async () => {
    try{
      const p = await (await fetch(
        `/api/folder/progress?chat_id=${encodeURIComponent(cid)}`)).json();
      if(p.total) toast(`Reading ${name}: ${p.current}/${p.total} \u2014 ${
        baseName(p.name)}`, 4000);
    }catch(e){}
  }, 900);
  try{
    const r = await (await fetch(
      `/api/folder/ingest?path=${encodeURIComponent(path)}`
      + `&chat_id=${encodeURIComponent(cid)}`,
      { method: "POST" })).json();
    if(r.error){ toast(r.error, 7000); FOLDER = null; renderChips(); return; }
    const n = (r.files || []).length || r.indexed || 0;
    toast(`${name}: ${n} file${n === 1 ? "" : "s"} ready. Ask anything about them.`,
          5000);
    boot();
  }catch(e){
    toast("Could not read that folder: " + e.message, 6000);
    FOLDER = null; renderChips();
  }finally{
    clearInterval(poll);
  }
}

/* ==================== folder analysis ==================== */

function analyseFolder(path){
  if(busy) { toast("Still working on the last question", 3000); return; }
  busy = true; sendEl.disabled = true;
  if(feed.querySelector(".hero")) feed.innerHTML = "";
  feed.classList.remove("home");

  const name = baseName(path);
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

  const es = new EventSource("/api/folder/analyse?path="
    + encodeURIComponent(path) + "&chat_id=" + encodeURIComponent(chatId || ""));
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
