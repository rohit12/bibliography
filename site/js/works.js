// Works: every cited work, filterable by book and chapter, with what each endnote citing it claims.
import {$, D, need, esc, claimsHTML, badge, WEAK, toast, fail} from './core.js';

const KIND = {'article': 'Journal article', 'book': 'Book, report or dataset', 'chapter': 'Book chapter', 'working-paper': 'Working paper', 'dataset': 'Dataset', 'report': 'Report', 'news': 'News or web'};
const S = {mode: 'all', shown: [], ready: false, ctl: {}};

export async function mount(el, args, hash) {
  try { await need('works', 'books', 'endnotes', 'concepts'); } catch (e) { return fail(el, e); }
  if (!S.ready) prepare();
  el.innerHTML = `
  <div class="tally" id="tally"></div>
  <div class="controls">
    <input id="q" type="search" placeholder="Search author, title, journal, claim, or note (e.g. Ch 8 n23)" aria-label="Search works">
    <select id="book" aria-label="Filter by book"></select>
    <select id="chap" aria-label="Filter by chapter" disabled><option value="">All chapters</option></select>
    <select id="type" aria-label="Filter by type"></select>
    <select id="sort" aria-label="Sort order">
      <option value="az">Author A–Z</option><option value="new">Newest first</option><option value="old">Oldest first</option>
      <option value="most">Most cited in endnotes</option><option value="books">In most books</option>
      <option value="oa" hidden>Most cited (OpenAlex)</option>
    </select>
    <div class="row2">
      <button type="button" class="chip" data-mode="all" aria-pressed="true">All entries</button>
      <button type="button" class="chip" data-mode="multi" aria-pressed="false" hidden>Cited in 2+ books</button>
      <button type="button" class="chip" data-mode="disputed" aria-pressed="false" hidden>Contested or retracted</button>
      <button type="button" id="copy" style="margin-left:auto">Copy shown as text</button>
    </div>
  </div>
  <div class="status"><span id="count"></span><span class="letters" id="letters"></span></div>
  <ol class="list" id="list"></ol>
  <div id="fallback" hidden><textarea id="fbText" readonly aria-label="Citations to copy"></textarea></div>`;
  setup();
  render();
  update(args, hash);
}

export function update(args, hash) {
  if (!hash?.startsWith('w-')) return;
  let li = document.getElementById(hash);
  if (!li && D.work[hash.slice(2)]) {  // hidden by filters: clear them
    S.mode = 'all'; ['q', 'type', 'chap'].forEach(id => $(id).value = ''); $('book').value = '';
    setChapters(); render(); li = document.getElementById(hash);
  }
  document.querySelectorAll('.entry.hit').forEach(e => e.classList.remove('hit'));
  if (li) { li.classList.add('hit'); li.scrollIntoView({block: 'center'}); }
}

