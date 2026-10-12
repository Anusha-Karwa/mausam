// Generates the updated Mausam components map (swimlane, 7 flows) as SVG.
// Usage: node map.js out.svg
const fs = require('fs');

const W = 1974, M = 30, TW = W - 2 * M, ACTOR_W = 200, COLS = 6, COL_W = (TW - ACTOR_W) / COLS;
const BOX_W = 232, FONT = 13, LH = 17, PADY = 9, GAP = 10, WRAP = 35;
const COLNAMES = ['Trigger', 'Input', 'Context', 'Processing', 'AI / ML', 'Output'];
const C = { trigger: 0, input: 1, context: 2, processing: 3, ai: 4, output: 5 };
const LANES = {
  meera: { name: 'Meera', sub: '(Investor, 28)', bg: '#EEF0FC', fill: '#E3E7FB', stroke: '#6E7FD6' },
  scheduler: { name: 'Scheduler', sub: '(Vercel Cron, GitHub Actions)', bg: '#F1F3F6', fill: '#E4E8ED', stroke: '#7C8796' },
  web: { name: 'Web app', sub: '(Mausam frontend)', bg: '#E8F5F1', fill: '#D5EEE6', stroke: '#2E8C76' },
  system: { name: 'System', sub: '(Flask backend on Vercel, Singapore)', bg: '#FFF7E3', fill: '#FDEBC0', stroke: '#D7A230' },
  ml: { name: 'ML model', sub: '(ARIMA + Ridge, GitHub Actions)', bg: '#FFF0E3', fill: '#FDDDBF', stroke: '#E08A2E' },
  ai: { name: 'AI model', sub: '(Gemini Flash)', bg: '#EAF6EA', fill: '#D8EFD7', stroke: '#4B9E4E' },
  guard: { name: 'Guardrails', sub: '(code checks, no AI)', bg: '#FCECEC', fill: '#F8D9D9', stroke: '#D35454' },
  db: { name: 'Database', sub: '(Neon PostgreSQL)', bg: '#EAF0FB', fill: '#D9E5F8', stroke: '#4F7FD0' },
  services: { name: 'Services', sub: '(mfapi.in, AMFI, Yahoo, GNews, RSS, Resend)', bg: '#FCEDF3', fill: '#F9DCE8', stroke: '#D4588A' },
};
const LANE_ORDER = ['meera', 'scheduler', 'web', 'system', 'ml', 'ai', 'guard', 'db', 'services'];

