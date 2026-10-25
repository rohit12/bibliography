// Theories: chains of links drawn from endnote claims, the works each step rests on, and a stress test.
import {$, D, need, esc, clabel, linkHTML, sign, btitle, bvar, workLink, workShort, claimsHTML, notesFor, badge, WEAK, fail} from './core.js';

const S = {removed: new Set(), dropWeak: false, minCites: 0, theory: null};

export async function mount(el, args) {
  try { await need('works', 'books', 'endnotes', 'concepts', 'theories', 'analysis'); } catch (e) { return fail(el, e); }
  S.el = el;
  update(args);
}

export function update(args) {
  const id = args[0];
  const t = D.theories.find(t => t.id === id);
  if (t) detail(t); else overview();
  scrollTo({top: 0});
}

function health(p) {
  const n = p.steps.length, bad = p.unsupported.length, thin = p.fragile.length;
  return `<span class="${bad ? 'no' : 'ok'}">${n - bad}/${n} steps supported</span>` + (thin ? `<span class="warn">${thin} on a single source</span>` : '');
}

function overview() {
  const groups = {};
  for (const t of D.theories) {
    const key = t.kind === 'synthesis' || (t.books || []).length !== 1 ? '_syn' : t.books[0];
    (groups[key] ??= []).push(t);
  }
  const order = [...D.bookOrder.filter(b => groups[b]), ...(groups._syn ? ['_syn'] : [])];
  S.el.innerHTML = `<p class="lede">Each theory is a chain of cause-and-effect links. A link is supported when an endnote in the book states it; the works that endnote cites are its evidence. <b>Pillars</b> are the works carrying the most of that weight; a ★ means some step rests on that work alone. Open a theory to stress-test it.</p>` +
    order.map(k => `<h2>${k === '_syn' ? 'Syntheses across books' : esc(btitle(k))}</h2><div class="cards">` +
      groups[k].map(t => {
        const p = D.analysis.theories[t.id];
        return `<a class="card" href="#theories/${t.id}"><h4>${esc(t.title)}</h4><p>${esc(t.summary || '')}</p><div class="health">${health(p)}<span>${p.pillars.length} works</span></div></a>`;
      }).join('') + '</div>').join('') +
    `<h2>What each book rests on</h2><p class="sub">Works ranked by how much of a book's linked argument cites them (each endnote's links shared among the works it cites).</p>` +
    D.bookOrder.map(b => `<h3 style="${bvar(b)}"><span class="bkdot"></span>${esc(btitle(b))}</h3>` + pillarTable(D.analysis.books[b].slice(0, 15), {steps: false})).join('');
}

function pillarTable(rows, {steps = true, stress = false} = {}) {
  if (!rows.length) return '<p class="sub">No linked endnotes yet.</p>';
  const max = Math.max(...rows.map(r => r.load));
  const anyCites = rows.some(r => r.cites != null);
  return `<div class="scroll"><table><thead><tr>${stress ? '<th>Keep</th>' : ''}<th>Work</th><th>Load</th>${steps ? '<th>Steps</th><th title="Steps that rest on this work alone">★</th>' : ''}${anyCites ? '<th class="num">Cited by</th>' : ''}<th>Evidence</th></tr></thead><tbody>` +
    rows.map(r => {
      const w = D.work[r.id];
      return `<tr>${stress ? `<td><input type="checkbox" data-w="${r.id}" ${S.removed.has(r.id) ? '' : 'checked'} aria-label="Keep ${esc(workShort(w))}"></td>` : ''}` +
        `<td>${workLink(r.id)} <span class="sub" style="font-size:12.5px">${esc((w?.title || '').slice(0, 70))}${(w?.title || '').length > 70 ? '…' : ''}</span></td>` +
        `<td style="min-width:90px"><div class="bar" title="${r.load}"><i style="width:${(100 * r.load / max).toFixed(0)}%"></i></div></td>` +
        (steps ? `<td class="num">${r.steps.map(i => i + 1).join(' ')}</td><td>${'★'.repeat(r.critical.length)}</td>` : '') +
        (anyCites ? `<td class="num">${r.cites != null ? r.cites.toLocaleString() : ''}</td>` : '') +
        `<td>${badge(r.status)}${r.method ? `<span class="badge method">${esc(r.method)}</span>` : ''}${r.type === 'dataset' ? '<span class="badge method">dataset</span>' : ''}</td></tr>`;
    }).join('') + '</tbody></table></div>';
}

function removedSet(p) {
  const out = new Set(S.removed);
  for (const r of p.pillars) {
    if (S.dropWeak && r.weak) out.add(r.id);
    if (S.minCites && r.cites != null && r.cites < S.minCites) out.add(r.id);
  }
  return out;
}

function stepState(st, gone) {
  const left = st.works.filter(w => !gone.has(w));
  if (!st.works.length) return {cls: 'none', left};
  if (!left.length) return {cls: 'broken', left};
  return {cls: left.length === 1 || st.sources <= 1 ? 'thin' : 'ok', left};
}

