// Entry point: renders the snapshots the server pushes over SSE, and bridges
// the module functions to the inline on* handlers in the HTML.
import { $, esc, rgb, ts, post } from "./util.js";
import { store, label } from "./store.js";
import { build, update, pick, slide, prime, dispense, wheelpick } from "./mixer.js";
import { loadFavs, renderFavs, saveFav, loadFav, deleteFav } from "./favs.js";

const toggle = () => post(store.state.connected ? "/disconnect" : "/connect");

function render(s) {
  store.state = s;
  if (s.colours) store.colours = s.colours; // the full 12-colour table, present even when idle
  if (s.labels) store.labels = s.labels; // full name -> printed short code
  if (s.max_total) $("amount").max = s.max_total; // the backend safety cap, protocol.MAX_TOTAL
  renderHeader(s);
  renderPanels(s);
  renderCartridges(s);
  if (store.names.length) update();
  else {
    $("dispense").disabled = true;
    $("save").disabled = true;
  }
  renderFavs();
}

// The connection pill, its buttons, the battery gauge and the error line.
function renderHeader(s) {
  $("pill").className = "pill" + (s.connected ? " on" : "");
  $("pilltext").textContent = s.busy ? "Working..." : s.connected ? "Connected" : "Disconnected";
  $("toggle").textContent = s.connected ? "Disconnect" : "Connect";
  $("toggle").disabled = s.busy;
  $("refresh").disabled = s.busy || !s.connected;
  $("err").textContent = s.error || "";
  const showBatt = s.connected && s.battery != null;
  $("batt").hidden = !showBatt;
  if (showBatt) {
    $("battbar").style.width = s.battery + "%";
    $("battpct").textContent = s.battery + "%" + (s.charging ? ", charging" : "");
  }
}

// Everything below the header shows only while connected.
function renderPanels(s) {
  $("hello").hidden = s.connected || s.busy; // idle + disconnected only
  $("frames").hidden = !s.connected;
  $("cartsPanel").hidden = !s.connected;
  $("dispensePanel").hidden = !s.connected;
  $("favsPanel").hidden = !s.connected;
  $("amount").disabled = s.busy || !s.connected;
  $("log").textContent = (s.log || []).join("\n");
  if (s.last_dispense) $("last").textContent = `Last dispense ${ts(s.last_dispense.at)}`;
}

// The cartridge cards, a change in the loaded set rebuilds the mix panel.
function renderCartridges(s) {
  if (!s.cartridges) {
    if (!store.names.length) skeleton();
    return;
  }
  $("cards").innerHTML = s.cartridges.map((c, i) => cartCard(s, c, i)).join("");
  if (store.names.join() !== s.cartridges.map((c) => c.name).join()) {
    store.names = s.cartridges.map((c) => c.name);
    store.colours = s.colours;
    build();
  }
}

function cartCard(s, c, i) {
  const u = (s.usage || [])[i];
  // The official app's rule (BeamLipsCartridgesExtensionKt.calculateVolume):
  // an unopened tube is 100%, otherwise trunc(remaining / usable * 100).
  const left = u ? (u.opened ? u.remaining_ml : c.usable_ml) : null;
  const pct =
    left == null || !c.usable_ml
      ? null
      : Math.max(0, Math.min(100, Math.floor((left / c.usable_ml) * 100)));
  // Expiry: shelf date from production, or the opened-life deadline from
  // usage, whichever comes first. Both are days since the epoch, 0 = unknown.
  const expires = Math.min(c.expires || Infinity, u && u.opened ? u.ends || Infinity : Infinity);
  const days = expires - Math.floor(Date.now() / 864e5);
  let expiry = "";
  if (isFinite(expires)) {
    const date = new Date(expires * 864e5).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
    const cls = days < 0 ? ' class="bad"' : days <= 30 ? ' class="warn"' : "";
    expiry = `<small><span${cls} title="${days < 0 ? "expired" : "expires"} ${date}">exp ${date}</span></small>`;
  }
  let fill = "";
  if (pct != null) {
    const bar = `<span class="bar" title="${pct}% of ${c.usable_ml} mL usable"><i style="width:${pct}%"></i></span>`;
    fill = `<small>${left.toFixed(1)} mL left</small><span class="fill">${bar}<small>${pct}%</small></span>`;
  }
  return `<div class="card cart"><span class="dot" style="background:${rgb(s.colours[c.name])}"></span>
      <div style="flex:1"><b>${esc(label(c.name))}</b><small>batch ${esc(c.batch)}</small>${expiry}${fill}</div>
      <button class="prime" onclick="prime(${i})" ${s.busy ? "disabled" : ""}
        title="Prime this tube: dispense a little of just this cartridge">Prime</button></div>`;
}

function skeleton() {
  $("cards").innerHTML = [0, 1, 2]
    .map(
      () => `<div class="card cart"><span class="dot skel"></span>
    <div style="flex:1"><b class="skel">cartridge</b><small class="skel">batch 000000</small></div></div>`,
    )
    .join("");
  $("preview").className = "preview skel";
  $("marker").hidden = true; // nothing picked yet, no wheel to sit on
  $("palette").innerHTML = Array(15).fill('<div class="sw skel"></div>').join("");
  $("sliders").innerHTML = [0, 1, 2]
    .map(
      () => `<div class="slider"><span class="dot skel"></span><span class="skel">cartridge</span>
    <input type="range" min="0" max="100" value="0" disabled><span class="skel">0%</span><span class="skel">0 µL</span></div>`,
    )
    .join("");
}

// The inline on* handlers, in index.html and in generated cards, resolve names
// on window; modules keep theirs private, so expose exactly what they call.
Object.assign(window, { $, post, toggle, update, wheelpick, saveFav, dispense, pick, slide, prime, loadFav, deleteFav });

loadFavs();
new EventSource("/events").onmessage = (e) => render(JSON.parse(e.data));