const FLOWS = [
  { title: 'Flow 1: Daily NAV job', when: 'Every evening, about 23:30 IST', steps: {
      t: ['scheduler', 'trigger', 'Vercel Cron calls /api/cron/daily at 18:00 UTC with CRON_SECRET'],
      in: ['services', 'input', 'mfapi.in: latest NAV for each fund and benchmark proxy, fills any missing days. AMFI NAVAll.txt fills in if mfapi.in fails or is behind. Yahoo: last 5 days of index closes. GNews: business headlines'],
      ctx: ['db', 'context', 'Active funds, last stored NAV dates, benchmark tickers and proxy scheme codes'],
      p: ['system', 'processing', 'Upserts nav_history (source: mfapi or amfi) and index_history. Flags stale data (over 4 days old) and big daily moves (over 3% equity, 0.5% debt). Clears cached index cards'],
      ai: ['ai', 'ai', 'Gemini writes "One thing to know today": biggest mover, next SIP date, one glossary word'],
      g: ['guard', 'ai', 'Tip checked: numbers match facts, no buy or sell words, short and friendly'],
      out: ['db', 'output', 'Saved: nav_history, index_history, news_items, alerts, daily_tips, job_runs log'],
      web: ['web', 'output', 'Today page: "Data as of" date with green dot, daily tip card, alerts bell count. Night mode after 6 pm'],
    }, edges: [['t', 'ctx'], ['in', 'p'], ['ctx', 'p'], ['p', 'ai'], ['ai', 'g'], ['g', 'out'], ['out', 'web']] },

  { title: 'Flow 2: Friday night forecasts and category averages', when: 'GitHub Actions, about 01:00 IST Saturday, or run manually', steps: {
      t: ['scheduler', 'trigger', 'GitHub Actions cron (Fri 19:30 UTC) or manual run. Secrets: DATABASE_URL, GEMINI_API_KEY'],
      in: ['services', 'input', 'mfapi.in: NAV history top-up for her funds and up to 12 peer funds per category. Yahoo: 5 years of index closes'],
      ctx: ['db', 'context', 'At least 300 daily NAVs per fund, benchmark and category history, category_peers list'],
      p: ['ml', 'processing', 'backfill.py and peers.py: category averages (1W, 1M, YTD, 1Y, 3Y) from 12 named similar funds'],
      m1: ['ml', 'ai', 'ARIMA on log NAV (d=1, drift, p and q chosen by AIC) + Ridge Regression for 7 and 30-day returns. Hybrid weight picked by backtest error'],
      m2: ['ml', 'ai', 'Walk-forward backtest: 12 monthly origins vs a "stays the same" guess. MAE, MAPE, skill, 80% coverage; bands calibrated'],
      ai: ['ai', 'ai', 'Gemini explains each range in 2 plain sentences: "an estimate, not a promise"'],
      g: ['guard', 'ai', 'Ranges only, no single price, no advice. Forecasts never feed the verdict'],
      out: ['db', 'output', 'Saved: category_stats, forecasts (point, 80% and 95% bands, path), forecast_metrics, job_runs'],
      web: ['web', 'output', 'Next month page: NAV with shaded ranges and accuracy vs a simple guess. 30-day range on each fund page'],
    }, edges: [['t', 'ctx'], ['in', 'p'], ['ctx', 'p'], ['p', 'm1'], ['m1', 'm2'], ['m2', 'ai'], ['ai', 'g'], ['g', 'out'], ['out', 'web']] },

  { title: 'Flow 3: Saturday weather report', when: 'Vercel Cron about 07:30 IST Saturday, or "Generate this week\'s report now"', steps: {
      mt: ['meera', 'trigger', 'Taps "Generate this week\'s report now" on Saturday reports (max once per 10 minutes)'],
      t: ['scheduler', 'trigger', 'Vercel Cron calls /api/cron/weekly at 02:00 UTC Saturday'],
      in: ['services', 'input', 'mfapi.in + AMFI: Friday\'s NAVs re-fetched. Yahoo: week\'s moves for Nifty 50, Midcap 150, Smallcap 100 and 250, S&P 500 and Nasdaq 100 (in INR). GNews headlines, RSS if it fails'],
      ctx: ['db', 'context', 'NAV and index history, category_stats, transactions, past weekly_snapshots, thresholds, latest forecasts'],
      p1: ['system', 'processing', 'Returns engine: 1W, 1M, YTD, 1Y, 3Y for fund, its market and similar funds. Saves weekly_snapshots'],
      p2: ['system', 'processing', 'Attribution labels each move: with the market, fund-specific, or partly both'],
      p3: ['system', 'processing', 'Verdict engine (rules, no AI): lagging streaks, gaps and mix drift pick 1 of 3 verdicts'],
      ai: ['ai', 'ai', 'Gemini reads the facts JSON and writes a headline, exactly 3 sentences per fund, a market paragraph and the verdict reason'],
      g: ['guard', 'ai', 'Checks: schema, 3 sentences each, verdict equals the code\'s verdict, every number matches facts, no advice words. 1 retry, then a fixed template'],
      out: ['db', 'output', 'Saved: reports (facts, text, HTML), market_context, news_items, alerts'],
      mail: ['services', 'output', 'Resend emails "Your money weather: Clear skies" to Meera'],
      web: ['web', 'output', 'Whole screen turns Clear skies, Passing clouds or Storm watch (rain falls). Today\'s Saturday report strip, Past weeks, Markets and news'],
      mo: ['meera', 'output', 'Opens Today, taps "Read this week\'s report" and understands her week in under 5 minutes'],
    }, edges: [['mt', 'ctx'], ['t', 'ctx'], ['in', 'p1'], ['ctx', 'p1'], ['p1', 'p2'], ['p2', 'p3'], ['p3', 'ai'], ['ai', 'g'], ['g', 'out'], ['out', 'mail'], ['out', 'web'], ['web', 'mo']] },

  { title: 'Flow 4: Meera adds or removes a fund', when: 'Manage funds page, no code changes', steps: {
      t: ['meera', 'trigger', 'Opens Manage funds and types a fund name or a 6-digit scheme code. Can also remove a fund'],
      in: ['web', 'input', 'Search box calls /api/funds/search; a code calls /api/funds/preview'],
      sv: ['services', 'input', 'mfapi.in search and scheme details: name, fund house, category, latest NAV'],
      ctx: ['db', 'context', 'Her current funds (duplicate check), benchmarks table, target mix'],
      p: ['system', 'processing', 'Validates the code, maps category to asset class and its market (she can override)'],
      mp: ['meera', 'processing', 'Checks the preview card, gives it a friendly name, clicks "Add fund"'],
      out: ['db', 'output', 'Saved: funds, full NAV history, 26 weeks of weekly_snapshots, peer funds. Remove = soft delete, history kept'],
      web: ['web', 'output', 'Fund appears on Today, My portfolio, Compare funds, Next month and in the next Saturday report. Its SIP is set on SIPs and payments'],
    }, edges: [['t', 'in'], ['in', 'p'], ['sv', 'p'], ['ctx', 'p'], ['p', 'mp'], ['mp', 'out'], ['out', 'web']] },

  { title: 'Flow 5: Meera\'s daily visit', when: 'Any time, phone or laptop', steps: {
      t: ['meera', 'trigger', 'Opens Mausam in the evening or at the weekend. After 6 pm it opens in night mode by itself'],
      in: ['web', 'input', 'Picks a page from the sidebar (This week, My money, Look ahead) or the bottom tab bar on her phone'],
      ctx: ['db', 'context', 'Latest NAVs, transactions, weekly_snapshots, reports, forecasts, tips and alerts'],
      p: ['system', 'processing', 'Portfolio maths: units, FIFO cost, value, gain, XIRR, mix vs target, 7-day change, gaps vs its market and similar funds'],
      w1: ['web', 'output', 'Today (this week only): weather sky, "This week in numbers", five fund tiles, next SIP, markets, news'],
      w2: ['web', 'output', 'My portfolio (all time): value, gain, XIRR, holdings, mix vs target, growth chart'],
      w3: ['web', 'output', 'Look ahead: Past weeks, Next month, Compare funds, Calculators'],
      mo: ['meera', 'output', 'Understands her money without jargon'],
    }, edges: [['t', 'in'], ['in', 'ctx'], ['ctx', 'p'], ['p', 'w1'], ['w1', 'mo']] },

  { title: 'Flow 6: SIPs and payments', when: 'SIPs and payments page (Mausam records, never moves money)', steps: {
      t: ['meera', 'trigger', 'Invests or withdraws in her real investing app (MF Central link), then records it in Mausam'],
      in: ['web', 'input', 'Monthly SIP plan (amount and day per fund) and "Record a payment": fund, SIP / buy / sell, date, amount or units'],
      ctx: ['db', 'context', 'NAV on that date (next business day if a holiday) and units she already holds'],
      p: ['system', 'processing', 'Units = amount ÷ NAV. A sell can\'t exceed units held. FIFO cost. "What if" simulator saves as simulated'],
      out: ['db', 'output', 'Saved: transactions; SIP amount and day on each fund'],
      web: ['web', 'output', 'History table, CSV export, SIP calendar, updated value and XIRR on My portfolio, next SIP on Today'],
    }, edges: [['t', 'in'], ['in', 'ctx'], ['ctx', 'p'], ['p', 'out'], ['out', 'web']] },

  { title: 'Flow 7: Ask Mausam (chat bubble)', when: 'Sun (day) or moon (night) chat bubble, bottom-right of every page', steps: {
      t: ['meera', 'trigger', 'Taps the chat bubble and asks: "Is my small-cap fund doing okay?"'],
      in: ['web', 'input', 'Chat panel with starter questions and her saved history'],
      ctx: ['db', 'context', 'Portfolio summary, latest snapshots and report, glossary, last 6 messages'],
      p: ['system', 'processing', 'Builds context without her name or email, rounds every number. Limit: 20 messages an hour'],
      ai: ['ai', 'ai', 'Gemini answers in plain words using only her numbers, in 90 words or less'],
      g: ['guard', 'ai', 'No buy, sell or switch advice, no guarantees, off-topic refused, numbers must match'],
      out: ['db', 'output', 'Saved: chat_messages'],
      web: ['web', 'output', 'Answer shown in the panel with a "not investment advice" note'],
    }, edges: [['t', 'in'], ['in', 'ctx'], ['ctx', 'p'], ['p', 'ai'], ['ai', 'g'], ['g', 'out'], ['out', 'web']] },
];

