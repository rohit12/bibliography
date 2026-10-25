// Router: each view is a module exporting mount(el, args) and optionally update(args).
// To add a view (e.g. a dataset visualisation), write js/<name>.js and list it here.
import {$, D, need, esc} from './core.js';

const VIEWS = [
  {id: 'works', label: 'Works', load: () => import('./works.js')},
  {id: 'theories', label: 'Theories & pillars', load: () => import('./theories.js')},
  {id: 'connections', label: 'Connections', load: () => import('./connections.js')},
  {id: 'compare', label: 'Compare', load: () => import('./compare.js')},
];

let current = null, mod = null;

function parse() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (!h || h.startsWith('w-') || h.startsWith('L-')) return {view: 'works', args: [], hash: h};
  const [view, ...args] = h.split('/');
  return VIEWS.some(v => v.id === view) ? {view, args, hash: h} : {view: 'works', args: [], hash: h};
}

async function route() {
  const {view, args, hash} = parse();
  document.querySelectorAll('#tabs a').forEach(a => a.dataset.view === view ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'));
  if (view === current && mod?.update) return mod.update(args, hash);
  current = view;
  $('view').innerHTML = '<p class="loading">Loading…</p>';
  mod = await VIEWS.find(v => v.id === view).load();
  await mod.mount($('view'), args, hash);
}

$('tabs').innerHTML = VIEWS.map(v => `<a href="#${v.id}" data-view="${v.id}">${esc(v.label)}</a>`).join('');
window.addEventListener('hashchange', route);
route();
need('meta').then(() => {
  $('gen').textContent = 'Updated ' + new Date(D.meta.generated).toLocaleDateString(undefined, {year: 'numeric', month: 'short', day: 'numeric'});
}).catch(() => {});
