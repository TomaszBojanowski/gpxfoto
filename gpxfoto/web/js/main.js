// The page of the browser interface: it shows what the server's session
// holds and sends the user's choices back. All the work happens on the
// server; this thread only draws and handles input.

import { address, base64, get, listen, post, token } from "./api.js";
import { choose } from "./chooser.js";
import { clockTime, clockTimeOfUnix, coordinates, exactDuration } from "./format.js";
import { _, format, loadCatalog, ngettext, number } from "./i18n.js";
import { PhotoList } from "./list.js";
import { PhotoMap, roundThumbnail } from "./map.js";
import { loadBaseStyle } from "./styles.js";

// The map's style is fetched while the page starts
loadBaseStyle().catch(() => {});
// The texts first: the map takes its labels when it is made
await loadCatalog();

const state = {
  photoGeneration: 0,
  folder: null,
  photos: [],             // summaries from the server, by id
  results: [],            // the last match, by id
  loading: false,
  found: 0,
  trackGeneration: 0,
  trackChoice: null,
  tracks: new Map(),      // id -> summary
  correction: 0,
  base: 0,                // whole hours; the slider covers base ± 30 min
  overwrite: false,
  stops: true,
  filter: "all",
  selected: null,
  // The map shows all of the tracks, until the user moves it
  autoFit: true,
  mapStyle: null,
};

const $ = (id) => document.getElementById(id);

// --- texts of the page -------------------------------------------------

function translatePage() {
  for (const element of document.querySelectorAll("[data-text]")) {
    element.textContent = _(element.dataset.text);
  }
  for (const element of document.querySelectorAll("[data-label]")) {
    element.setAttribute("aria-label", _(element.dataset.label));
    element.title = _(element.dataset.label);
  }
  document.title = "gpxfoto";
  photoMap.updateTexts();
}

// --- notices -----------------------------------------------------------

function notice(message, kind = "error") {
  const element = document.createElement("div");
  element.className = "notice " + kind;
  const text = document.createElement("p");
  text.textContent = message;
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", _("Close"));
  close.addEventListener("click", () => element.remove());
  element.append(text, close);
  $("notices").append(element);
  while ($("notices").children.length > 5) {
    $("notices").firstElementChild.remove();
  }
}

function progress(done, total) {
  const box = $("progress");
  if (total === null) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.querySelector("span").style.width = (total ? (100 * done) / total : 0) + "%";
  box.querySelector("p").textContent = format(_("Reading photos: {done} of {total}"),
    { done: number(done), total: number(total) });
}

// --- thumbnails ----------------------------------------------------------

function thumbnailUrl(generation, id) {
  return address("thumbnail", { generation, id });
}

async function mapThumbnail(generation, id) {
  const photo = state.photos[id];
  if (generation !== state.photoGeneration || !photo || !photo.thumbnail) {
    return null;
  }
  try {
    const response = await fetch(thumbnailUrl(generation, id));
    if (!response.ok) {
      return null;
    }
    return await roundThumbnail(await response.blob(), photo.orientation);
  } catch (error) {
    return null;
  }
}

// --- the list ------------------------------------------------------------

function detailOf(photo, result) {
  if (!result) {
    return photo.reason || "";
  }
  if (result.state === "matched" || result.state === "stop") {
    const parts = [];
    if (result.overwrites) {
      parts.push(_("will overwrite the existing location"));
    }
    if (result.stop) {
      parts.push(format(_("stop {start} – {end}"), {
        start: clockTimeOfUnix(result.stop[0], result.time),
        end: clockTimeOfUnix(result.stop[1], result.time),
      }));
    } else {
      parts.push(coordinates(result.lat, result.lon));
    }
    return parts.join(" · ");
  }
  if (result.state === "has_location") {
    return format(_("skipped: {reason}"), { reason: result.reason });
  }
  return format(_("skipped: {reason}"), { reason: result.reason || "" });
}

// The lines a selected row adds: the whole of its detail and the position
function moreOf(photo, result) {
  const lines = [detailOf(photo, result)];
  if (isMatched(result)) {
    let position = coordinates(result.lat, result.lon);
    if (result.ele !== null && result.ele !== undefined) {
      position += " · " + format(_("{elevation} m"), { elevation: number(result.ele) });
    }
    lines.push(position);
  }
  return lines.filter(Boolean);
}