function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function wrap(text) {
  const words = text.split(' '), lines = []; let cur = '';
  for (const w of words) { if ((cur + ' ' + w).trim().length > WRAP) { lines.push(cur.trim()); cur = w; } else cur += ' ' + w; }
  if (cur.trim()) lines.push(cur.trim());
  return lines;
}
const colX = (c) => M + ACTOR_W + c * COL_W;

let svg = '', y = 0;
const FONT_FAMILY = "Segoe UI, Inter, Arial, sans-serif";
// title
y = 40;
svg += `<text x="${W / 2}" y="${y + 24}" text-anchor="middle" font-size="34" font-weight="700" fill="#1E2A44">Mausam · Mutual Fund Weather Tracker — Components Map</text>`;
svg += `<text x="${W / 2}" y="${y + 58}" text-anchor="middle" font-size="16" fill="#4A5566">Meera's five SIPs vs their markets and similar funds, explained every Saturday by Gemini Flash, with a full-screen weather sky, a hybrid ARIMA + Ridge forecast range and an Ask Mausam chat</text>`;
svg += `<text x="${W / 2}" y="${y + 82}" text-anchor="middle" font-size="13" fill="#7A8494">Updated 12 Oct 2026 · live at mausam-alpha-azure.vercel.app · code at github.com/Anusha-Karwa/mausam</text>`;
y += 112;