function detail(t) {
  if (S.theory !== t.id) { S.removed = new Set(); S.theory = t.id; }
  const p = D.analysis.theories[t.id];
  const anyCites = p.pillars.some(r => r.cites != null), anyWeak = p.pillars.some(r => r.weak);
  S.el.innerHTML = `<p style="margin:16px 0 0"><a href="#theories">← All theories</a></p>
    <h2 style="margin-top:8px">${esc(t.title)}</h2>
    <p class="sub">${esc(t.summary || '')}</p>
    <p class="sub">From ${(t.books || []).map(b => `<span style="${bvar(b)}"><span class="bkdot"></span>${esc(btitle(b))}</span>`).join(' and ')}</p>
    <div id="graph"></div>
    <div class="legend"><span style="--c:var(--good)">supported by 2+ sources</span><span style="--c:var(--flag)">one source</span><span style="--c:var(--bad)">no support left</span></div>
    <div class="stress" id="stress">
      <span class="verdict" id="verdict"></span>
      ${anyWeak ? '<label><input type="checkbox" id="dropWeak"> Drop contested or retracted works</label>' : ''}
      ${anyCites ? '<label>Drop works cited fewer than <input type="number" id="minCites" min="0" step="10" value="0"> times</label>' : ''}
      <button type="button" class="quiet" id="dropTop">Remove the top pillar</button>
      <button type="button" class="quiet" id="reset">Reset</button>
    </div>
    <h3>Steps</h3><div id="steps"></div>
    <h3>Pillars</h3><p class="sub">Untick a work to see which steps would lose their support.</p><div id="pillars"></div>`;
  if ($('dropWeak')) { $('dropWeak').checked = S.dropWeak; $('dropWeak').onchange = e => { S.dropWeak = e.target.checked; paint(t, p); }; }
  if ($('minCites')) { $('minCites').value = S.minCites; $('minCites').oninput = e => { S.minCites = +e.target.value || 0; paint(t, p); }; }
  $('dropTop').onclick = () => { const gone = removedSet(p); const top = p.pillars.find(r => !gone.has(r.id)); if (top) { S.removed.add(top.id); paint(t, p); } };
  $('reset').onclick = () => { S.removed.clear(); S.dropWeak = false; S.minCites = 0; detail(t); };
  $('pillars').addEventListener('change', e => { const c = e.target.closest('input[data-w]'); if (!c) return; c.checked ? S.removed.delete(c.dataset.w) : S.removed.add(c.dataset.w); paint(t, p); });
  paint(t, p);
}

function paint(t, p) {
  const gone = removedSet(p);
  const states = p.steps.map(st => stepState(st, gone));
  const ok = states.filter(s => s.cls === 'ok' || s.cls === 'thin').length;
  $('verdict').innerHTML = gone.size ? `<span class="${ok === p.steps.length ? 'ok' : 'no'}">${ok} of ${p.steps.length} steps still supported</span> after removing ${gone.size} work${gone.size === 1 ? '' : 's'}` : `${ok} of ${p.steps.length} steps supported`;
  $('graph').innerHTML = graphSVG(t, p, states);
  $('steps').innerHTML = p.steps.map((st, i) => {
    const s = states[i];
    const facts = [`${st.notes.length} endnote${st.notes.length === 1 ? '' : 's'}`, `${s.left.length}${s.left.length !== st.works.length ? ' of ' + st.works.length : ''} works`, `${st.sources} independent source${st.sources === 1 ? '' : 's'}`];
    const extra = (st.also.length ? ` · <span class="ok">+${st.also.length} in other books</span>` : '') + (st.against.length ? ` · <span class="no">${st.against.length} against</span>` : '');
    const own = claimsHTML(notesFor(st.notes)), also = claimsHTML(notesFor(st.also)), against = claimsHTML(notesFor(st.against.map(a => [a[0], a[1]])));
    return `<div class="step ${s.cls === 'broken' || s.cls === 'none' ? 'broken' : ''}"><span class="n">${i + 1}</span><span class="t">${linkHTML(st.from, st.to, st.sign)}</span>` +
      `<span class="s ${s.cls === 'ok' ? '' : s.cls === 'thin' ? 'warn' : 'no'}">${facts.join(' · ')}${extra}</span>` +
      `<details><summary>Endnotes and works</summary>${own || '<p class="sub">No endnote in this theory\'s books states this link.</p>'}` +
      (also ? `<h3>Also stated in other books</h3>${also}` : '') + (against ? `<h3>Stated the other way</h3>${against}` : '') + '</details></div>';
  }).join('');
  $('pillars').innerHTML = pillarTable(p.pillars, {stress: true});
}

