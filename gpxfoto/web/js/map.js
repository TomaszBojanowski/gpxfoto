// The map: the tracks, the photos as round thumbnails, and the selected photo.

import {
  AttributionControl, LngLatBounds, Map as MapLibreMap, NavigationControl, ScaleControl,
} from "../vendor/maplibre-gl/maplibre-gl.mjs";

const STYLES = "https://tiles.openfreemap.org/styles/";
// Without the map server (no internet), the tracks and photos are drawn
// on a plain background
const STYLE_TIMEOUT = 8000;          // ms
const THUMB = 64;                    // px of a thumbnail image, drawn at half size
const FONT = ["Noto Sans Bold"];

const dark = window.matchMedia("(prefers-color-scheme: dark)");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function colors() {
  const style = getComputedStyle(document.documentElement);
  const read = (name) => style.getPropertyValue(name).trim();
  return {
    track: dark.matches ? "#ff8a3d" : "#e2561c",
    casing: dark.matches ? "#1e2024" : "#ffffff",
    accent: read("--accent"), matched: read("--matched"), stop: read("--stop"),
    skipped: read("--skipped"), located: read("--located"),
  };
}

function plainStyle() {
  return {
    version: 8,
    sources: {},
    layers: [{ id: "background", type: "background",
      paint: { "background-color": dark.matches ? "#26292e" : "#eceff1" } }],
  };
}

export class PhotoMap {
  // thumbnail(generation, id) -> Promise of an ImageBitmap-like or null;
  // onSelect(id) when a photo on the map is clicked; padding() -> the
  // margins of the map the panels cover, {top, bottom, left, right} in px
  constructor(container, { thumbnail, onSelect, padding }) {
    this.thumbnail = thumbnail;
    this.onSelect = onSelect;
    this.padding = padding;
    this.tracks = { type: "FeatureCollection", features: [] };
    this.photos = { type: "FeatureCollection", features: [] };
    this.selected = -1;
    this.plain = false;
    this.map = new MapLibreMap({
      container,
      style: STYLES + (dark.matches ? "dark" : "liberty"),
      center: [15, 50],
      zoom: 3,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
    });
    this.map.touchZoomRotate.disableRotation();
    this.map.addControl(new AttributionControl({ compact: true }), "bottom-right");
    this.map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    this.map.addControl(new ScaleControl(), "bottom-right");
    this.map.on("style.load", () => this.addLayers());
    this.map.setMissingStyleImageResolver((name) => this.addThumbnail(name));
    this.map.on("error", () => this.fallBack());
    this.styleTimer = setTimeout(() => this.fallBack(), STYLE_TIMEOUT);
    dark.addEventListener("change", () => {
      // Without diff the old style is dropped at once, so a server that
      // cannot be reached leads to the plain background again
      this.plain = false;
      this.map.setStyle(STYLES + (dark.matches ? "dark" : "liberty"), { diff: false });
      this.styleTimer = setTimeout(() => this.fallBack(), STYLE_TIMEOUT);
    });
    for (const layer of ["photo-thumbs", "photo-rings"]) {
      this.map.on("click", layer, (event) => this.onSelect(event.features[0].properties.id));
      this.map.on("mouseenter", layer, () => { this.map.getCanvas().style.cursor = "pointer"; });
      this.map.on("mouseleave", layer, () => { this.map.getCanvas().style.cursor = ""; });
    }
    this.map.on("click", "clusters", (event) => this.openCluster(event.features[0]));
  }

  // The map server cannot be reached: draw on a plain background
  fallBack() {
    if (this.plain || this.map.isStyleLoaded()) {
      return;
    }
    this.plain = true;
    clearTimeout(this.styleTimer);
    this.map.setStyle(plainStyle(), { diff: false });
  }

  addLayers() {
    clearTimeout(this.styleTimer);
    const map = this.map;
    const c = colors();
    map.addSource("tracks", { type: "geojson", data: this.tracks });
    map.addLayer({ id: "track-casing", type: "line", source: "tracks",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": c.casing, "line-width": 7, "line-opacity": 0.85 } });
    map.addLayer({ id: "track-line", type: "line", source: "tracks",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": c.track, "line-width": 3.5 } });
    map.addSource("photos", { type: "geojson", data: this.photos, cluster: true,
      clusterRadius: 40, clusterMaxZoom: 17 });
    const unclustered = ["!", ["has", "point_count"]];
    map.addLayer({ id: "clusters", type: "circle", source: "photos",
      filter: ["has", "point_count"],
      paint: { "circle-color": c.accent,
        "circle-radius": ["step", ["get", "point_count"], 15, 10, 19, 100, 24],
        "circle-stroke-width": 2, "circle-stroke-color": c.casing } });
    if (map.getStyle().glyphs) {
      map.addLayer({ id: "cluster-count", type: "symbol", source: "photos",
        filter: ["has", "point_count"],
        layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": FONT,
          "text-size": 13, "text-allow-overlap": true },
        paint: { "text-color": "#ffffff" } });
    }
    map.addLayer({ id: "photo-rings", type: "circle", source: "photos", filter: unclustered,
      paint: { "circle-radius": 19,
        "circle-color": ["match", ["get", "state"], "stop", c.stop, "has_location", c.located,
          "matched", c.matched, c.skipped],
        "circle-stroke-width": 2, "circle-stroke-color": c.casing } });
    map.addLayer({ id: "photo-thumbs", type: "symbol", source: "photos", filter: unclustered,
      layout: { "icon-image": ["get", "image"], "icon-allow-overlap": true,
        "icon-ignore-placement": true } });
    map.addLayer({ id: "photo-selected", type: "circle", source: "photos",
      filter: ["==", ["get", "id"], this.selected],
      paint: { "circle-radius": 22, "circle-color": "rgba(0,0,0,0)",
        "circle-stroke-width": 4, "circle-stroke-color": c.accent } });
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
    context.fillStyle = dark.matches ? "#4a4f57" : "#c9ced4";
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
    const source = this.map.getSource("photos");
    if (source) {
      source.setData(this.photos);
    }
  }

  select(id, position) {
    this.selected = id === null ? -1 : id;
    if (this.map.getLayer("photo-selected")) {
      this.map.setFilter("photo-selected", ["==", ["get", "id"], this.selected]);
    }
    if (position) {
      this.map.easeTo({ center: [position.lon, position.lat], padding: this.padding(),
        zoom: Math.max(this.map.getZoom(), 15), duration: reducedMotion.matches ? 0 : 600 });
    }
  }

  fitTo(coordinates) {
    if (!coordinates.length) {
      return;
    }
    const bounds = new LngLatBounds(coordinates[0], coordinates[0]);
    for (const point of coordinates) {
      bounds.extend(point);
    }
    this.map.fitBounds(bounds, { padding: this.padding(), maxZoom: 16,
      duration: reducedMotion.matches ? 0 : 800 });
  }

  async openCluster(feature) {
    const source = this.map.getSource("photos");
    const zoom = await source.getClusterExpansionZoom(feature.properties.cluster_id);
    this.map.easeTo({ center: feature.geometry.coordinates, zoom,
      duration: reducedMotion.matches ? 0 : 500 });
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
