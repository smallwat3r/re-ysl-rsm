// The mix panel: colour maths, the wheel, sliders, palette, prime and dispense.
import { $, esc, rgb, ask, post, GREY, EMPTY_MIX } from "./util.js";
import { store, label } from "./store.js";

// The [r, g, b] of the cartridge in slot i, grey until its colour is known.
const colourOf = (i) => store.colours[store.names[i]] || GREY;

export function mix(w) {
  const t = w.reduce((a, b) => a + b, 0);
  if (!t) return EMPTY_MIX;
  return rgb(
    [0, 1, 2].map((ch) => Math.round(w.reduce((acc, wi, i) => acc + wi * colourOf(i)[ch], 0) / t)),
  );
}

export function amounts() {
  const total = Math.max(0, parseInt($("amount").value) || 0),
    t = store.shares.reduce((a, b) => a + b, 0);
  return t ? store.shares.map((s) => Math.round((s / t) * total)) : [0, 0, 0];
}

export function update() {
  const { state, names, shares } = store;
  const amt = $("amount");
  if (amt.max && +amt.value > +amt.max) amt.value = amt.max; // typed input ignores the max attr
  const a = amounts(),
    t = shares.reduce((x, y) => x + y, 0);
  $("preview").className = "preview";
  $("preview").style.background = mix(shares);
  // Park the marker on the wheel at the point whose barycentric weights are the
  // current shares (the affine mix of the anchors, always inside the disc).
  const mk = $("marker");
  mk.hidden = false;
  mk.style.background = mix(shares);
  let px = 0,
    py = 0;
  shares.forEach((s, i) => {
    px += (s * ANCH[i][0]) / (t || 1);
    py += (s * ANCH[i][1]) / (t || 1);
  });
  const cv = $("wheel");
  mk.style.left = ((px + 1) / 2) * 100 + "%";
  mk.style.top = (((py + 1) * (cv.width / 2)) / cv.height) * 100 + "%";
  names.forEach((_, i) => {
    $("s" + i).value = shares[i];
    $("p" + i).textContent = (t ? Math.round((shares[i] / t) * 100) : 0) + "%";
    $("u" + i).textContent = a[i] + " µL";
  });
  for (const el of document.querySelectorAll(".sw"))
    el.classList.toggle("sel", el.dataset.w === JSON.stringify(shares));
  $("amountml").textContent = "= " + (Math.max(0, parseInt(amt.value) || 0) / 1000).toFixed(3) + " mL";
  $("dispense").disabled = state.busy || !state.connected || !t;
  $("save").disabled = state.busy || !state.connected || !t;
}

export function pick(w) {
  store.shares = w.map((v) => Math.round(v * 100));
  update();
}

export function slide(i, v) {
  store.shares[i] = parseInt(v);
  update();
}

// calibration knob, tune to your tubes. The app's 0.1f (=100) is too low, product
// often doesn't reach the nozzle
const PRIME_AMOUNT = 400;
export function prime(i) {
  const a = [0, 0, 0];
  a[i] = PRIME_AMOUNT;
  ask(
    `Prime ${store.names[i]}? It dispenses a little to fill the tube.`,
    rgb(store.colours[store.names[i]]),
  ).then((ok) => ok && post("/dispense", { amounts: a }));
}

export function dispense() {
  const a = amounts();
  ask(
    `Dispense ${store.names.map((n, i) => `${n} ${a[i]}`).join(", ")}?`,
    mix(store.shares),
  ).then((ok) => ok && post("/dispense", { amounts: a }));
}

// Anchors 120 deg apart on the unit circle, one per cartridge. A point's blend is
// its barycentric weights in that triangle, so the triangle IS the achievable
// gamut, and the canvas is sized to its bounding box (y from -1 to 0.5).
const ANCH = [
  [0, -1],
  [0.866, 0.5],
  [-0.866, 0.5],
];