// Layered left-to-right drawing of the theory's links.
function graphSVG(t, p, states) {
  const nodes = [...new Set(p.steps.flatMap(s => [s.from, s.to]))];
  const out = Object.fromEntries(nodes.map(n => [n, []]));
  p.steps.forEach((s, i) => out[s.from].push(i));
  // drop back edges (cycles) for layering
  const back = new Set(), mark = {};
  const dfs = n => { mark[n] = 1; for (const i of out[n]) { const m = p.steps[i].to; if (mark[m] === 1) back.add(i); else if (!mark[m]) dfs(m); } mark[n] = 2; };
  nodes.forEach(n => mark[n] || dfs(n));
  const rank = Object.fromEntries(nodes.map(n => [n, 0]));
  for (let k = 0; k < nodes.length; k++) p.steps.forEach((s, i) => { if (!back.has(i) && s.from !== s.to) rank[s.to] = Math.max(rank[s.to], rank[s.from] + 1); });
  const cols = [];
  nodes.forEach(n => (cols[rank[n]] ??= []).push(n));
  const pos = {};
  cols.forEach((col, c) => {
    if (c) col.sort((a, b) => avg(a) - avg(b));
    col.forEach((n, r) => pos[n] = {c, r});
  });
  function avg(n) { const ps = p.steps.filter(s => s.to === n && pos[s.from]).map(s => pos[s.from].r); return ps.length ? ps.reduce((x, y) => x + y) / ps.length : 0; }
  const W = 170, H = 46, GX = 70, GY = 26, PAD = 18;
  const rows = Math.max(...cols.map(c => c.length));
  const width = PAD * 2 + cols.length * W + (cols.length - 1) * GX, height = PAD * 2 + rows * H + (rows - 1) * GY;
  const xy = n => { const {c, r} = pos[n]; const off = (rows - cols[c].length) * (H + GY) / 2; return {x: PAD + c * (W + GX), y: PAD + off + r * (H + GY)}; };
  const color = {ok: 'var(--good)', thin: 'var(--flag)', broken: 'var(--bad)', none: 'var(--bad)'};
  const edges = p.steps.map((s, i) => {
    const a = xy(s.from), b = xy(s.to), st = states[i].cls;
    let d, mx, my;
    if (pos[s.to].c > pos[s.from].c) {
      const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2;
      d = `M${x1},${y1} C${x1 + GX / 2},${y1} ${x2 - GX / 2},${y2} ${x2},${y2}`; mx = (x1 + x2) / 2; my = (y1 + y2) / 2;
    } else {  // backward or same column: arc over the top
      const x1 = a.x + W / 2 + 12, y1 = a.y, x2 = b.x + W / 2 - 12, y2 = b.y, lift = 30 + Math.abs(x1 - x2) / 6;
      d = `M${x1},${y1} C${x1},${Math.min(y1, y2) - lift} ${x2},${Math.min(y1, y2) - lift} ${x2},${y2}`; mx = (x1 + x2) / 2; my = Math.min(y1, y2) - lift * 0.75;
    }
    const g = {'+': '↑', '-': '↓', '0': '∅', '~': '∩', '?': '~'}[s.sign];
    return `<g><title>${i + 1}. ${esc(clabel(s.from))} → ${esc(clabel(s.to))}</title><path class="edge" d="${d}" stroke="${color[st]}" ${st === 'broken' || st === 'none' ? 'stroke-dasharray="5 4"' : ''} marker-end="url(#ah-${st})"/>` +
      `<circle cx="${mx}" cy="${my}" r="9" fill="var(--surface)" stroke="${color[st]}"/><text x="${mx}" y="${my + 4}" text-anchor="middle" style="font-weight:600">${g}</text></g>`;
  }).join('');
  const boxes = nodes.map(n => {
    const {x, y} = xy(n), lines = wrap(clabel(n), 24);
    return `<g class="node"><rect x="${x}" y="${y}" width="${W}" height="${H}" rx="6"/>` +
      lines.map((l, k) => `<text x="${x + W / 2}" y="${y + H / 2 + (k - (lines.length - 1) / 2) * 14 + 4}" text-anchor="middle">${esc(l)}</text>`).join('') + '</g>';
  }).join('');
  const marker = Object.entries(color).map(([k, c]) => `<marker id="ah-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${c}"/></marker>`).join('');
  return `<svg class="graph" viewBox="0 -40 ${width} ${height + 40}" role="img" aria-label="Diagram of the theory's links"><defs>${marker}</defs>${edges}${boxes}</svg>`;
}

function wrap(s, n) {
  const words = s.split(' '), lines = [''];
  for (const w of words) { const cur = lines[lines.length - 1]; if ((cur + ' ' + w).trim().length > n && cur) lines.push(w); else lines[lines.length - 1] = (cur + ' ' + w).trim(); }
  return lines.length > 2 ? [lines[0], lines.slice(1).join(' ').slice(0, n - 1) + '…'] : lines;
}
