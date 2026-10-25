// Connections: where books meet — shared and contested links, rival explanations,
// candidate chains for new theories, works both books lean on, and gaps.
import {$, D, need, esc, clabel, glabel, sign, bshort, btitle, bvar, workLink, claimsHTML, fail} from './core.js';

export async function mount(el, args) {
  try { await need('works', 'books', 'endnotes', 'concepts', 'groups', 'theories', 'analysis'); } catch (e) { return fail(el, e); }
  const C = D.analysis.connections;
  const used = [...new Set(D.endnotes.flatMap(n => (n.links || []).flatMap(l => [l[0], l[1]])))].sort((a, b) => clabel(a).localeCompare(clabel(b)));
  el.innerHTML = `<p class="lede">Where the books meet. Every item is built from the endnote claims, so open one to see the endnotes and the works behind it.</p>
    <div class="controls" style="grid-template-columns:minmax(200px,360px) 1fr">
      <select id="focus" aria-label="Focus on a concept"><option value="">All concepts</option>${used.map(c => `<option value="${c}">${esc(clabel(c))}</option>`).join('')}</select>
      <span class="sub" style="margin:0">${C.books.length < 2 ? 'Add a second book to see cross-book connections.' : 'Pick a concept to narrow every section to it.'}</span>
    </div>
    <div id="sections"></div>`;
  $('focus').oninput = () => { location.hash = 'connections' + ($('focus').value ? '/' + $('focus').value : ''); };
  $('sections').addEventListener('toggle', fill, true);
  update(args);
}

export function update(args) {
  const f = args[0] || '';
  $('focus').value = f;
  render(f);
}

const notesOn = (a, b, books) => (D.byLink[a + '>' + b] || []).filter(n => !books || books.includes(n.book));
const has = (f, ...cs) => !f || cs.includes(f);
const books = obj => Object.entries(obj).map(([b, ss]) => `<span style="${bvar(b)}"><span class="bkdot"></span>${esc(bshort(b))} ${Object.entries(ss).map(([s, n]) => sign(s) + (n > 1 ? n : '')).join(' ')}</span>`).join(' &nbsp; ');

function section(title, sub, items, empty) {
  return `<h2>${title} <span class="sub" style="font-family:var(--sans);font-size:14px">${items.length}</span></h2><p class="sub">${sub}</p>` +
    (items.length ? items.slice(0, 40).join('') : `<p class="sub"><i>${empty}</i></p>`);
}
// Each item is a <details> filled with endnotes when opened; data-q holds what to show.
const item = (summary, why, q) => `<details class="item" data-q="${esc(JSON.stringify(q))}"><summary>${summary}${why ? `<div class="why">${why}</div>` : ''}</summary></details>`;

function fill(e) {
  const d = e.target; if (!d.matches?.('details.item') || !d.open || d.dataset.done) return;
  const q = JSON.parse(d.dataset.q); let html = '';
  for (const part of q) {
    if (part.link) {
      const ns = notesOn(part.link[0], part.link[1], part.books);
      html += `<h3>${esc(clabel(part.link[0]))} → ${esc(clabel(part.link[1]))}${part.books ? ' · ' + part.books.map(bshort).join(', ') : ''}</h3>` + claimsHTML(ns);
    }
    if (part.work) {
      for (const [b, locs] of Object.entries(part.uses)) html += `<h3 style="${bvar(b)}"><span class="bkdot"></span>${esc(btitle(b))}</h3>` + claimsHTML(locs.map(l => D.note[b + '|' + l]).filter(Boolean));
    }
  }
  d.insertAdjacentHTML('beforeend', html || '<p class="sub">Nothing to show.</p>');
  d.dataset.done = 1;
}

