// The map: the tracks, the photos as round thumbnails, and the selected photo.

import {
  AttributionControl, LngLatBounds, Map as MapLibreMap, NavigationControl, ScaleControl,
} from "../vendor/maplibre-gl/maplibre-gl.mjs";
import { fanOffsets } from "./fan.js";
import { _, currentLocale } from "./i18n.js";
import { darkStyle, loadBaseStyle, plainStyle } from "./styles.js";

const THUMB = 64;                    // px of a thumbnail image, drawn at half size
const FONT = ["Noto Sans Bold"];
const MAX_ZOOM = 20;
// Photos closer than this on the screen are drawn as one group
const CLUSTER_RADIUS = 40;           // px
// A group of photos that only parts beyond this zoom, or never, as for
// photos taken at one place, is fanned out around its place instead
const FAN_ZOOM = 17;

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

// The colours drawn on the map follow the map's style, not the page's
const PALETTES = {
  light: { track: "#e2561c", casing: "#ffffff", accent: "#2563c9", matched: "#2a9d55",
    stop: "#2563c9", skipped: "#8a9099", located: "#b8860b", placeholder: "#c9ced4" },
  dark: { track: "#ff8a3d", casing: "#1e2024", accent: "#6ea0f5", matched: "#4cc47a",
    stop: "#6ea0f5", skipped: "#8f959e", located: "#e0b33a", placeholder: "#4a4f57" },
};

const EMPTY = { type: "FeatureCollection", features: [] };

function duration(ms) {
  return reducedMotion.matches ? 0 : ms;
}

function icon(paths) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of paths) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

const ICONS = {
  // A track between the corners of a frame
  fit: ["M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5",
    "M7.5 15.5c2-4 3.5 1 5.5-3s2.5-1.5 3.5-3.5"],
  // A crescent moon
  dark: ["M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"],
};

// Buttons in the style of the map's own, under the zoom buttons
class ButtonsControl {
  constructor(buttons) {
    this.buttons = buttons;
  }

  onAdd() {
    this.container = document.createElement("div");
    this.container.className = "maplibregl-ctrl maplibregl-ctrl-group gpxfoto-ctrl";
    for (const { element } of this.buttons) {
      this.container.append(element);
    }
    return this.container;
  }

  onRemove() {
    this.container.remove();
  }
}

function controlButton(name, onClick) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = "gpxfoto-" + name;
  element.append(icon(ICONS[name]));
  element.addEventListener("click", onClick);
  return { element };
}

export class PhotoMap {
  // thumbnail(generation, id) -> Promise of an ImageBitmap-like or null;
  // onSelect(id) when a photo on the map is clicked; padding() -> the
  // margins of the map the panels cover, {top, bottom, left, right} in px;
  // onFit() for the button that shows the whole track; onStyle(name) for
  // the button that switches between the light and the dark map
  constructor(container, { thumbnail, onSelect, padding, onFit, onStyle }) {
    this.thumbnail = thumbnail;
    this.onSelect = onSelect;
    this.padding = padding;
    this.tracks = EMPTY;
    this.photos = EMPTY;
    this.selected = -1;
    this.styleName = "light";
    this.styleToken = 0;
    this.fan = null;                 // {clusterId, ids} of the photos fanned out
    this.revealToken = 0;
    this.map = new MapLibreMap({
      container,
      style: plainStyle(false),
      center: [15, 50],
      zoom: 3,
      maxZoom: MAX_ZOOM,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      locale: {
        "NavigationControl.ZoomIn": _("Zoom in"),
        "NavigationControl.ZoomOut": _("Zoom out"),
        "AttributionControl.ToggleAttribution": _("Show or hide the sources of the map"),
        "Map.Title": _("Map"),
      },
    });
    this.map.touchZoomRotate.disableRotation();
    this.map.addControl(new AttributionControl({ compact: true }), "bottom-right");
    this.map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    this.fitButton = controlButton("fit", () => onFit());
    this.darkButton = controlButton("dark", () =>
      onStyle(this.styleName === "dark" ? "light" : "dark"));
    this.map.addControl(new ButtonsControl([this.fitButton, this.darkButton]), "top-right");
    this.map.addControl(new ScaleControl(), "bottom-right");
    this.map.setMissingStyleImageResolver((name) => this.addThumbnail(name));
    this.map.on("style.load", () => this.addLayers());
    for (const layer of ["photo-thumbs", "photo-rings", "fan-thumbs", "fan-rings"]) {
      this.map.on("click", layer, (event) => this.onSelect(event.features[0].properties.id));
      this.map.on("mouseenter", layer, () => { this.map.getCanvas().style.cursor = "pointer"; });
      this.map.on("mouseleave", layer, () => { this.map.getCanvas().style.cursor = ""; });
    }
    this.map.on("mouseenter", "clusters", () => { this.map.getCanvas().style.cursor = "pointer"; });
    this.map.on("mouseleave", "clusters", () => { this.map.getCanvas().style.cursor = ""; });
    this.map.on("click", "clusters", (event) => this.openCluster(event.features[0]));
    this.map.on("click", (event) => {
      // A click beside the fanned-out photos folds them back
      if (this.fan && !this.map.queryRenderedFeatures(event.point, {
        layers: ["fan-thumbs", "fan-rings", "clusters"] }).length) {
        this.closeFan();
      }
    });
    // The fan is laid out in px of the zoom it was opened at
    this.map.on("zoomstart", () => this.closeFan());
    this.updateTexts();
  }

