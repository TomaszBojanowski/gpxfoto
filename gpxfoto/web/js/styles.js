// The styles of the map. The light one is OpenFreeMap's Liberty; the dark
// one is made from it here. OpenFreeMap's own dark style shows neither
// the land cover nor the paths, so its colours are not used.

export const TILES = "https://tiles.openfreemap.org";
const STYLE_URL = TILES + "/styles/liberty";
const STYLE_TIMEOUT = 8000;          // ms

let base = null;                     // the promise of Liberty, while it is wanted

// Liberty, as fetched once; rejected without the map server (no internet)
export function loadBaseStyle() {
  if (!base) {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), STYLE_TIMEOUT);
    base = fetch(STYLE_URL, { signal: abort.signal, credentials: "omit" })
      .then((response) => {
        if (!response.ok) {
          throw new Error(response.statusText);
        }
        return response.json();
      })
      .finally(() => clearTimeout(timer));
    // A later attempt tries again
    base.catch(() => { base = null; });
  }
  return base;
}

// The tracks and photos alone, on a plain background
export function plainStyle(dark) {
  return {
    version: 8,
    sources: {},
    layers: [{ id: "background", type: "background",
      paint: { "background-color": dark ? "#26292e" : "#eceff1" } }],
  };
}

// --- colours ---------------------------------------------------------------

function clamp(value, low = 0, high = 1) {
  return Math.min(high, Math.max(low, value));
}

// {h, s, l, a} of a colour in the forms the style uses, or null
export function parseColor(text) {
  const value = text.trim().toLowerCase();
  let match = /^#([0-9a-f]{3,8})$/.exec(value);
  if (match) {
    let hex = match[1];
    if (hex.length === 3 || hex.length === 4) {
      hex = [...hex].map((c) => c + c).join("");
    }
    if (hex.length !== 6 && hex.length !== 8) {
      return null;
    }
    const channel = (k) => parseInt(hex.slice(2 * k, 2 * k + 2), 16);
    return fromRgb(channel(0), channel(1), channel(2), hex.length === 8 ? channel(3) / 255 : 1);
  }
  match = /^(rgba?|hsla?)\(([^)]*)\)$/.exec(value);
  if (!match) {
    return null;
  }
  const parts = match[2].split(/[\s,/]+/).filter(Boolean);
  if (parts.length < 3 || parts.length > 4) {
    return null;
  }
  const number = (part, scale) => (part.endsWith("%")
    ? parseFloat(part) / 100 * scale : parseFloat(part));
  const alpha = parts.length === 4 ? number(parts[3], 1) : 1;
  if (match[1].startsWith("rgb")) {
    const [r, g, b] = parts.slice(0, 3).map((part) => number(part, 255));
    return [r, g, b, alpha].some(Number.isNaN) ? null : fromRgb(r, g, b, alpha);
  }
  const color = { h: parseFloat(parts[0]), s: parseFloat(parts[1]) / 100,
    l: parseFloat(parts[2]) / 100, a: alpha };
  return Object.values(color).some(Number.isNaN) ? null : color;
}

function fromRgb(r, g, b, a) {
  r /= 255; g /= 255; b /= 255;
  const high = Math.max(r, g, b);
  const low = Math.min(r, g, b);
  const l = (high + low) / 2;
  let h = 0;
  let s = 0;
  if (high !== low) {
    const d = high - low;
    s = l > 0.5 ? d / (2 - high - low) : d / (high + low);
    if (high === r) {
      h = (g - b) / d + (g < b ? 6 : 0);
    } else if (high === g) {
      h = (b - r) / d + 2;
    } else {
      h = (r - g) / d + 4;
    }
    h *= 60;
  }
  return { h, s, l, a };
}

export function formatColor({ h, s, l, a }) {
  const round = (value, digits) => Number(value.toFixed(digits));
  return `hsla(${round(h, 1)},${round(100 * clamp(s), 1)}%,${round(100 * clamp(l), 1)}%,`
    + `${round(clamp(a), 3)})`;
}

// How the dark style changes each kind of colour: areas turn dark, lines
// and labels stay lighter than the areas under them, and the halos of
// labels turn dark. lightness(l) is the new lightness; the colourfulness
// (chroma) is kept, times chroma, so that the nearly white land does not
// turn brown and the pale yellow roads do not turn gold.
const DARKEN = {
  area: { lightness: (l) => 0.16 + 0.42 * (1 - l), chroma: 0.8 },
  line: { lightness: (l) => 0.12 + 0.42 * l, chroma: 0.9 },
  text: { lightness: (l) => 0.92 - 0.55 * l, chroma: 1 },
  halo: { lightness: (l) => 0.12 + 0.12 * (1 - l), chroma: 0.5 },
};

function darken(color, { lightness, chroma }) {
  const span = (l) => 1 - Math.abs(2 * l - 1);
  const l = clamp(lightness(color.l));
  const c = span(color.l) * color.s * chroma;
  return { ...color, l, s: span(l) ? clamp(c / span(l)) : 0 };
}

const KINDS = {
  "background-color": "area", "fill-color": "area", "fill-outline-color": "area",
  "fill-extrusion-color": "area", "line-color": "line", "circle-color": "line",
  "circle-stroke-color": "area", "text-color": "text", "icon-color": "text",
  "text-halo-color": "halo", "icon-halo-color": "halo",
};

// Every colour in a paint value, which may be an expression or a function
function mapColors(value, how) {
  if (typeof value === "string") {
    const color = parseColor(value);
    return color ? formatColor(darken(color, how)) : value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => mapColors(item, how));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) =>
      [key, mapColors(item, how)]));
  }
  return value;
}

export function darkColor(text, kind) {
  return mapColors(text, DARKEN[kind]);
}

// A dark version of a light style
export function darkStyle(light) {
  const style = structuredClone(light);
  for (const layer of style.layers) {
    const paint = layer.paint || (layer.paint = {});
    for (const [property, value] of Object.entries(paint)) {
      if (KINDS[property]) {
        paint[property] = mapColors(value, DARKEN[KINDS[property]]);
      }
    }
    // Black, the default colour of labels, would vanish on the dark land
    if (layer.type === "symbol" && layer.layout && layer.layout["text-field"]
        && !("text-color" in paint)) {
      paint["text-color"] = mapColors("#000000", DARKEN.text);
    }
    // Patterns are pictures, which stay light: they are only hinted at
    if ("fill-pattern" in paint && typeof (paint["fill-opacity"] ?? 1) === "number") {
      paint["fill-opacity"] = 0.35 * (paint["fill-opacity"] ?? 1);
    }
    if (layer.type === "raster") {
      paint["raster-brightness-max"] = 0.35;
      paint["raster-saturation"] = -0.4;
    }
  }
  return style;
}
