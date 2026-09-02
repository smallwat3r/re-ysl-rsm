// DOM, formatting and server helpers shared by every module.
export const $ = (id) => document.getElementById(id);

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
// Escape device-supplied strings before they go into innerHTML (a spoofed device
// could otherwise inject markup via a cartridge name/batch/serial field).
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

export const ts = (t) => (t ? new Date(t * 1000).toLocaleString() : "");
export const rgb = (c) => (c ? `rgb(${c.join(",")})` : "#ccc");

export const GREY = [128, 128, 128]; // fallback for a cartridge whose colour isn't known
export const EMPTY_MIX = "#e6dfdc"; // swatch colour when nothing is selected

export async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) $("err").textContent = (await r.json()).error;
}

// In-page confirmation using the native <dialog>, resolves true on Confirm.
// A colour swatch is optional, Esc / Cancel resolve false.
export function ask(message, colour) {
  return new Promise((resolve) => {
    const d = $("confirm");
    $("confirmMsg").textContent = message;
    const sw = $("confirmSwatch");
    sw.hidden = !colour; // no swatch for text-only prompts like delete
    sw.style.background = colour || "transparent";
    d.returnValue = "";
    d.showModal();
    d.onclose = () => resolve(d.returnValue === "ok");
  });
}