// Raw barycentric weights, negative outside the triangle (callers clamp).
function weights(dx, dy) {
  const [[x0, y0], [x1, y1], [x2, y2]] = ANCH,
    den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
  const w0 = ((y1 - y2) * (dx - x2) + (x2 - x1) * (dy - y2)) / den;
  const w1 = ((y2 - y0) * (dx - x2) + (x0 - x2) * (dy - y2)) / den;
  return [w0, w1, 1 - w0 - w1];
}

const FEATHER = 0.008; // ~2px of edge anti-aliasing in barycentric units
function drawwheel() {
  const cv = $("wheel"),
    n = cv.width,
    h = cv.height,
    r = n / 2,
    ctx = cv.getContext("2d"),
    img = ctx.createImageData(n, h);
  for (let y = 0; y < h; y++)
    for (let x = 0; x < n; x++) {
      const raw = weights((x - r) / r, (y - r) / r),
        minw = Math.min(...raw);
      if (minw < -FEATHER) continue; // outside the triangle, stays transparent
      const w = raw.map((v) => Math.max(0, v)),
        t = w[0] + w[1] + w[2],
        o = (y * n + x) * 4;
      [0, 1, 2].forEach(
        (ch) =>
          (img.data[o + ch] = Math.round(w.reduce((a, wi, i) => a + wi * colourOf(i)[ch], 0) / t)),
      );
      img.data[o + 3] = minw >= FEATHER ? 255 : Math.round(((minw + FEATHER) / (2 * FEATHER)) * 255);
    }
  ctx.putImageData(img, 0, 0);
  // stroke the gamut edge in the darkest cartridge colour, crisps the feathering
  const cs = store.names.map((nm) => store.colours[nm] || GREY),
    dark = cs.reduce((a, c) => (c[0] + c[1] + c[2] < a[0] + a[1] + a[2] ? c : a));
  ctx.beginPath();
  ANCH.forEach(([ax, ay], i) => ctx[i ? "lineTo" : "moveTo"](r + ax * r, r + ay * r));
  ctx.closePath();
  ctx.strokeStyle = rgb(dark);
  ctx.lineWidth = 2;
  ctx.stroke();
}

export function wheelpick(e) {
  const cv = $("wheel"),
    b = cv.getBoundingClientRect(),
    r = cv.width / 2;
  // Keep the drag alive when the finger wanders off the canvas; a point outside
  // the triangle clamps to the nearest edge blend, so overshoot still picks.
  if (e.type === "pointerdown") cv.setPointerCapture(e.pointerId);
  const dx = ((e.clientX - b.left) * cv.width) / b.width / r - 1,
    dy = ((e.clientY - b.top) * cv.height) / b.height / r - 1;
  const w = weights(dx, dy).map((v) => Math.max(0, v)),
    t = w[0] + w[1] + w[2];
  store.shares = w.map((v) => Math.round((v / t) * 100));
  update();
}

export function build() {
  const { names, colours } = store;
  const steps = [0, 0.25, 0.5, 0.75, 1],
    out = [];
  for (const x of steps)
    for (const y of steps) if (x + y <= 1.001) out.push([x, y, +(1 - x - y).toFixed(2)]);
  $("palette").innerHTML = out
    .map((w) => {
      const pcts = JSON.stringify(w.map((v) => Math.round(v * 100)));
      const title = w.map((wi, i) => `${esc(label(names[i]))} ${Math.round(wi * 100)}%`).join(", ");
      return `<button type="button" class="sw"
        style="background:${mix(w)}"
        data-w='${pcts}'
        onclick='pick(${JSON.stringify(w)})'
        title="${title}"></button>`;
    })
    .join("");
  $("sliders").innerHTML = names
    .map(
      (n, i) => `<div class="slider">
        <span class="dot" style="background:${rgb(colours[n])}"></span>
        <span>${esc(label(n))}</span>
        <input id="s${i}" type="range" min="0" max="100" value="0" oninput="slide(${i}, this.value)">
        <span id="p${i}"></span>
        <span id="u${i}"></span></div>`,
    )
    .join("");
  drawwheel();
  update();
}
