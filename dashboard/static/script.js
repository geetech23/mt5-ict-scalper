const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => (n === null || n === undefined || n === "" ? "–" : Number(n).toFixed(d));
const cls = (n) => (Number(n) > 0 ? "ok" : Number(n) < 0 ? "bad" : "");
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let countdownEnd = 0;

function hms(s) {
  s = Math.max(0, Math.floor(s));
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map((x) => String(x).padStart(2, "0")).join(":");
}

function render(d) {
  const c = $("conn");
  c.textContent = d.connected ? "✅ Connected" : "❌ Disconnected";
  c.className = "badge " + (d.connected ? "ok" : "bad");
  $("mode").textContent = (d.simulation ? "🧪 SIM · " : "") + d.broker;
  $("session").textContent = d.session.current === "NONE" ? "⚠️ No active session" : d.session.current;
  countdownEnd = Date.now() + d.session.seconds_to_change * 1000;
  $("session-hours").textContent = Object.entries(d.session.hours).map(([k, v]) => `${k} ${v[0]}:00-${v[1]}:00`).join(" · ") + " UTC";
  $("balance").textContent = fmt(d.account.balance);
  $("equity").textContent = fmt(d.account.equity);
  $("risk").textContent = d.risk.trading_allowed ? `Daily P&L ${d.risk.daily_pnl} · losses in a row ${d.risk.consecutive_losses}` : `⚠️ Trading halted: ${d.risk.reason}`;
  $("wins").textContent = d.stats.wins;
  $("losses").textContent = d.stats.losses;
  $("winrate").textContent = d.stats.win_rate;
  $("net").textContent = d.stats.net_pnl;
  $("net").className = cls(d.stats.net_pnl);

  const sigs = Object.entries(d.signals).map(([sym, s]) => {
    const z = d.zones[sym];
    const trend = z ? ` <span class="muted">trend: ${esc(z.trend)}${z.last_break ? " · " + esc(z.last_break) : ""}</span>` : "";
    if (!s) return `<div class="sig"><b>${esc(sym)}</b>${trend}<div class="muted">No signal</div></div>`;
    return `<div class="sig"><b>${esc(sym)}</b> <span class="${s.side === "buy" ? "ok" : "bad"}">${esc(s.side.toUpperCase())}</span> @ ${s.entry} SL ${s.sl} TP ${s.tp} (RR ${s.rr})${trend}<div class="muted">${s.reasons.map(esc).join(" · ")}</div></div>`;
  });
  $("signals").innerHTML = sigs.join("") || '<span class="muted">Waiting for an active session…</span>';

  $("zones").textContent = Object.entries(d.zones).map(([sym, z]) =>
    `${sym} [${z.structure.join(" ")}]\n` +
    z.order_blocks.map((o) => `  OB  ${o.dir.padEnd(7)} ${o.low}-${o.high}`).join("\n") + "\n" +
    z.fvgs.map((g) => `  FVG ${g.dir.padEnd(7)} ${g.bottom}-${g.top}`).join("\n")).join("\n") || "–";

  $("positions").querySelector("tbody").innerHTML = d.positions.map((p) =>
    `<tr><td>${esc(p.symbol)}</td><td>${esc(p.side)}</td><td>${p.volume}</td><td>${p.entry}</td><td>${p.sl}</td><td>${p.tp}</td><td class="${cls(p.profit)}">${fmt(p.profit)}</td></tr>`).join("")
    || '<tr><td colspan="7" class="muted">No open positions</td></tr>';
  $("journal").querySelector("tbody").innerHTML = d.journal.map((r) =>
    `<tr><td>${esc(r.time)}</td><td>${esc(r.symbol)}</td><td>${esc(r.side)}</td><td>${esc(r.entry)}</td><td>${esc(r.exit)}</td><td class="${cls(r.pnl)}">${esc(r.pnl)}</td><td>${esc(r.reason)}</td></tr>`).join("")
    || '<tr><td colspan="7" class="muted">No trades yet</td></tr>';
}

async function poll() {
  try {
    const r = await fetch("/api/state");
    render(await r.json());
  } catch (e) {
    $("conn").textContent = "❌ Dashboard offline";
    $("conn").className = "badge bad";
  }
}

setInterval(() => {
  if (countdownEnd) $("countdown").textContent = "Next change in " + hms((countdownEnd - Date.now()) / 1000);
}, 1000);
poll();
setInterval(poll, 3000);