function prepare() {
  const order = D.bookOrder;
  for (const w of D.works) {
    w.c = (w.cited_in || []).slice().sort((a, b) => order.indexOf(a.book) - order.indexOf(b.book) || locKey(a) - locKey(b));
    w.citedBooks = [...new Set(w.c.map(c => c.book))];
    w.books = [...new Set([...(w.listed_in || []), ...w.citedBooks])];
    w.oaN = w.oa && w.oa.cited_by_count != null ? w.oa.cited_by_count : -1;
    w.sortName = w.authors.normalize('NFD').replace(/[̀-ͯ’']/g, '').replace(/^(de |von |van )/i, '').toLowerCase();
    w.y = parseInt(w.year) || 0;
    w.uses = w.c.map(c => D.note[c.book + '|' + c.loc]).filter(Boolean);
    w.key = [w.authors, w.title, w.venue, w.year, w.id, (w.claims || []).join(' '), w.c.map(c => c.loc).join(' '),
      (w.oa && w.oa.topics || []).join(' '), w.uses.map(n => n.claim).join(' ')].join(' ').toLowerCase();
  }
  S.multi = D.books.length > 1;
  S.ready = true;
}

function locKey(c) {
  const order = Object.keys(D.book[c.book]?.chapters || {});
  const [ch, n] = c.loc.split(' n'); const i = order.indexOf(ch);
  return (i < 0 ? 999 : i) * 10000 + (parseInt(n) || 0);
}

function setup() {
  const byTitle = D.books.slice().sort((a, b) => a.title.replace(/^(the|a|an) /i, '').localeCompare(b.title.replace(/^(the|a|an) /i, '')));
  $('book').innerHTML = '<option value="">All books</option>' + byTitle.map(b => `<option value="${b.id}" title="${esc(b.title)} — ${esc(b.author)}">${esc(b.title)}</option>`).join('');
  $('type').innerHTML = '<option value="">All types</option>' + Object.entries(KIND).filter(([k]) => D.works.some(w => w.type === k)).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
  if (S.multi) document.querySelector('[data-mode="multi"]').hidden = false;
  if (D.works.some(w => WEAK.has(w.status))) document.querySelector('[data-mode="disputed"]').hidden = false;
  if (D.works.some(w => w.oaN >= 0)) $('sort').querySelector('[value="oa"]').hidden = false;
  if (!S.multi) $('sort').querySelector('[value="books"]').hidden = true;
  const chips = [...document.querySelectorAll('.chip[data-mode]')];
  if (chips.filter(c => !c.hidden).length < 2) chips.forEach(c => c.hidden = true);
  for (const [id, v] of Object.entries(S.ctl)) if ($(id)) $(id).value = v;
  setChapters(); if (S.ctl.chap) $('chap').value = S.ctl.chap;
  document.querySelectorAll('.chip[data-mode]').forEach(b => { b.setAttribute('aria-pressed', b.dataset.mode === S.mode); b.onclick = () => setMode(b.dataset.mode); });
  const W = D.works, n = D.books.length, multiN = W.filter(w => w.citedBooks.length > 1).length;
  $('tally').innerHTML = `<span><b>${W.length}</b>works</span><span><b>${n}</b>${n === 1 ? 'book' : 'books'}</span>` +
    `<span><b>${D.endnotes.length}</b>endnote claims</span>` +
    (S.multi ? `<span><b>${multiN}</b><a href="#" data-mode="multi">cited in 2+ books</a></span>` : '');
  $('tally').addEventListener('click', e => { const a = e.target.closest('a[data-mode]'); if (a) { e.preventDefault(); setMode(a.dataset.mode); } });
  ['q', 'type', 'chap', 'sort'].forEach(id => $(id).addEventListener('input', render));
  $('book').addEventListener('input', () => { setChapters(); render(); });
  $('letters').addEventListener('click', e => {
    const a = e.target.closest('a'); if (!a) return; e.preventDefault();
    const t = document.getElementById(a.dataset.l); if (!t) return;
    const y = t.closest('li').getBoundingClientRect().top + scrollY - document.querySelector('.controls').offsetHeight - 8;
    scrollTo({top: y, behavior: 'smooth'});
  });
  $('list').addEventListener('toggle', e => {
    const d = e.target; if (!d.matches?.('details.uses') || !d.open || d.dataset.done) return;
    const w = D.work[d.dataset.id], bk = $('book').value;
    d.insertAdjacentHTML('beforeend', claimsHTML(w.uses.filter(n => !bk || n.book === bk)));
    d.dataset.done = 1;
  }, true);
  $('copy').onclick = copy;
}

function setChapters() {
  const b = D.book[$('book').value];
  $('chap').disabled = !b;
  $('chap').innerHTML = '<option value="">All chapters</option>' + (b ? Object.entries(b.chapters || {}).map(([k, v]) => `<option value="${esc(k)}">${esc(k.startsWith('Ch ') ? k.slice(3) + ' ' + v : v)}</option>`).join('') : '');
}
function setMode(m) { S.mode = m; document.querySelectorAll('.chip[data-mode]').forEach(b => b.setAttribute('aria-pressed', b.dataset.mode === m)); render(); }
function hl(s, q) { s = String(s ?? ''); if (!q) return esc(s); const i = s.toLowerCase().indexOf(q); return i < 0 ? esc(s) : esc(s.slice(0, i)) + '<mark>' + esc(s.slice(i, i + q.length)) + '</mark>' + esc(s.slice(i + q.length)); }

function render() {
  const q = $('q').value.trim().toLowerCase(), t = $('type').value, bk = $('book').value, ch = $('chap').value, s = $('sort').value, m = S.mode;
  S.ctl = {q: $('q').value, type: t, book: bk, chap: ch, sort: s};
  const cs = w => bk ? w.c.filter(c => c.book === bk) : w.c;
  S.shown = D.works.filter(w => (!q || w.key.includes(q)) && (!t || w.type === t) && (!bk || w.books.includes(bk))
    && (!ch || w.c.some(c => c.book === bk && c.loc.split(' n')[0] === ch))
    && (m !== 'multi' || w.citedBooks.length > 1) && (m !== 'disputed' || WEAK.has(w.status)));
  const byName = (a, b) => a.sortName.localeCompare(b.sortName) || a.y - b.y;
  const cmp = {az: byName, new: (a, b) => b.y - a.y || byName(a, b), old: (a, b) => a.y - b.y || byName(a, b),
    most: (a, b) => cs(b).length - cs(a).length || byName(a, b),
    books: (a, b) => b.citedBooks.length - a.citedBooks.length || b.c.length - a.c.length || byName(a, b),
    oa: (a, b) => b.oaN - a.oaN || byName(a, b)}[s];
  S.shown.sort(cmp);
  $('count').textContent = `Showing ${S.shown.length} of ${D.works.length}`;
  const seen = new Set();
  $('list').innerHTML = S.shown.length ? S.shown.map(w => {
    const L = (w.sortName[0] || '#').toUpperCase(); let anchor = '';
    if (s === 'az' && !seen.has(L)) { seen.add(L); anchor = `<span id="L-${L}"></span>`; }
    const body = w.title ? `<span class="au">${hl(w.authors, q)}</span> (${esc(w.year)}). <span class="ti">${hl(w.title, q)}</span>${w.venue ? '. ' + hl(w.venue, q) : ''}.` : hl(w.citation, q);
    const shownC = cs(w);
    let notes = '';
    if (shownC.length && S.multi && !bk) {
      const g = {}; shownC.forEach(c => (g[c.book] = g[c.book] || []).push(c.loc));
      notes = '<span class="notes">' + Object.entries(g).map(([b, l]) => `<span class="bk">${esc(D.book[b].title)}:</span> ${l.join(' · ')}`).join(' &nbsp; ') + '</span>';
    } else if (shownC.length) notes = `<span class="notes">${shownC.map(c => c.loc).join(' · ')}</span>`;
    const badges = badge(w.status) + (w.method ? `<span class="badge method">${esc(w.method)}</span>` : '') + (w.citedBooks.length > 1 ? `<span class="badge multi">${w.citedBooks.length} books</span>` : '');
    const oa = w.oaN >= 0 ? `<span title="Citations counted by OpenAlex">${w.oaN.toLocaleString()} citations</span>` : '';
    const doi = w.doi ? `<a href="https://doi.org/${esc(w.doi)}" rel="noopener">DOI</a>` : '';
    const nUses = w.uses.filter(n => !bk || n.book === bk).length;
    const uses = nUses ? `<details class="uses" data-id="${w.id}"><summary>What ${nUses === 1 ? 'the endnote claims' : `the ${nUses} endnotes claim`}</summary></details>` : '';
    return `<li class="entry" id="w-${w.id}"><div class="yr">${anchor}${esc(w.year)}</div><div class="body"><div class="cit">${body}</div><div class="meta">${badges}<span class="kind">${KIND[w.type] || w.type}</span>${notes}${oa}${doi}<a href="#w-${w.id}" aria-label="Link to this entry">#</a></div>${uses}</div></li>`;
  }).join('') : '<li class="empty">No works match. Clear the search or switch filters.</li>';
  const have = new Set(s === 'az' ? S.shown.map(w => (w.sortName[0] || '#').toUpperCase()) : []);
  $('letters').innerHTML = s === 'az' ? 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').map(l => have.has(l) ? `<a href="#" data-l="L-${l}">${l}</a>` : `<span>${l}</span>`).join('') : '';
}

function copy() {
  const txt = S.shown.map(w => w.citation || `${w.authors} (${w.year}). ${w.title}.`).join('\n');
  const done = () => { $('fallback').hidden = true; toast(`Copied ${S.shown.length} citations`); };
  const no = () => { $('fallback').hidden = false; $('fbText').value = txt; $('fbText').select(); toast('Copy blocked here. Text selected below, press Ctrl/Cmd+C'); };
  navigator.clipboard ? navigator.clipboard.writeText(txt).then(done, no) : no();
}
