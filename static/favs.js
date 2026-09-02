// Favourites: saved recipes, their cards, and loading one back into the mix.
import { $, esc, rgb, ask, post, GREY, EMPTY_MIX } from "./util.js";
import { store, label } from "./store.js";
import { mix, update } from "./mixer.js";

// A favourite is {code: share}, proportions only, the amount is chosen when
// dispensing. Its colour is the share-weighted blend of the cartridge colours
// (known for all 12, so it renders even when not loaded).
function recipeColor(recipe) {
  const e = Object.entries(recipe),
    t = e.reduce((s, [, a]) => s + a, 0);
  if (!t) return EMPTY_MIX;
  return rgb(
    [0, 1, 2].map((ch) =>
      Math.round(e.reduce((acc, [c, a]) => acc + a * (store.colours[c] || GREY)[ch], 0) / t),
    ),
  );
}

export async function loadFavs() {
  try {
    store.favs = (await (await fetch("/favourites")).json()).favourites || [];
  } catch {
    store.favs = [];
  }
  renderFavs();
}

export function renderFavs() {
  const { state, names, favs } = store;
  if (!favs.length) {
    $("favs").innerHTML = '<div class="card muted">No favourites yet. Make a blend and press Save.</div>';
    return;
  }
  const cards = favs.map((f) => {
      const parts = Object.entries(f.recipe),
        missing = parts.map(([c]) => c).filter((c) => !names.includes(c)),
        total = parts.reduce((s, [, a]) => s + a, 0),
        ready = state.connected && !state.busy && !missing.length;
      const chips = parts
        .map(([c, a]) => {
          const miss = state.connected && !names.includes(c); // not loaded, flag it red
          const colour = rgb(store.colours[c] || GREY);
          const dot = `<span class="minidot" style="background:${colour}"></span>`;
          const pct = Math.round((a / total) * 100);
          return `<span class="chip${miss ? " miss" : ""}">${dot}${esc(label(c))} ${pct}%</span>`;
        })
        .join("");
      const body = ready ? `onclick="loadFav(${f.id})" title="Load into the mix"` : "disabled";
      const html = `<div class="card fav${ready ? " ready" : " off"}">
      <button type="button" class="favbody" ${body}>
      <span class="dot" style="background:${recipeColor(f.recipe)}"></span>
      <div class="favtext"><b>${esc(f.name)}</b><div class="favmix">${chips}</div></div></button>
      <button class="del" onclick="deleteFav(${f.id})" title="Delete"
        aria-label="Delete ${esc(f.name)}">×</button></div>`;
      return { ready, html };
  });
  // Dispensable ones first (stable, so each group stays alphabetical), with a
  // heading over the rest only when both groups exist.
  const ready = cards.filter((c) => c.ready).map((c) => c.html),
    rest = cards.filter((c) => !c.ready).map((c) => c.html),
    sep = ready.length && rest.length ? '<h3 class="favsep">Needs other cartridges</h3>' : "";
  $("favs").innerHTML = ready.join("") + sep + rest.join("");
}

export function saveFav() {
  const recipe = {};
  store.names.forEach((c, i) => {
    if (store.shares[i] > 0) recipe[c] = store.shares[i];
  });
  if (!Object.keys(recipe).length) return;
  const d = $("savedlg");
  $("favname").value = "";
  $("saveSwatch").style.background = mix(store.shares);
  d.returnValue = "";
  d.showModal();
  $("favname").focus();
  d.onclose = () => {
    const name = $("favname").value.trim();
    if (d.returnValue === "ok" && name) post("/favourites", { name, recipe }).then(loadFavs);
  };
}

// Load a favourite into the sliders so it can be tweaked, dispensed or re-saved.
// Recipe shares map onto the current slots, the amount input is left alone (old
// favourites stored device units, normalising handles both).
export function loadFav(id) {
  const f = store.favs.find((x) => x.id === id);
  if (!f) return;
  const a = store.names.map((c) => f.recipe[c] || 0),
    total = a.reduce((x, y) => x + y, 0);
  store.shares = a.map((x) => (total ? Math.round((x / total) * 100) : 0));
  update();
  $("dispensePanel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

export function deleteFav(id) {
  const f = store.favs.find((x) => x.id === id);
  ask(`Delete favourite "${f ? f.name : ""}"?`).then(
    (ok) => ok && post("/favourites/delete", { id }).then(loadFavs),
  );
}