  // The texts of the buttons, once the translations are there
  updateTexts() {
    const fit = _("Show the whole track");
    this.fitButton.element.title = fit;
    this.fitButton.element.setAttribute("aria-label", fit);
    const dark = _("Dark map");
    this.darkButton.element.title = dark;
    this.darkButton.element.setAttribute("aria-label", dark);
    this.darkButton.element.setAttribute("aria-pressed", String(this.styleName === "dark"));
  }

  get palette() {
    return PALETTES[this.styleName];
  }

  // Switch to the light or the dark map; without the map server, the
  // tracks and photos are drawn on a plain background
  async setStyleName(name) {
    this.styleName = name === "dark" ? "dark" : "light";
    this.darkButton.element.setAttribute("aria-pressed", String(this.styleName === "dark"));
    const token = ++this.styleToken;
    let style;
    try {
      const light = await loadBaseStyle();
      style = this.styleName === "dark" ? darkStyle(light) : structuredClone(light);
    } catch (error) {
      style = plainStyle(this.styleName === "dark");
    }
    if (token === this.styleToken) {
      // The thumbnails are drawn again on the new style's placeholder colour
      this.map.setStyle(style, { diff: false });
    }
  }

  addLayers() {
    const map = this.map;
    const c = this.palette;
    // A new style starts without the fan
    this.fan = null;
    map.addSource("tracks", { type: "geojson", data: this.tracks });
    map.addLayer({ id: "track-casing", type: "line", source: "tracks",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": c.casing, "line-width": 7, "line-opacity": 0.85 } });
    map.addLayer({ id: "track-line", type: "line", source: "tracks",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": c.track, "line-width": 3.5 } });
    map.addSource("photos", { type: "geojson", data: this.photos, cluster: true,
      clusterRadius: CLUSTER_RADIUS, clusterMaxZoom: MAX_ZOOM, maxzoom: MAX_ZOOM + 1 });
    map.addSource("fan", { type: "geojson", data: EMPTY });
    const clustered = ["has", "point_count"];
    const unclustered = ["!", ["has", "point_count"]];
    const ringColor = ["match", ["get", "state"], "stop", c.stop, "has_location", c.located,
      "matched", c.matched, c.skipped];
    map.addLayer({ id: "clusters", type: "circle", source: "photos", filter: clustered,
      paint: { "circle-color": c.accent,
        "circle-radius": ["step", ["get", "point_count"], 15, 10, 19, 100, 24],
        "circle-stroke-width": 2, "circle-stroke-color": c.casing } });
    if (map.getStyle().glyphs) {
      map.addLayer({ id: "cluster-count", type: "symbol", source: "photos", filter: clustered,
        layout: { "text-field": ["number-format", ["get", "point_count"],
          { locale: currentLocale() }], "text-font": FONT,
          "text-size": 13, "text-allow-overlap": true },
        paint: { "text-color": "#ffffff" } });
    }
    map.addLayer({ id: "photo-rings", type: "circle", source: "photos", filter: unclustered,
      paint: { "circle-radius": 19, "circle-color": ringColor,
        "circle-stroke-width": 2, "circle-stroke-color": c.casing } });
    map.addLayer({ id: "photo-thumbs", type: "symbol", source: "photos", filter: unclustered,
      layout: { "icon-image": ["get", "image"], "icon-allow-overlap": true,
        "icon-ignore-placement": true } });
    // The fanned-out photos, joined to their place by thin lines
    const isLine = ["==", ["geometry-type"], "LineString"];
    const isPoint = ["==", ["geometry-type"], "Point"];
    map.addLayer({ id: "fan-legs", type: "line", source: "fan", filter: isLine,
      paint: { "line-color": c.accent, "line-width": 1.5, "line-opacity": 0.8 } });
    map.addLayer({ id: "fan-centre", type: "circle", source: "fan",
      filter: ["all", isPoint, ["get", "centre"]],
      paint: { "circle-radius": 4, "circle-color": c.accent,
        "circle-stroke-width": 1.5, "circle-stroke-color": c.casing } });
    const leg = ["all", isPoint, ["!", ["get", "centre"]]];
    map.addLayer({ id: "fan-rings", type: "circle", source: "fan", filter: leg,
      paint: { "circle-radius": 19, "circle-color": ringColor,
        "circle-stroke-width": 2, "circle-stroke-color": c.casing } });
    map.addLayer({ id: "fan-thumbs", type: "symbol", source: "fan", filter: leg,
      layout: { "icon-image": ["get", "image"], "icon-allow-overlap": true,
        "icon-ignore-placement": true } });
    for (const [layer, source] of [["photo-selected", "photos"], ["fan-selected", "fan"]]) {
      map.addLayer({ id: layer, type: "circle", source,
        filter: ["==", ["get", "id"], this.selected],
        paint: { "circle-radius": 22, "circle-color": "rgba(0,0,0,0)",
          "circle-stroke-width": 4, "circle-stroke-color": c.accent } });
    }
  }

  // A thumbnail the photo layer asks for: a grey disc at once, the photo when it comes
  addThumbnail(name) {
    const match = /^thumb-(\d+)-(\d+)$/.exec(name);
    if (!match || this.map.hasImage(name)) {
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = THUMB;
    const context = canvas.getContext("2d");
    context.fillStyle = this.palette.placeholder;
    context.beginPath();
    context.arc(THUMB / 2, THUMB / 2, THUMB / 2, 0, 2 * Math.PI);
    context.fill();
    this.map.addImage(name, context.getImageData(0, 0, THUMB, THUMB), { pixelRatio: 2 });
    this.thumbnail(Number(match[1]), Number(match[2])).then((drawn) => {
      if (drawn && this.map.hasImage(name)) {
        this.map.updateImage(name, drawn);
      }
    });
  }

  setTracks(tracks) {
    this.tracks = {
      type: "FeatureCollection",
      features: tracks.map((track) => ({
        type: "Feature", properties: { id: track.id },
        geometry: { type: "MultiLineString", coordinates: track.lines },
      })),
    };
    const source = this.map.getSource("tracks");
    if (source) {
      source.setData(this.tracks);
    }
  }

  // photos: [{id, lat, lon, state}] of the photos that have a position
  setPhotos(generation, photos) {
    this.photos = {
      type: "FeatureCollection",
      features: photos.map((p) => ({
        type: "Feature",
        properties: { id: p.id, state: p.state, image: `thumb-${generation}-${p.id}` },
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      })),
    };
    // The groups are made anew
    this.closeFan();
    const source = this.map.getSource("photos");
    if (source) {
      source.setData(this.photos);
    }
  }

  // Mark a photo; with its position, bring it into view, out of its group
  select(id, position) {
    this.selected = id === null ? -1 : id;
    for (const layer of ["photo-selected", "fan-selected"]) {
      if (this.map.getLayer(layer)) {
        this.map.setFilter(layer, ["==", ["get", "id"], this.selected]);
      }
    }
    const token = ++this.revealToken;
    // A photo of the fan in view is shown already
    const shown = this.fan && this.fan.ids.includes(id)
      && this.map.getBounds().contains(this.fan.centre);
    if (position && !shown) {
      this.closeFan();
      this.reveal(id, [position.lon, position.lat], token);
    }
  }

  async reveal(id, centre, token) {
    for (let step = 0; step < 4 && token === this.revealToken; step++) {
      await this.moveTo({ center: centre, zoom: Math.max(this.map.getZoom(), 15) });
      if (token !== this.revealToken) {
        return;
      }
      const group = await this.groupOf(id, centre);
      if (!group || token !== this.revealToken) {
        return;
      }
      const zoom = await this.map.getSource("photos")
        .getClusterExpansionZoom(group.properties.cluster_id);
      if (zoom > FAN_ZOOM || zoom > this.map.getMaxZoom()) {
        await this.openFan(group);
        return;
      }
      await this.moveTo({ center: centre, zoom });
    }
  }

  // The part of the map the panels leave free is the map's padding, so
  // that the centre and fitting refer to it; it is never given twice
  usePadding() {
    this.map.setPadding(this.padding());
  }

  // Resolves when the map has moved there and drawn the photos
  async moveTo(options) {
    // A movement in progress would end at once and resolve this too early
    this.map.stop();
    this.usePadding();
    await new Promise((resolve) => {
      this.map.once("moveend", () => resolve());
      this.map.easeTo({ ...options, duration: duration(600) });
    });
    if (this.map.getSource("photos") && !this.map.isSourceLoaded("photos")) {
      await new Promise((resolve) => this.map.once("idle", () => resolve()));
    }
  }

  // The group drawn near centre that holds the photo, or null
  async groupOf(id, centre) {
    const point = this.map.project(centre);
    const box = [[point.x - CLUSTER_RADIUS, point.y - CLUSTER_RADIUS],
      [point.x + CLUSTER_RADIUS, point.y + CLUSTER_RADIUS]];
    const source = this.map.getSource("photos");
    for (const group of this.map.queryRenderedFeatures(box, { layers: ["clusters"] })) {
      const leaves = await source.getClusterLeaves(group.properties.cluster_id, Infinity, 0);
      if (leaves.some((leaf) => leaf.properties.id === id)) {
        return group;
      }
    }
    return null;
  }

  fitTo(coordinates) {
    if (!coordinates.length) {
      return;
    }
    const bounds = new LngLatBounds(coordinates[0], coordinates[0]);
    for (const point of coordinates) {
      bounds.extend(point);
    }
    this.map.stop();
    this.usePadding();
    this.map.fitBounds(bounds, { maxZoom: 16, duration: duration(800) });
  }

  // A click on a group: closer, or fanned out when it would not part
  async openCluster(feature) {
    if (this.fan && this.fan.clusterId === feature.properties.cluster_id) {
      return;
    }
    const zoom = await this.map.getSource("photos")
      .getClusterExpansionZoom(feature.properties.cluster_id);
    if (zoom > this.map.getMaxZoom()) {
      await this.openFan(feature);
    } else {
      this.map.easeTo({ center: feature.geometry.coordinates, zoom, duration: duration(500) });
    }
  }

  async openFan(group) {
    const clusterId = group.properties.cluster_id;
    const leaves = await this.map.getSource("photos").getClusterLeaves(clusterId, Infinity, 0);
    leaves.sort((a, b) => a.properties.id - b.properties.id);
    const centre = group.geometry.coordinates;
    const middle = this.map.project(centre);
    const features = [{ type: "Feature", properties: { centre: true },
      geometry: { type: "Point", coordinates: centre } }];
    fanOffsets(leaves.length).forEach(([dx, dy], k) => {
      const at = this.map.unproject([middle.x + dx, middle.y + dy]);
      const position = [at.lng, at.lat];
      features.push({ type: "Feature", properties: { centre: false },
        geometry: { type: "LineString", coordinates: [centre, position] } });
      features.push({ type: "Feature", properties: { ...leaves[k].properties, centre: false },
        geometry: { type: "Point", coordinates: position } });
    });
    this.fan = { clusterId, centre, ids: leaves.map((leaf) => leaf.properties.id) };
    this.map.getSource("fan").setData({ type: "FeatureCollection", features });
    this.filterGroups();
  }

  closeFan() {
    if (!this.map.getSource("fan")) {
      return;
    }
    if (this.fan) {
      this.fan = null;
      this.map.getSource("fan").setData(EMPTY);
    }
    this.filterGroups();
  }

  // The group that is fanned out is not drawn itself
  filterGroups() {
    const filter = this.fan
      ? ["all", ["has", "point_count"], ["!=", ["get", "cluster_id"], this.fan.clusterId]]
      : ["has", "point_count"];
    for (const layer of ["clusters", "cluster-count"]) {
      if (this.map.getLayer(layer)) {
        this.map.setFilter(layer, filter);
      }
    }
  }
}

// A round thumbnail, turned upright, as ImageData for the map
export async function roundThumbnail(blob, orientation) {
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = THUMB;
  const context = canvas.getContext("2d");
  context.beginPath();
  context.arc(THUMB / 2, THUMB / 2, THUMB / 2, 0, 2 * Math.PI);
  context.clip();
  context.translate(THUMB / 2, THUMB / 2);
  context.rotate({ 3: Math.PI, 6: Math.PI / 2, 8: -Math.PI / 2 }[orientation] || 0);
  const side = Math.min(bitmap.width, bitmap.height);
  context.drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side,
    -THUMB / 2, -THUMB / 2, THUMB, THUMB);
  bitmap.close();
  return context.getImageData(0, 0, THUMB, THUMB);
}