function renderRow(row, id, selected) {
  const photo = state.photos[id];
  const result = state.results[id];
  row.classList.add(result ? result.state : "waiting");
  row.classList.toggle("selected", selected);
  row.classList.toggle("expanded", selected);
  row.replaceChildren();
  const thumb = document.createElement("span");
  thumb.className = "thumb";
  if (photo.thumbnail) {
    const image = document.createElement("img");
    image.alt = "";
    image.className = "o" + photo.orientation;
    image.loading = "lazy";
    image.addEventListener("error", () => image.remove());
    image.src = thumbnailUrl(state.photoGeneration, id);
    thumb.append(image);
  }
  const text = document.createElement("span");
  text.className = "text";
  const name = document.createElement("span");
  name.className = "name";
  name.textContent = photo.name;
  text.append(name);
  for (const line of selected ? moreOf(photo, result) : [detailOf(photo, result)]) {
    const detail = document.createElement("span");
    detail.className = "detail";
    detail.textContent = line;
    if (result && result.overwrites && !text.querySelector(".overwrites")) {
      detail.classList.add("overwrites");
    }
    text.append(detail);
  }
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = clockTime(result && result.time ? result.time : photo.taken);
  row.append(thumb, text, time);
}

function isMatched(result) {
  return result && (result.state === "matched" || result.state === "stop");
}

function visibleIds() {
  const ids = [];
  for (const photo of state.photos) {
    if (!photo) {
      continue;                 // a batch that has not come yet
    }
    const result = state.results[photo.id];
    if (state.filter === "all"
        || (state.filter === "matched" && isMatched(result))
        || (state.filter === "skipped" && !isMatched(result))) {
      ids.push(photo.id);
    }
  }
  return ids;
}

const list = new PhotoList($("list"), { render: renderRow, onSelect: (id) => select(id) });

// --- the map -------------------------------------------------------------

// The part of the map the panels leave free: how far it is from each edge
function freeArea() {
  const toolbar = $("toolbar").getBoundingClientRect();
  const sidebar = $("sidebar").getBoundingClientRect();
  const timebar = $("timebar").getBoundingClientRect();
  if (sidebar.top > toolbar.bottom + 24) {
    // A narrow window: the list is at the bottom, under the time correction
    return { top: toolbar.bottom, right: 0, left: 0,
      bottom: window.innerHeight - Math.min(sidebar.top, timebar.top) };
  }
  return { top: toolbar.bottom, right: 0, left: sidebar.right,
    bottom: window.innerHeight - timebar.top };
}

// The free part with some room around, for showing places on the map
function mapPadding() {
  const free = freeArea();
  const margin = 24;
  const padding = { top: free.top + margin, right: free.right + margin,
    bottom: free.bottom + margin, left: free.left + margin };
  // A window too small for the panels: use all of it
  if (padding.top + padding.bottom > window.innerHeight - 2 * margin
      || padding.left + padding.right > window.innerWidth - 2 * margin) {
    return { top: margin, bottom: margin, left: margin, right: margin };
  }
  return padding;
}

// The buttons of the map stay in its free part
function placeMapControls() {
  const free = freeArea();
  const root = document.documentElement.style;
  root.setProperty("--free-top", Math.round(free.top) + "px");
  root.setProperty("--free-bottom", Math.round(free.bottom) + "px");
}
const panels = new ResizeObserver(() => placeMapControls());
for (const id of ["toolbar", "sidebar", "timebar"]) {
  panels.observe($(id));
}
window.addEventListener("resize", () => placeMapControls());

// Without WebGL there is no map, but the list and the rest still work
const nothing = new Proxy(function () {}, { get: () => nothing, apply: () => nothing });

function makeMap() {
  try {
    return new PhotoMap($("map"), {
      thumbnail: mapThumbnail,
      onSelect: (id) => select(id),
      padding: mapPadding,
      onFit: () => showWhole(),
      onStyle: (name) => setMapStyle(name, true),
    });
  } catch (error) {
    notice(_("The map cannot be shown: this browser does not allow WebGL."));
    return nothing;
  }
}

