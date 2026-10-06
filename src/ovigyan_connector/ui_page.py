"""The connector's one page. Plain HTML, CSS and JavaScript with no external files or libraries, so it works
offline on a school PC and ships inside the executable. Text is always inserted with textContent."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ovigyan Connector</title>
<style nonce="__NONCE__">
:root{--bg:#f6f7f9;--card:#fff;--text:#16181d;--muted:#5c6470;--line:#e1e4ea;--accent:#1f6feb;--accent-text:#fff;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--chip:#eef0f4}
@media (prefers-color-scheme:dark){:root{--bg:#0e1116;--card:#171b22;--text:#e6e8ec;--muted:#9aa3b0;--line:#2a303a;--accent:#4c8dff;--accent-text:#06101f;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--chip:#222833}}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 48px}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}
h1{font-size:1.25rem;margin:0}
h2{font-size:1.05rem;margin:0 0 4px}
p{margin:0 0 12px}
.muted{color:var(--muted);font-size:.9rem}
.pill{display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;background:var(--chip);font-size:.85rem;font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;background:var(--muted)}
.pill.ok .dot{background:var(--ok)}.pill.warn .dot{background:var(--warn)}.pill.bad .dot{background:var(--bad)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:16px}
label{display:block;font-weight:600;margin:14px 0 6px}
input{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--text);font:inherit}
input:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
button{font:inherit;font-weight:600;padding:11px 18px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--text);cursor:pointer}
button.primary{background:var(--accent);color:var(--accent-text);border-color:var(--accent)}
button.link{border:0;background:none;color:var(--muted);padding:4px 0;font-weight:500;text-decoration:underline}
button:disabled{opacity:.6;cursor:default}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media (max-width:520px){.grid{grid-template-columns:1fr}}
.device{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:14px 0;border-top:1px solid var(--line)}
.device:first-child{border-top:0;padding-top:0}
.device strong{display:block}
.chip{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.8rem;font-weight:600;background:var(--chip)}
.chip.ok{color:var(--ok)}.chip.warn{color:var(--warn)}.chip.bad{color:var(--bad)}
.msg{padding:10px 12px;border-radius:10px;margin-top:12px;font-size:.95rem}
.msg.error{background:color-mix(in srgb,var(--bad) 14%,transparent);color:var(--bad)}
.msg.good{background:color-mix(in srgb,var(--ok) 14%,transparent);color:var(--ok)}
.msg.warn{background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn)}
details{margin-top:12px}summary{cursor:pointer;color:var(--muted)}
footer{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:8px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.tight{margin:0}
.hint{margin-top:8px}
#banner:not(:empty),#notice:not(:empty){margin-bottom:16px}
#banner .msg,#notice .msg{margin-top:0}
#devices{margin-top:4px}
.brand{display:flex;align-items:center;gap:12px}
#logo{width:36px;height:36px;object-fit:contain;border-radius:8px}
</style>
</head>
<body>
<main>
  <header><div class="brand"><img id="logo" alt="" hidden><div><h1 id="title">Ovigyan Connector</h1><div id="sub" class="muted tight" hidden>Attendance connector</div></div></div><span id="pill" class="pill"><span class="dot"></span><span id="pillText">Checking…</span></span></header>
  <div id="banner"></div>
  <div id="notice" role="status"></div>
  <section id="setup" class="card" hidden>
    <h2>Connect to your Ovigyan site</h2>
    <p class="muted">In Ovigyan open <strong>Settings → Connectors</strong>, press <strong>Generate key</strong>, then enter the details here.</p>
    <form id="pairForm" autocomplete="off">
      <label for="server">Web address of your Ovigyan site</label>
      <input id="server" name="server" placeholder="school.example.com" inputmode="url" required>
      <label for="key">Connection key</label>
      <input id="key" name="key" placeholder="OVG-XXXXX-XXXXX-XXXXX-XXXXX" autocapitalize="characters" spellcheck="false" required>
      <div class="row"><button class="primary" id="pairBtn" type="submit">Connect</button></div>
      <div id="pairMsg" role="status"></div>
    </form>
  </section>
  <section id="devicesCard" class="card" hidden>
    <div class="head"><div><h2>Attendance terminals</h2><p class="muted tight">Add each terminal on this network. An administrator approves it in <span class="prod">Ovigyan</span>.</p></div><button class="primary" id="addToggle" type="button">Add a terminal</button></div>
    <form id="addForm" hidden autocomplete="off">
      <label for="host">Terminal IP address</label>
      <input id="host" name="host" placeholder="192.168.1.50" required>
      <details><summary>More options</summary>
        <div class="grid"><div><label for="port">Port</label><input id="port" name="port" value="4370" inputmode="numeric"></div>
        <div><label for="password">Communication password</label><input id="password" name="password" value="0" inputmode="numeric"></div></div>
        <p class="muted hint">Most terminals use port 4370 and no password (0).</p></details>
      <div class="row"><button class="primary" id="addBtn" type="submit">Check and add</button><button type="button" id="addCancel">Cancel</button></div>
      <div id="addMsg" role="status"></div>
    </form>
    <div id="devices"></div>
    <p id="empty" class="muted" hidden>No terminals yet. Press “Add a terminal” to start.</p>
  </section>
  <footer id="footer" class="muted" hidden><span id="conn"></span><button class="link" id="unpair" type="button">Disconnect</button></footer>
</main>
<script nonce="__NONCE__">
const $ = (id) => document.getElementById(id);
const WORDS = {ok:["Working","ok"],pending:["Waiting for approval","warn"],unreachable:["Can’t reach terminal","bad"],rejected:["Declined","bad"],disabled:["Switched off","warn"],unknown:["Checking…",""]};
async function api(path, method = "GET", body) {
  const res = await fetch(path, {method, headers: body ? {"content-type": "application/json"} : {}, body: body ? JSON.stringify(body) : undefined});
  let data = {}; try { data = await res.json(); } catch (e) {}
  if (!res.ok) { const err = new Error(data.error || "Something went wrong."); err.status = res.status; throw err; }
  return data;
}
function msg(el, text, kind) { el.replaceChildren(); if (!text) return; const d = document.createElement("div"); d.className = "msg " + kind; d.textContent = text; el.append(d); }
function show(el, on) { el.hidden = !on; }
function pill(text, kind) { $("pillText").textContent = text; $("pill").className = "pill " + kind; }
let adding = false;
let product = "Ovigyan";
function brand(b) {
  b = b || {};
  const name = b.name || "Ovigyan Connector";
  product = b.name || "Ovigyan";
  for (const el of document.querySelectorAll(".prod")) el.textContent = product;
  $("title").textContent = name; document.title = name; show($("sub"), !!b.name);
  const root = document.documentElement.style;
  if (b.color) { root.setProperty("--accent", b.color); root.setProperty("--accent-text", b.text); }
  else { root.removeProperty("--accent"); root.removeProperty("--accent-text"); }
  const logo = $("logo");
  if (b.logo) { const src = "/logo.png?v=" + b.logo; if (logo.getAttribute("src") !== src) logo.setAttribute("src", src); }
  show(logo, !!b.logo);
}
function render(s) {
  brand(s.brand);
  const last = s.lastCycle || {};
  const byId = Object.fromEntries((last.devices || []).map((d) => [d.id, d]));
  const connected = s.paired;
  show($("setup"), !connected); show($("devicesCard"), connected); show($("footer"), connected);
  $("banner").replaceChildren();
  if (s.status === "revoked") msg($("banner"), "Your school’s Ovigyan site no longer accepts this connector. Connect again with a new key.", "warn");
  if (!connected) { pill(s.status === "revoked" ? "Disconnected" : "Not connected", s.status === "revoked" ? "bad" : "warn"); return; }
  if (last.connector === "offline") pill("Offline — will retry", "warn");
  else if (last.connector === "ok") pill("Connected", "ok");
  else pill("Connecting…", "");
  $("conn").textContent = "Connected to " + s.server + " as “" + s.connectorName + "” · version " + s.version;
  const list = $("devices"); list.replaceChildren();
  show($("empty"), s.devices.length === 0 && !adding);
  for (const d of s.devices) {
    const info = byId[d.id] || {state: "unknown", message: "Checking…"};
    const [word, kind] = WORDS[info.state] || WORDS.unknown;
    const row = document.createElement("div"); row.className = "device";
    const left = document.createElement("div");
    const name = document.createElement("strong"); name.textContent = (d.model || "Terminal") + " · " + (d.serial || "unidentified");
    const where = document.createElement("span"); where.className = "muted"; where.textContent = d.host + ":" + d.port;
    const chip = document.createElement("span"); chip.className = "chip " + kind; chip.textContent = word;
    const note = document.createElement("div"); note.className = "muted"; note.textContent = info.message + (info.queued ? " (" + info.queued + " waiting to send)" : "");
    left.append(name, where, document.createElement("br"), chip, note);
    const rm = document.createElement("button"); rm.className = "link"; rm.type = "button"; rm.textContent = "Remove";
    rm.addEventListener("click", async () => { if (!confirm("Remove this terminal from this PC?")) return; try { await api("/api/devices/" + encodeURIComponent(d.id), "DELETE"); refresh(); } catch (e) { alert(e.message); } });
    row.append(left, rm); list.append(row);
  }
}
async function refresh() { try { render(await api("/api/status")); } catch (e) { pill("Connector not running", "bad"); } }
$("pairForm").addEventListener("submit", async (ev) => {
  ev.preventDefault(); $("pairBtn").disabled = true; msg($("pairMsg"), "Connecting…", "good");
  try { await api("/api/pair", "POST", {server: $("server").value, key: $("key").value}); $("key").value = ""; msg($("pairMsg"), "", ""); await refresh(); }
  catch (e) { msg($("pairMsg"), e.message, "error"); } finally { $("pairBtn").disabled = false; }
});
function toggleAdd(on) { adding = on; show($("addForm"), on); $("addToggle").hidden = on; msg($("addMsg"), "", ""); if (on) $("host").focus(); refresh(); }
$("addToggle").addEventListener("click", () => toggleAdd(true));
$("addCancel").addEventListener("click", () => toggleAdd(false));
$("addForm").addEventListener("submit", async (ev) => {
  ev.preventDefault(); $("addBtn").disabled = true; msg($("addMsg"), "Contacting the terminal…", "good");
  try {
    const r = await api("/api/devices", "POST", {host: $("host").value, port: $("port").value, password: $("password").value});
    $("host").value = ""; toggleAdd(false);
    msg($("notice"), (r.existing ? "Updated " : "Added ") + (r.device.model || "terminal") + " " + r.device.serial + ". An administrator now approves it in " + product + " under Settings → Connectors.", "good");
    await refresh();
  } catch (e) { msg($("addMsg"), e.message, "error"); } finally { $("addBtn").disabled = false; }
});
$("unpair").addEventListener("click", async () => { if (!confirm("Disconnect this PC from Ovigyan? Terminals stay in the list.")) return; try { await api("/api/unpair", "POST", {}); refresh(); } catch (e) { alert(e.message); } });
refresh(); setInterval(refresh, 3000);
// While a terminal is waiting for approval (or not checked yet), nudge the connector so approval shows within seconds.
setInterval(async () => { if (document.hidden) return; try { const s = await api("/api/status"); const last = (s.lastCycle || {}).devices || []; if (s.paired && last.some((d) => d.state === "pending" || d.state === "unknown")) await api("/api/refresh", "POST", {}); } catch (e) {} }, 15000);
</script>
</body>
</html>
"""
