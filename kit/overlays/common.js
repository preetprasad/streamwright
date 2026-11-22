// Shared by all overlay pages. DATA is injected by the server (links, ticker, handle). Chat text only ever goes through textContent.
const D = JSON.parse(document.getElementById('d').textContent);
// Static trusted SVG strings (simplified marks), never user text.
const IC = {
 youtube: '<svg viewBox="0 0 24 24"><rect x="1" y="5" width="22" height="14" rx="4" fill="#f00"/><path d="M10 9l5 3-5 3z" fill="#fff"/></svg>',
 twitch: '<svg viewBox="0 0 24 24"><path d="M4 2h17v12l-5 5h-4l-3 3H6v-3H2V5z" fill="#9146ff"/><path d="M11 7h2v5h-2zM16 7h2v5h-2z" fill="#fff"/></svg>',
 tiktok: '<svg viewBox="0 0 24 24"><path id="n" d="M16 3c.3 2.4 1.8 4 4 4.2v3a7 7 0 0 1-4-1.3v6.1a5.5 5.5 0 1 1-5.5-5.5v3.1a2.4 2.4 0 1 0 2.4 2.4V3z" fill="#25f4ee" transform="translate(-1 0)"/><path d="M16 3c.3 2.4 1.8 4 4 4.2v3a7 7 0 0 1-4-1.3v6.1a5.5 5.5 0 1 1-5.5-5.5v3.1a2.4 2.4 0 1 0 2.4 2.4V3z" fill="#fe2c55" transform="translate(1 0)"/><path d="M16 3c.3 2.4 1.8 4 4 4.2v3a7 7 0 0 1-4-1.3v6.1a5.5 5.5 0 1 1-5.5-5.5v3.1a2.4 2.4 0 1 0 2.4 2.4V3z" fill="#fff"/></svg>',
 instagram: '<svg viewBox="0 0 24 24" fill="none" stroke="#FF7096" stroke-width="2.2"><rect x="3" y="3" width="18" height="18" rx="5.5"/><circle cx="12" cy="12" r="4.2"/><circle cx="17.3" cy="6.7" r=".8" fill="#FF7096"/></svg>',
 discord: '<svg viewBox="0 0 24 24"><path d="M5 5c2-1 4-1.5 7-1.5S17 4 19 5c1.5 3 2.5 6 2.5 10-1.5 1.5-3 2.3-5 2.8l-1-2c.7-.2 1.200-.5 1.700-.9-3 1.300-8 1.300-11 0 .5.4 1 .7 1.700.9l-1 2c-2-.5-3.500-1.300-5-2.800 0-4 1-7 3-10z" fill="#5865f2"/><circle cx="9" cy="12" r="1.600" fill="#fff"/><circle cx="15" cy="12" r="1.600" fill="#fff"/></svg>'
};
function icon(k) { const s = document.createElement('span'); s.className = 'ic'; s.innerHTML = IC[k] || ''; return s }
function span(cls, text) { const e = document.createElement('span'); e.className = cls; e.textContent = text; return e }
function add(parent, ...kids) { kids.forEach(k => parent.append(k)); return parent }

// Multi-platform chat box: polls /chat, newest at bottom, max 8, older lines fade.
function startChat(box, onTimer) {
 let last = 0, timer = 0;
 function line(m) {
  const d = document.createElement('div');
  d.className = 'l' + (m.kind == 'bot' ? ' bot' : '') + (m.kind != 'chat' && m.kind != 'bot' ? ' paid' : '');
  if (m.kind != 'bot') d.append(icon(m.platform));
  if (m.first) d.append(span('pill', 'FIRST TIME'));
  d.append(span('n', m.author), document.createTextNode((m.amount ? m.amount + ' ' : '') + m.text));
  box.append(d);
 }
 function fade() {
  const k = [...box.children]; while (k.length > 8) k.shift().remove();
  k.forEach((e, i) => e.style.opacity = .35 + .65 * (i + 1) / k.length);
 }
 async function poll() {
  try {
   const r = await (await fetch('/chat?since=' + last)).json();
   if (last == 0 && r.lines.length > 8) r.lines = r.lines.slice(-8);
   last = r.last; r.lines.forEach(line); fade();
   if (r.timer.n != timer) { if (timer && onTimer) onTimer(r.timer.text); timer = r.timer.n }
  } catch (e) {}
  setTimeout(poll, 1000);
 }
 poll();
}
