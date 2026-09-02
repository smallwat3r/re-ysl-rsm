// The one mutable store, every module reads and writes the same state here.
export const store = {
  state: {}, // last snapshot pushed by the server over SSE
  names: [], // loaded cartridge names, in slot order
  colours: {}, // cartridge name -> [r, g, b]
  labels: {}, // cartridge name -> printed short code, e.g. VC_220 -> O2
  shares: [0, 0, 0], // current mix, one share per slot
  favs: [], // saved favourites from the backend
};

// The short code printed on the physical tube, falling back to the full name.
export const label = (name) => store.labels[name] || name;