function render(f) {
  const C = D.analysis.connections, G = D.analysis.gaps;
  const chains = C.chains.filter(c => has(f, ...c.path)).map(c => {
    const [a, b, z] = c.path;
    return item(`${esc(clabel(a))} ${sign(c.signs[0])} ${esc(clabel(b))} ${sign(c.signs[1])} ${esc(clabel(z))}`,
      `${esc(bshort(c.books[0]))} links the first pair, ${esc(bshort(c.books[1]))} the second; neither states the end-to-end link (implied: ${esc(clabel(a))} ${sign(c.sign)} ${esc(clabel(z))}). Weakest leg: ${c.support} endnote${c.support === 1 ? '' : 's'}.`,
      [{link: [a, b], books: [c.books[0]]}, {link: [b, z], books: [c.books[1]]}]);
  });
  const rivals = C.rivals.filter(r => has(f, r.outcome, ...Object.values(r.only).flat(), ...r.shared)).map(r => item(
    `What drives <b>${esc(clabel(r.outcome))}</b>?`,
    Object.entries(r.only).map(([b, cs]) => `<span style="${bvar(b)}"><span class="bkdot"></span><b>${esc(bshort(b))}</b>: ${cs.slice(0, 5).map(c => esc(clabel(c))).join(', ')}${cs.length > 5 ? '…' : ''}</span>`).join('<br>') +
      (r.shared.length ? `<br>Both: ${r.shared.map(c => esc(clabel(c))).join(', ')}` : ''),
    Object.entries(r.only).flatMap(([b, cs]) => cs.slice(0, 5).map(c => ({link: [c, r.outcome], books: [b]})))));
  const contested = C.contested.filter(c => has(f, c.from, c.to)).map(c => item(
    `${esc(clabel(c.from))} → ${esc(clabel(c.to))}`, books(c.books), [{link: [c.from, c.to]}]));
  const shared = C.shared.filter(c => has(f, c.from, c.to)).map(c => item(
    `${esc(clabel(c.from))} → ${esc(clabel(c.to))}`, books(c.books), [{link: [c.from, c.to]}]));
  const works = C.shared_works.filter(w => !f || Object.entries(w.books).some(([b, locs]) => locs.some(l => (D.note[b + '|' + l]?.links || []).some(k => k[0] === f || k[1] === f)))).map(w => item(
    workLink(w.id) + ` <span class="sub">${esc(D.work[w.id]?.title || '')}</span>`,
    Object.entries(w.books).map(([b, l]) => `${esc(bshort(b))}: ${l.length} endnote${l.length === 1 ? '' : 's'}`).join(' · '),
    [{work: w.id, uses: w.books}]));
  const fragile = G.fragile_steps.filter(s => has(f, s.step[0], s.step[1])).map(s => {
    const t = D.theories.find(x => x.id === s.theory);
    return `<div class="item"><a href="#theories/${s.theory}">${esc(t?.title || s.theory)}</a>: ${esc(clabel(s.step[0]))} ${sign(s.step[2])} ${esc(clabel(s.step[1]))}<div class="why">${s.works.length ? `Rests on ${s.works.map(workLink).join(', ')}` : 'No endnote supports this step'}</div></div>`;
  });
  const oneBook = (rows, lab) => rows.map(x => `<span class="chip" style="cursor:default">${esc(lab(x.id))} · ${Object.entries(x.books).map(([b, n]) => `${esc(bshort(b))} ${n}`).join('')}</span>`).join(' ');
  const bridges = C.bridges.filter(x => has(f, x.concept)).slice(0, 40).map(x => `<span class="chip" style="cursor:default">${esc(clabel(x.concept))} · ${Object.entries(x.books).map(([b, n]) => `${esc(bshort(b))} ${n}`).join(' / ')}</span>`).join(' ');

  $('sections').innerHTML =
    section('Candidate chains', 'Two links from different books that join end to end, where no book states the implied link. Starting points for a new theory.', chains, 'No chains through this concept.') +
    section('Rival explanations', 'The same outcome, explained by different causes in different books.', rivals, 'No rival explanations here.') +
    section('Contested links', 'Links stated with opposite signs, within or across books.', contested, 'No conflicting links.') +
    section('Shared links', 'Links more than one book states.', shared, 'No links shared across books.') +
    section('Works both books lean on', 'Works cited in more than one book, with what each book uses them for.', works, 'No shared works with linked endnotes.') +
    `<h2>Gaps</h2><p class="sub">Where the evidence is thin or one-sided.</p><h3>Theory steps with one source or none</h3>${fragile.join('') || '<p class="sub"><i>None.</i></p>'}` +
    (!f ? `<h3>Groups only one book discusses</h3><p>${oneBook(G.groups_one_book, glabel) || '<i class="sub">None.</i>'}</p>` +
      `<h3>Concepts only one book uses</h3><p>${oneBook(G.concepts_one_book, clabel) || '<i class="sub">None.</i>'}</p>` : '') +
    `<h2>Shared vocabulary</h2><p class="sub">Concepts that appear in more than one book's links, with how many endnotes use them.</p><p>${bridges || '<i class="sub">None.</i>'}</p>`;
}