const photoMap = makeMap();

// Moved by hand, the map stays where the user put it
photoMap.map.on("movestart", (event) => {
  if (event.originalEvent) {
    state.autoFit = false;
  }
});

// All of the tracks, or without them the photos placed on the map
function wholeView() {
  const points = [...state.tracks.values()].flatMap((track) => track.lines.flat());
  return points.length ? points : state.results.filter(isMatched).map((r) => [r.lon, r.lat]);
}

function showWhole() {
  photoMap.fitTo(wholeView());
}

// While tracks and photos come in, the map keeps all of them in view
let fitTimer = null;
function followWhole() {
  if (state.autoFit) {
    clearTimeout(fitTimer);
    fitTimer = setTimeout(() => {
      if (state.autoFit) {
        showWhole();
      }
    }, 200);
  }
}

function setMapStyle(name, chosen = false) {
  if (name !== state.mapStyle) {
    state.mapStyle = name;
    photoMap.setStyleName(name);
  }
  if (chosen) {
    post("preferences", { map_style: name }).catch((error) => notice(error.message));
  }
}

function select(id) {
  state.autoFit = false;
  state.selected = id;
  list.select(id);
  const result = state.results[id];
  photoMap.select(id, isMatched(result) ? result : null);
}

// --- what the page shows -------------------------------------------------

function showPhotos() {
  const count = state.photos.length;
  $("empty").hidden = count > 0;
  const matched = state.results.filter(isMatched).length;
  const skipped = state.results.length - matched;
  const buttons = document.querySelectorAll(".filters button");
  const labels = {
    all: format(_("All ({count})"), { count: number(count) }),
    matched: format(_("Matched ({count})"), { count: number(matched) }),
    skipped: format(_("Skipped ({count})"),
      { count: number(state.results.length ? skipped : 0) }),
  };
  for (const button of buttons) {
    button.textContent = labels[button.dataset.filter];
    button.setAttribute("aria-pressed", String(button.dataset.filter === state.filter));
  }
  list.setIds(visibleIds());
  const write = $("write");
  write.textContent = format(ngettext("Write {count} location", "Write {count} locations",
    matched), { count: number(matched) });
  write.disabled = true;
}

function showSummary(match) {
  if (!match) {
    $("summary").textContent = "";
    return;
  }
  let text = format(_("Matched: {matched}, skipped: {skipped}"),
    { matched: number(match.matched), skipped: number(match.skipped) });
  if (match.matched && state.stops) {
    text += " · " + format(ngettext("During stops: {count} of {matched} matched photo",
      "During stops: {count} of {matched} matched photos", match.matched),
    { count: number(match.at_stops), matched: number(match.matched) });
  }
  $("summary").textContent = text;
}

function applyMatch(match) {
  if (!match || match.generation !== state.photoGeneration
      || match.tracks !== state.trackGeneration) {
    return;
  }
  state.results = match.results;
  showSummary(match);
  photoMap.setPhotos(state.photoGeneration, match.results.filter(isMatched));
  showPhotos();
  if (!state.tracks.size) {
    followWhole();
  }
}

function folderName(path) {
  return path.split("/").filter(Boolean).pop() || path;
}

function showChoices() {
  const photosValue = $("photos-value");
  if (state.folder) {
    photosValue.textContent = folderName(state.folder);
    photosValue.parentElement.title = state.folder;
  }
  const tracksValue = $("tracks-value");
  const choice = state.trackChoice;
  if (choice) {
    const names = choice.files ? choice.files.map(folderName) : [folderName(choice.folder)];
    tracksValue.textContent = names.join(", ");
    tracksValue.parentElement.title = (choice.files || [choice.folder]).join("\n");
  }
}

function showTracks() {
  const tracks = [...state.tracks.values()];
  photoMap.setTracks(tracks);
  if (tracks.length) {
    followWhole();
  }
}

// --- the time correction -------------------------------------------------

const slider = $("correction");
let sending = false;
let pending = null;

function showCorrection() {
  slider.value = String(state.correction - state.base);
  $("correction-value").textContent = exactDuration(state.correction, state.correction !== 0);
}