for (const flow of FLOWS) {
  // place boxes: group by lane+col
  const cells = {};
  for (const [id, [lane, col, text]] of Object.entries(flow.steps)) {
    const key = lane + '|' + col; (cells[key] = cells[key] || []).push({ id, lane, col: C[col], lines: wrap(text) });
  }
  const lanes = LANE_ORDER.filter((l) => Object.values(flow.steps).some((s) => s[0] === l));
  const laneH = {};
  for (const l of lanes) {
    let h = 0;
    for (const [k, boxes] of Object.entries(cells)) if (k.startsWith(l + '|')) {
      const ch = boxes.reduce((a, b) => a + b.lines.length * LH + PADY * 2, 0) + GAP * (boxes.length - 1);
      h = Math.max(h, ch);
    }
    laneH[l] = Math.max(78, h + 30);
  }
  // header card
  svg += `<rect x="${M}" y="${y}" width="${TW}" height="44" rx="8" fill="#FFFFFF" stroke="#E2E6EC"/>`;
  svg += `<text x="${M + 16}" y="${y + 28}" font-size="18" font-weight="700" fill="#1E2A44">${esc(flow.title)}</text>`;
  svg += `<text x="${M + TW - 16}" y="${y + 28}" text-anchor="end" font-size="13" fill="#5A6577">${esc(flow.when)}</text>`;
  y += 54;
  // column header
  svg += `<rect x="${M}" y="${y}" width="${TW}" height="38" rx="6" fill="#1F2A44"/>`;
  svg += `<text x="${M + ACTOR_W / 2}" y="${y + 24}" text-anchor="middle" font-size="14" fill="#FFFFFF">Actor</text>`;
  COLNAMES.forEach((n, i) => { svg += `<text x="${colX(i) + COL_W / 2}" y="${y + 24}" text-anchor="middle" font-size="14" fill="#FFFFFF">${n}</text>`; });
  y += 38;
  const top = y, pos = {};
  for (const l of lanes) {
    const L = LANES[l], h = laneH[l];
    svg += `<rect x="${M + ACTOR_W}" y="${y}" width="${TW - ACTOR_W}" height="${h}" fill="${L.bg}"/>`;
    svg += `<rect x="${M}" y="${y}" width="${ACTOR_W}" height="${h}" fill="#1F2A44"/>`;
    svg += `<line x1="${M}" y1="${y + h}" x2="${M + ACTOR_W}" y2="${y + h}" stroke="#34405C"/>`;
    svg += `<text x="${M + ACTOR_W / 2}" y="${y + h / 2 - 2}" text-anchor="middle" font-size="16" font-weight="700" fill="#FFFFFF">${L.name}</text>`;
    const subLines = wrap(L.sub).length > 1 ? [L.sub.slice(0, L.sub.lastIndexOf(',', 26) + 1), L.sub.slice(L.sub.lastIndexOf(',', 26) + 2)] : [L.sub];
    subLines.forEach((s, i) => { svg += `<text x="${M + ACTOR_W / 2}" y="${y + h / 2 + 16 + i * 14}" text-anchor="middle" font-size="11" fill="#B9C3D6">${esc(s)}</text>`; });
    // boxes in this lane
    for (const [k, boxes] of Object.entries(cells)) {
      if (!k.startsWith(l + '|')) continue;
      const total = boxes.reduce((a, b) => a + b.lines.length * LH + PADY * 2, 0) + GAP * (boxes.length - 1);
      let by = y + (h - total) / 2;
      for (const b of boxes) {
        const bh = b.lines.length * LH + PADY * 2, bx = colX(b.col) + (COL_W - BOX_W) / 2;
        svg += `<rect x="${bx}" y="${by}" width="${BOX_W}" height="${bh}" rx="6" fill="${L.fill}" stroke="${L.stroke}" stroke-width="1.3"/>`;
        b.lines.forEach((ln, i) => { svg += `<text x="${bx + BOX_W / 2}" y="${by + PADY + 12 + i * LH}" text-anchor="middle" font-size="${FONT}" fill="#1F2733">${esc(ln)}</text>`; });
        pos[b.id] = { x: bx, y: by, w: BOX_W, h: bh, col: b.col };
        by += bh + GAP;
      }
    }
    y += h;
  }
  // column dividers
  for (let i = 1; i < COLS; i++) svg += `<line x1="${colX(i)}" y1="${top}" x2="${colX(i)}" y2="${y}" stroke="#D5DBE3" stroke-width="1"/>`;
  // arrows
  const A = 'stroke="#2B3445" stroke-width="1.4" stroke-dasharray="5 4" fill="none" marker-end="url(#ah)"';
  for (const [a, b] of flow.edges) {
    const s = pos[a], t = pos[b];
    if (!s || !t) continue;
    if (s.col === t.col) {
      if (t.y > s.y) svg += `<path d="M${s.x + s.w / 2} ${s.y + s.h} L${t.x + t.w / 2} ${t.y - 2}" ${A}/>`;
      else svg += `<path d="M${s.x + s.w / 2} ${s.y} L${t.x + t.w / 2} ${t.y + t.h + 2}" ${A}/>`;
    } else {
      const sx = s.x + s.w, sy = s.y + s.h / 2, tx = t.x - 2, ty = t.y + t.h / 2;
      const mx = t.x - 14 - (s.col % 2) * 4;
      svg += `<path d="M${sx} ${sy} L${mx} ${sy} L${mx} ${ty} L${tx} ${ty}" ${A}/>`;
    }
  }
  y += 34;
}
const H = y + 10;
const out = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="${FONT_FAMILY}">
<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#2B3445"/></marker></defs>
<rect width="${W}" height="${H}" fill="#F7F8FA"/>${svg}</svg>`;
fs.writeFileSync(process.argv[2], out);
console.log('ok', W, H);
