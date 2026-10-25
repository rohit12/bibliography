// Compare: what the books say about places, religions and social groups, mechanism by mechanism.
import {$, D, need, esc, clabel, glabel, bshort, btitle, bvar, claimsHTML, fail} from './core.js';

const KINDS = [['place', 'Places'], ['religion', 'Religions'], ['social', 'Groups']];
const S = {books: null, cell: null};

export async function mount(el, args) {
  try { await need('works', 'books', 'endnotes', 'concepts', 'groups'); } catch (e) { return fail(el, e); }
  S.books ??= new Set(D.bookOrder);
  S.count = {};
  for (const n of D.endnotes) for (const g of n.about || []) S.count[g] = (S.count[g] || 0) + 1;
  el.innerHTML = `<p class="lede">Pick places, religions or social groups to compare. Rows are mechanisms (concepts in the endnotes' links); each cell counts the endnotes about that group that involve that mechanism. Click a cell to read them.</p>
    <div class="pickers" id="pickers"></div>
    <div class="pickers"><div class="row"><span>Books</span>${D.bookOrder.map(b => `<button type="button" class="chip" data-book="${b}" style="${bvar(b)}"><span class="bkdot"></span>${esc(btitle(b))}</button>`).join('')}</div></div>
    <div id="profile" class="status"></div>
    <div class="scroll"><table class="grid" id="grid"></table></div>
    <div id="detail"></div>`;
  $('pickers').innerHTML = KINDS.map(([k, lab]) => {
    const gs = D.groups.filter(g => g.kind === k && S.count[g.id]).sort((a, b) => S.count[b.id] - S.count[a.id]);
    return gs.length ? `<div class="row"><span>${lab}</span>${gs.map(g => `<button type="button" class="chip" data-g="${g.id}">${esc(g.label)} <small>${S.count[g.id]}</small></button>`).join('')}</div>` : '';
  }).join('');
  $('pickers').onclick = e => {
    const c = e.target.closest('[data-g]'); if (!c) return;
    const sel = current(); const i = sel.indexOf(c.dataset.g);
    i < 0 ? sel.push(c.dataset.g) : sel.splice(i, 1);
    location.hash = 'compare/' + sel.join(',');
  };
  el.querySelectorAll('[data-book]').forEach(b => b.onclick = () => {
    S.books.has(b.dataset.book) ? S.books.delete(b.dataset.book) : S.books.add(b.dataset.book);
    if (!S.books.size) S.books = new Set(D.bookOrder);
    render();
  });
  $('grid').onclick = e => { const td = e.target.closest('td.cell'); if (td) { S.cell = [td.dataset.g, td.dataset.c]; render(); $('detail').scrollIntoView({behavior: 'smooth', block: 'start'}); } };
  update(args);
}

function current() {
  const h = decodeURIComponent(location.hash).split('/')[1];
  if (h !== undefined) return h.split(',').filter(g => D.group[g]);
  return Object.entries(S.count).filter(([g]) => g !== 'global').sort((a, b) => b[1] - a[1]).slice(0, 5).map(([g]) => g);
}

export function update() { render(); }

function render() {
  const sel = current();
  document.querySelectorAll('#pickers [data-g]').forEach(c => c.setAttribute('aria-pressed', sel.includes(c.dataset.g)));
  document.querySelectorAll('[data-book]').forEach(c => c.setAttribute('aria-pressed', S.books.has(c.dataset.book)));
  const notes = D.endnotes.filter(n => S.books.has(n.book) && (n.about || []).some(g => sel.includes(g)));
  const cell = {}, tot = {};
  for (const n of notes) {
    const cs = new Set((n.links || []).flatMap(l => [l[0], l[1]]));
    for (const g of n.about.filter(g => sel.includes(g))) {
      (tot[g] ??= {})[n.book] = (tot[g][n.book] || 0) + 1;
      for (const c of cs) ((cell[c] ??= {})[g] ??= []).push(n);
    }
  }
  $('profile').innerHTML = sel.length ? sel.map(g => `<span><b>${esc(glabel(g))}</b>: ${Object.entries(tot[g] || {}).map(([b, k]) => `${esc(bshort(b))} ${k}`).join(', ') || 'no endnotes'}</span>`).join('') : '<span>Pick at least one group above.</span>';
  const rows = Object.keys(cell).sort((a, b) => total(cell[b]) - total(cell[a])).slice(0, 30);
  const max = Math.max(1, ...rows.flatMap(c => sel.map(g => cell[c][g]?.length || 0)));
  $('grid').innerHTML = sel.length && rows.length ? `<thead><tr><th>Mechanism</th>${sel.map(g => `<th class="g">${esc(glabel(g))}</th>`).join('')}</tr></thead><tbody>` +
    rows.map(c => `<tr><td class="c">${esc(clabel(c))}</td>` + sel.map(g => {
      const ns = cell[c][g] || [];
      if (!ns.length) return '<td class="cell" style="cursor:default;color:var(--rule)">·</td>';
      const per = {}; ns.forEach(n => per[n.book] = (per[n.book] || 0) + 1);
      const on = S.cell && S.cell[0] === g && S.cell[1] === c;
      return `<td class="cell${on ? ' sel' : ''}" data-g="${g}" data-c="${c}" style="background:color-mix(in srgb, var(--accent) ${Math.round(8 + 42 * ns.length / max)}%, var(--surface))">${ns.length}` +
        `<div class="dots">${Object.entries(per).map(([b, k]) => `<i style="${bvar(b)};width:${Math.max(4, 28 * k / ns.length)}px" title="${esc(bshort(b))} ${k}"></i>`).join('')}</div></td>`;
    }).join('') + '</tr>').join('') + '</tbody>' : '';
  if (S.cell && sel.includes(S.cell[0]) && cell[S.cell[1]]?.[S.cell[0]]) {
    const [g, c] = S.cell;
    $('detail').innerHTML = `<h2>${esc(glabel(g))} · ${esc(clabel(c))}</h2>` + claimsHTML(cell[c][g]);
  } else $('detail').innerHTML = sel.length ? `<h2>All endnotes about ${sel.map(glabel).map(esc).join(', ')}</h2>` + claimsHTML(notes.slice(0, 200)) : '';
}
const total = row => Object.values(row).reduce((s, ns) => s + ns.length, 0);