async function sendCorrection(seconds) {
  pending = seconds;
  if (sending) {
    return;
  }
  sending = true;
  // Only the newest value is sent; those in between are skipped
  while (pending !== null) {
    const value = pending;
    pending = null;
    try {
      await post("correction", { seconds: value });
    } catch (error) {
      notice(error.message);
      pending = null;
      sending = false;
      // The page shows again what is in effect
      refreshState();
      return;
    }
  }
  sending = false;
}

// As far as the server goes: a day either way
const MAX_CORRECTION = 86400;

function setCorrection(seconds, base = Math.round(seconds / 3600) * 3600) {
  const limit = (value) => Math.max(-MAX_CORRECTION, Math.min(MAX_CORRECTION, value));
  state.correction = limit(seconds);
  state.base = limit(base);
  seconds = state.correction;
  showCorrection();
  sendCorrection(seconds);
}

slider.addEventListener("input", () => {
  setCorrection(state.base + Number(slider.value), state.base);
});
$("minus-hour").addEventListener("click", () =>
  setCorrection(state.correction - 3600, state.base - 3600));
$("plus-hour").addEventListener("click", () =>
  setCorrection(state.correction + 3600, state.base + 3600));
$("reset-correction").addEventListener("click", () => setCorrection(0, 0));

// --- choices ---------------------------------------------------------------

$("choose-photos").addEventListener("click", async () => {
  const chosen = await choose("photos", state.folder || "");
  if (chosen && chosen.folder) {
    try {
      await post("photos", chosen);
    } catch (error) {
      notice(error.message);
    }
  }
});

$("choose-tracks").addEventListener("click", async () => {
  const choice = state.trackChoice;
  const start = choice ? (choice.folder || choice.files[0].replace(/\/[^/]*$/, "")) : "";
  const chosen = await choose("tracks", start);
  if (chosen) {
    try {
      await post("tracks", chosen);
    } catch (error) {
      notice(error.message);
    }
  }
});

const optionsButton = $("options-button");
const optionsMenu = $("options-menu");
optionsButton.addEventListener("click", () => {
  optionsMenu.hidden = !optionsMenu.hidden;
  optionsButton.setAttribute("aria-expanded", String(!optionsMenu.hidden));
});
document.addEventListener("click", (event) => {
  if (!optionsMenu.hidden && !event.target.closest(".menu")) {
    optionsMenu.hidden = true;
    optionsButton.setAttribute("aria-expanded", "false");
  }
});
$("overwrite").addEventListener("change", (event) => {
  post("options", { overwrite: event.target.checked }).catch((error) => notice(error.message));
});
$("stops").addEventListener("change", (event) => {
  state.stops = event.target.checked;
  post("options", { stops: event.target.checked }).catch((error) => notice(error.message));
});

for (const button of document.querySelectorAll(".filters button")) {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    showPhotos();
  });
}

// --- dropping GPX files ----------------------------------------------------

const dropZone = $("drop-zone");
let dragDepth = 0;

function carriesFiles(event) {
  return event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files");
}

document.addEventListener("dragenter", (event) => {
  if (carriesFiles(event)) {
    dragDepth += 1;
    dropZone.hidden = false;
  }
});
document.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) {
    dropZone.hidden = true;
  }
});
document.addEventListener("dragover", (event) => {
  if (carriesFiles(event)) {
    event.preventDefault();
  }
});
// Larger files would not fit into one request once encoded
const MAX_DROP = 40 * 1024 * 1024;

document.addEventListener("drop", async (event) => {
  if (!carriesFiles(event)) {
    return;                     // text dropped into a field
  }
  event.preventDefault();
  dragDepth = 0;
  dropZone.hidden = true;
  const dropped = Array.from(event.dataTransfer.files);
  const tracks = dropped.filter((file) => /\.gpx$/i.test(file.name));
  if (!tracks.length) {
    notice(_("Only GPX files can be dropped here. Choose the folder with photos with the "
      + "Photos button."), "info");
    return;
  }
  if (tracks.reduce((sum, file) => sum + file.size, 0) > MAX_DROP) {
    notice(_("The GPX file is too large to be dropped here. Choose it with the Track button."));
    return;
  }
  try {
    const files = [];
    for (const file of tracks) {
      files.push({ name: file.name, data: base64(await file.arrayBuffer()) });
    }
    await post("tracks/drop", { files });
  } catch (error) {
    notice(error.message);
  }
});

