// Shared data loading and rendering helpers. Every view imports from here.
export const $ = id => document.getElementById(id);
export const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

const cache = {};
export const D = {};

export function getJSON(name) {
  return cache[name] ??= fetch(`data/${name}.json`, {cache: 'no-cache'}).then(r => {
    if (!r.ok) throw new Error(`${name}.json ${r.status}`);
    return r.json();
  });
}

// Load named data files (works, books, endnotes, …) and build lookups once.
export async function need(...names) {
  const vals = await Promise.all(names.map(getJSON));
  names.forEach((n, i) => { if (!D[n]) { D[n] = vals[i]; index(n); } });
  return D;
}

function index(name) {
  if (name === 'books') {
    D.book = Object.fromEntries(D.books.map((b, i) => [b.id, {...b, i}]));
    D.bookOrder = D.books.map(b => b.id);
  }
  if (name === 'works') {
    D.work = Object.fromEntries(D.works.map(w => [w.id, w]));
    D.at = {};  // "book|loc" -> [work ids]
    for (const w of D.works) for (const c of w.cited_in || []) (D.at[c.book + '|' + c.loc] ??= []).push(w.id);
  }
  if (name === 'endnotes') {
    D.note = Object.fromEntries(D.endnotes.map(n => [n.book + '|' + n.loc, n]));
    D.byLink = {};  // "from>to" -> [notes]
    for (const n of D.endnotes) for (const [a, b] of n.links || []) (D.byLink[a + '>' + b] ??= []).push(n);
  }
  if (name === 'concepts') D.concept = Object.fromEntries(D.concepts.map(c => [c.id, c]));
  if (name === 'groups') D.group = Object.fromEntries(D.groups.map(g => [g.id, g]));
}

export const clabel = id => D.concept?.[id]?.label ?? id.replace(/_/g, ' ');
export const glabel = id => D.group?.[id]?.label ?? id.replace(/_/g, ' ');
export const bshort = id => D.book?.[id]?.short ?? id;
export const btitle = id => D.book?.[id]?.title ?? id;
export const bvar = id => `--bk:var(--b${(D.book?.[id]?.i ?? 0) % 6})`;

const SG = {'+': ['↑', 'p', 'raises'], '-': ['↓', 'm', 'lowers'], '0': ['∅', 'z', 'no effect on'], '~': ['∩', 'h', 'hump-shaped effect on'], '?': ['~', 'q', 'linked to']};
export const sign = s => `<span class="sg ${SG[s]?.[1] ?? 'q'}" title="${SG[s]?.[2] ?? s}">${SG[s]?.[0] ?? s}</span>`;
export const signWord = s => SG[s]?.[2] ?? s;
export const linkHTML = (a, b, s) => `${esc(clabel(a))} ${sign(s)} ${esc(clabel(b))}`;
export const linkChip = ([a, b, s]) => `<span class="link"><b>${esc(clabel(a))}</b> ${sign(s)} <b>${esc(clabel(b))}</b></span>`;

export function workShort(w) {
  if (!w) return '';
  const first = (w.authors || '').split(',')[0].trim();
  const more = /,.*(,| and )|et al/.test(w.authors || '') ? ' et al.' : / and /.test(w.authors || '') ? ' & co' : '';
  return `${first}${more} ${w.year || ''}`.trim();
}
export const workLink = id => D.work?.[id] ? `<a href="#w-${id}" title="${esc(D.work[id].title)}">${esc(workShort(D.work[id]))}</a>` : esc(id);

// One endnote claim: where it is, what it says, its links, and the works it cites.
export function claimHTML(n, {links = true, works = true} = {}) {
  const ws = D.at?.[n.book + '|' + n.loc] || [];
  return `<li class="claim" style="${bvar(n.book)}"><span class="loc"><i>${esc(bshort(n.book))}</i> ${esc(n.loc)}</span>${esc(n.claim)}` +
    (links && n.links?.length ? `<span class="lk">${n.links.map(linkChip).join('')}</span>` : '') +
    (works && ws.length ? `<span class="by">Cites ${ws.map(workLink).join(', ')}</span>` : '') + '</li>';
}
export const claimsHTML = (notes, opts) => notes.length ? `<ul class="claimlist">${notes.map(n => claimHTML(n, opts)).join('')}</ul>` : '';
export const notesFor = keys => keys.map(([b, l]) => D.note?.[b + '|' + l]).filter(Boolean);

export const STATUS_LABEL = {'contested': 'Contested', 'failed-replication': 'Failed replication', 'retracted': 'Retracted', 'replicated': 'Replicated'};
export const WEAK = new Set(['contested', 'failed-replication', 'retracted']);
export const badge = st => STATUS_LABEL[st] ? `<span class="badge ${st}">${STATUS_LABEL[st]}</span>` : '';

export function toast(m) {
  const t = $('toast'); t.textContent = m; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => t.hidden = true, 2200);
}
export const fail = (el, e) => {
  el.innerHTML = `<p class="empty">Couldn’t load the data files (${esc(e.message)}). If you opened this file directly, run <code>python bib.py serve</code> and open the address it prints.</p>`;
};