// --- events from the server -----------------------------------------------

function resetPhotos(generation, folder) {
  state.photoGeneration = generation;
  state.folder = folder;
  state.photos = [];
  state.results = [];
  state.selected = null;
  state.autoFit = true;
  list.select(null, false);
  photoMap.select(null);
  photoMap.setPhotos(generation, []);
  showSummary(null);
  showChoices();
  showPhotos();
}

const handlers = {
  "photos-reset": (data) => {
    if (data.generation > state.photoGeneration) {
      resetPhotos(data.generation, data.folder);
      progress(0, 0);
    }
  },
  "photos-found": (data) => {
    if (data.generation === state.photoGeneration) {
      progress(0, data.total);
    }
  },
  "photos": (data) => {
    if (data.generation !== state.photoGeneration) {
      return;
    }
    for (const photo of data.photos) {
      state.photos[photo.id] = photo;
    }
    progress(data.done, data.total);
    showPhotos();
  },
  "photos-done": (data) => {
    if (data.generation === state.photoGeneration) {
      progress(0, null);
    }
  },
  "tracks-reset": (data) => {
    if (data.generation > state.trackGeneration) {
      // The same choice is read again when the stops are switched: the
      // map stays where it is
      if (JSON.stringify(data.choice) !== JSON.stringify(state.trackChoice)) {
        state.autoFit = true;
      }
      state.trackGeneration = data.generation;
      state.trackChoice = data.choice;
      state.tracks = new Map();
      // The photos wait for the match on the new tracks
      state.results = [];
      photoMap.setPhotos(state.photoGeneration, []);
      showSummary(null);
      showChoices();
      showTracks();
      showPhotos();
    }
  },
  "track": (data) => {
    if (data.generation === state.trackGeneration) {
      state.tracks.set(data.track.id, data.track);
      showTracks();
    }
  },
  "matches": applyMatch,
  "failure": (data) => notice(data.message),
  "preferences": (data) => setMapStyle(data.map_style),
};

// Events that come while the page catches up with the state wait for it:
// they may be newer than the state it gets
let waiting = null;

function held(all) {
  return Object.fromEntries(Object.entries(all).map(([name, handler]) =>
    [name, (data) => (waiting ? waiting.push(() => handler(data)) : handler(data))]));
}

// The whole state, when the page (re)connects or a choice changed
async function refreshState() {
  waiting = waiting || [];
  try {
    await catchUp();
  } finally {
    const late = waiting || [];
    waiting = null;
    for (const handle of late) {
      handle();
    }
  }
}

async function catchUp() {
  let data;
  try {
    data = await get("state");
  } catch (error) {
    notice(error.message);
    return;
  }
  if (data.photos.generation !== state.photoGeneration || !state.photos.length) {
    resetPhotos(data.photos.generation, data.photos.folder);
  }
  for (const photo of data.photos.photos) {
    state.photos[photo.id] = photo;
  }
  progress(0, data.photos.loading ? 0 : null);
  state.trackGeneration = data.tracks.generation;
  state.trackChoice = data.tracks.choice;
  state.tracks = new Map(data.tracks.tracks.map((track) => [track.id, track]));
  state.overwrite = data.overwrite;
  state.stops = data.stops;
  setMapStyle(data.preferences.map_style);
  $("overwrite").checked = data.overwrite;
  $("stops").checked = data.stops;
  if (!sending) {
    state.correction = data.correction;
    state.base = Math.round(data.correction / 3600) * 3600;
    showCorrection();
  }
  showChoices();
  showTracks();
  showPhotos();
  applyMatch(data.matches);
}

// --- start -----------------------------------------------------------------

translatePage();
showCorrection();
showPhotos();
if (token) {
  listen(held(handlers), refreshState);
} else {
  notice(_("Open the address that gpxfoto showed in the terminal when it started."));
}
