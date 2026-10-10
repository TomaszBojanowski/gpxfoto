// The page of the browser interface: it shows what the server's session
// holds and sends the user's choices back. All the work happens on the
// server; this thread only draws and handles input.

import { address, base64, get, listen, post, token } from "./api.js";
import { choose } from "./chooser.js";
import { clockTime, clockTimeOfUnix, coordinates, exactDuration, parseDuration }
  from "./format.js";
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
  matchVersion: null,     // its number, which a write names
  warnings: [],           // the cards about signs of a suspicious match
  notes: new Map(),       // id -> what the shown cards say of the photo
  loading: false,
  tracksLoading: false,
  writing: null,          // the progress of the write or undo under way
  undo: 0,                // how many photos undoing the last write would restore
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
  editing: false,         // photos can be moved on the map by hand
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

// --- warnings ------------------------------------------------------------

// The warning sign as text, which takes the colour of the warnings
const WARNING_SIGN = "\u26a0\ufe0e ";

// Signs of a suspicious match, as cards. They only inform: nothing is
// changed for them, and writing the locations never depends on them. A
// card can be folded, or hidden until gpxfoto is closed, by its kind. Of
// several cards only the first is open, unless the user chose otherwise,
// so that they leave room for the list of photos.
const WARNING_CHOICES = "gpxfoto-warnings";
const warningChoices = { folded: new Set(), open: new Set(), hidden: new Set() };
try {
  const stored = JSON.parse(sessionStorage.getItem(WARNING_CHOICES) || "{}");
  for (const name of Object.keys(warningChoices)) {
    warningChoices[name] = new Set(Array.isArray(stored[name]) ? stored[name] : []);
  }
} catch (error) {
  // Without the storage, the choices last until the page is loaded again
}

function chooseWarning(name, kind, chosen) {
  warningChoices[name][chosen ? "add" : "delete"](kind);
  try {
    sessionStorage.setItem(WARNING_CHOICES, JSON.stringify(Object.fromEntries(
      Object.entries(warningChoices).map(([key, kinds]) => [key, [...kinds]]))));
  } catch (error) {
    // As above
  }
  showWarnings();
}

function foldWarning(kind, folded) {
  warningChoices[folded ? "open" : "folded"].delete(kind);
  chooseWarning(folded ? "folded" : "open", kind, true);
}

function warningCard(card, index) {
  const folded = warningChoices.folded.has(card.kind)
    || (index > 0 && !warningChoices.open.has(card.kind));
  const element = document.createElement("section");
  element.className = "warning";
  const head = document.createElement("div");
  head.className = "warning-head";
  const title = document.createElement("button");
  title.type = "button";
  title.className = "warning-title";
  title.textContent = card.title;
  title.title = card.title;
  title.setAttribute("aria-expanded", String(!folded));
  title.addEventListener("click", () => foldWarning(card.kind, !folded));
  const hide = document.createElement("button");
  hide.type = "button";
  hide.className = "warning-hide";
  hide.textContent = "×";
  hide.title = _("Hide this warning until gpxfoto is closed");
  hide.setAttribute("aria-label", hide.title);
  hide.addEventListener("click", () => chooseWarning("hidden", card.kind, true));
  head.append(title, hide);
  element.append(head);
  if (folded) {
    return element;
  }
  const body = document.createElement("div");
  body.className = "warning-body";
  body.append(paragraph(card.text));
  if (card.items.length) {
    const items = document.createElement("ul");
    for (const item of card.items) {
      const line = document.createElement("li");
      const show = document.createElement("button");
      show.type = "button";
      show.className = "warning-item";
      show.textContent = item.text;
      show.title = item.detail || "";
      // Its photos in turn, with each click
      show.addEventListener("click", () => {
        const at = item.photos.indexOf(state.selected);
        showPhoto(item.photos[(at + 1) % item.photos.length]);
      });
      line.append(show);
      items.append(line);
    }
    body.append(items);
  }
  if (card.more) {
    body.append(paragraph(card.more, "more"));
  }
  if (card.advice) {
    body.append(paragraph(card.advice, "advice"));
  }
  if (card.action) {
    // As with the slider: the correction changes, nothing is written
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "small warning-action";
    apply.textContent = card.action.label;
    apply.disabled = Boolean(state.writing);
    apply.addEventListener("click", () => setCorrection(card.action.correction));
    body.append(apply);
  }
  element.append(body);
  return element;
}

function showWarnings() {
  const shown = state.warnings.filter((card) => !warningChoices.hidden.has(card.kind));
  const hidden = state.warnings.length - shown.length;
  state.notes = new Map();
  for (const card of shown) {
    for (const [id, notes] of Object.entries(card.notes)) {
      state.notes.set(Number(id), [...(state.notes.get(Number(id)) || []), ...notes]);
    }
  }
  const content = shown.map(warningCard);
  if (hidden) {
    const line = paragraph(format(ngettext("{count} warning is hidden.",
      "{count} warnings are hidden.", hidden), { count: number(hidden) }), "warnings-hidden");
    const back = document.createElement("button");
    back.type = "button";
    back.className = "small";
    back.textContent = _("Show");
    back.addEventListener("click", () => {
      for (const card of state.warnings) {
        warningChoices.hidden.delete(card.kind);
      }
      chooseWarning("hidden", null, false);
    });
    line.append(" ", back);
    content.push(line);
  }
  $("warnings").replaceChildren(...content);
  list.refresh();
}

function applyWarnings(data) {
  if (data && data.generation === state.photoGeneration
      && data.tracks === state.trackGeneration) {
    state.warnings = data.warnings;
    showWarnings();
  }
}

// The warnings of photos or tracks that were replaced
function dropWarnings() {
  if (state.warnings.length) {
    state.warnings = [];
    showWarnings();
  }
}

// The progress of reading the photos; a write takes its place
let reading = { done: 0, total: null };

function progress(done, total) {
  reading = { done, total };
  state.loading = total !== null;
  showProgress();
}

function showProgress() {
  const box = $("progress");
  const writing = state.writing;
  const { done, total } = writing || reading;
  $("cancel-write").hidden = !writing;
  if (total === null) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.querySelector("span").style.width = (total ? (100 * done) / total : 0) + "%";
  let text = _("Reading photos: {done} of {total}");
  if (writing && writing.cancelled) {
    text = _("Cancelling: finishing the photos being written…");
  } else if (writing && writing.kind === "undo") {
    text = _("Undoing the write: {done} of {total}");
  } else if (writing) {
    text = _("Writing locations: {done} of {total}");
  }
  box.querySelector("p").textContent = format(text,
    { done: number(done), total: number(total) });
  $("cancel-write").disabled = Boolean(writing && writing.cancelled);
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
  if (isMatched(result)) {
    const parts = [];
    if (result.overwrites) {
      parts.push(_("will overwrite the existing location"));
    }
    if (result.state === "manual") {
      parts.push(_("placed by hand"));
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
    if (lines[0] === position) {
      lines.pop();              // the detail was the position alone: said once
    }
    if (result.ele !== null && result.ele !== undefined) {
      position += " · " + format(_("{elevation} m"), { elevation: number(result.ele) });
    }
    lines.push(position);
  }
  if (photo.tz_note) {
    lines.push(photo.tz_note);
  }
  for (const note of state.notes.get(photo.id) || []) {
    lines.push(WARNING_SIGN + note);
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
  const notes = state.notes.get(id);
  if (notes) {
    const mark = document.createElement("span");
    mark.className = "mark";
    mark.textContent = WARNING_SIGN;
    mark.title = notes.join("\n");
    name.prepend(mark);
  }
  text.append(name);
  for (const line of selected ? moreOf(photo, result) : [detailOf(photo, result)]) {
    const detail = document.createElement("span");
    detail.className = "detail";
    detail.textContent = line;
    if (result && result.overwrites && !text.querySelector(".overwrites")) {
      detail.classList.add("overwrites");
    }
    if (line.startsWith(WARNING_SIGN)) {
      detail.classList.add("warned");
    }
    text.append(detail);
  }
  if (selected && state.editing && !state.writing) {
    text.append(editActions(result));
  }
  const time = document.createElement("span");
  time.className = "time";
  time.textContent = clockTime(result && result.time ? result.time : photo.taken);
  if (photo.tz_note) {
    // The time zone of this time is a guess
    time.classList.add("assumed");
    time.title = photo.tz_note;
  }
  row.append(thumb, text, time);
}

function isMatched(result) {
  return Boolean(result) && ["matched", "stop", "manual"].includes(result.state);
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

// --- placing photos by hand ------------------------------------------------

// What the selected row offers while editing
function editActions(result) {
  const actions = document.createElement("span");
  actions.className = "actions";
  const hint = document.createElement("span");
  hint.className = "hint";
  if (result && result.state === "manual") {
    hint.textContent = _("Drag the photo on the map to move it.");
    const back = document.createElement("button");
    back.type = "button";
    back.className = "small";
    back.dataset.action = "unplace";
    back.textContent = _("Use the position from the track");
    actions.append(hint, back);
  } else if (isMatched(result)) {
    hint.textContent = _("Drag the photo on the map to move it.");
    actions.append(hint);
  } else {
    hint.textContent = _("Click on the map where this photo was taken.");
    actions.append(hint);
  }
  return actions;
}

async function place(id, position) {
  try {
    await post("position", { generation: state.photoGeneration, id,
      lat: position ? position.lat : null, lon: position ? position.lon : null });
  } catch (error) {
    notice(error.message);
    // The photo goes back where it was
    photoMap.showDragged(null);
  }
}

$("list").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (button && button.dataset.action === "unplace") {
    place(Number(button.closest(".row").dataset.id), null);
  }
});

function setEditing(editing) {
  state.editing = editing;
  $("edit").setAttribute("aria-pressed", String(editing));
  photoMap.setEditing(editing);
  list.refresh();
}

$("edit").addEventListener("click", () => setEditing(!state.editing));

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
      onMove: (id, position) => place(id, position),
      // A click on the map places the selected photo that has no position
      onPlace: (position) => {
        const id = state.selected;
        if (id !== null && state.photos[id] && !isMatched(state.results[id])) {
          place(id, position);
        }
      },
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

// Select a photo, also when the list is filtered to leave it out
function showPhoto(id) {
  if (!state.photos[id]) {
    return;
  }
  if (!visibleIds().includes(id)) {
    state.filter = "all";
    showPhotos();
  }
  select(id);
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
  showWriteButton();
}

function showWriteButton() {
  const matched = state.results.filter(isMatched).length;
  const write = $("write");
  let text = format(ngettext("Write {count} location", "Write {count} locations", matched),
    { count: number(matched) });
  if (state.writing) {
    text = state.writing.kind === "undo" ? _("Undoing…") : _("Writing…");
  }
  write.textContent = text;
  $("undo").hidden = !state.undo;
  // Only a match of everything chosen, as the page shows it
  write.disabled = Boolean(state.writing) || !matched || state.loading || state.tracksLoading
    || state.matchVersion === null;
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
  state.matchVersion = match.version;
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
// To the second, which the knob of the slider cannot be set to
$("minus-second").addEventListener("click", () => setCorrection(state.correction - 1));
$("plus-second").addEventListener("click", () => setCorrection(state.correction + 1));

// The value can be typed: a click on it turns it into a field
const correctionValue = $("correction-value");
const correctionInput = $("correction-input");
correctionValue.title = _("Click to type the time correction");

correctionValue.addEventListener("click", () => {
  correctionInput.value = String(state.correction);
  correctionValue.hidden = true;
  correctionInput.hidden = false;
  correctionInput.focus();
  correctionInput.select();
});

// Back to the value; what was typed is used when apply is set. False
// when it cannot be read, and the field then stays.
function endTyping(apply) {
  if (correctionInput.hidden) {
    return true;
  }
  if (apply) {
    const seconds = parseDuration(correctionInput.value);
    if (seconds === null) {
      return false;
    }
    if (seconds !== state.correction) {
      setCorrection(seconds);
    }
  }
  correctionInput.hidden = true;
  correctionValue.hidden = false;
  return true;
}

correctionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    if (endTyping(true)) {
      correctionValue.focus();
    } else {
      notice(_("Type the correction in seconds, or as minutes:seconds, for example −90 or "
        + "−1:30."));
      correctionInput.select();
    }
  } else if (event.key === "Escape") {
    endTyping(false);
    correctionValue.focus();
  }
});
// Leaving the field uses what can be read, and drops what cannot
correctionInput.addEventListener("blur", () => endTyping(true) || endTyping(false));

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

// --- writing ----------------------------------------------------------------

// A question or a message in a dialog; true when ok was chosen
function ask(title, content, ok, cancel = true) {
  const dialog = $("message");
  if (dialog.open) {
    dialog.close();             // a question that is no longer asked
  }
  $("message-title").textContent = title;
  $("message-body").replaceChildren(...content);
  $("message-ok").textContent = ok;
  $("message-cancel").hidden = !cancel;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"),
      { once: true });
  });
}

function paragraph(text, kind = "") {
  const element = document.createElement("p");
  element.textContent = text;
  element.className = kind;
  return element;
}

// While the photos are written, nothing can be chosen anew
const LOCKED = ["choose-photos", "choose-tracks", "overwrite", "stops", "correction",
  "minus-hour", "plus-hour", "minus-second", "plus-second", "correction-value",
  "reset-correction", "edit", "undo"];

function showWriting() {
  for (const id of LOCKED) {
    $(id).disabled = Boolean(state.writing);
  }
  if (state.writing && state.editing) {
    setEditing(false);
  }
  if (state.writing) {
    endTyping(false);
  }
  for (const button of document.querySelectorAll(".warning-action")) {
    button.disabled = Boolean(state.writing);
  }
  showProgress();
  showWriteButton();
}

$("write").addEventListener("click", async () => {
  const version = state.matchVersion;
  const planned = state.results.filter(isMatched);
  const overwrites = planned.filter((result) => result.overwrites).length;
  const content = [paragraph(format(ngettext(
    "The location will be written into {count} photo.",
    "The location will be written into {count} photos.", planned.length),
  { count: number(planned.length) }))];
  if (overwrites) {
    content.push(paragraph(format(ngettext(
      "{count} of them already has a location, which will be overwritten.",
      "{count} of them already have a location, which will be overwritten.", overwrites),
    { count: number(overwrites) }), "error"));
  }
  content.push(paragraph(_("Only the location is written. The image data of every photo is "
    + "checked to be unchanged, and a photo is replaced only once it is.")));
  const ok = format(ngettext("Write {count} location", "Write {count} locations",
    planned.length), { count: number(planned.length) });
  if (!await ask(_("Write the locations?"), content, ok)) {
    return;
  }
  try {
    await post("write", { match: version });
  } catch (error) {
    notice(error.message);
  }
});

$("undo").addEventListener("click", async () => {
  const content = [paragraph(format(ngettext(
    "The GPS data of {count} photo will be restored as it was before the last write.",
    "The GPS data of {count} photos will be restored as they were before the last write.",
    state.undo), { count: number(state.undo) })),
  paragraph(_("A photo changed by another program since then is left as it is."))];
  if (!await ask(_("Undo the last write?"), content, _("Undo"))) {
    return;
  }
  try {
    await post("undo", {});
  } catch (error) {
    notice(error.message);
  }
});

$("cancel-write").addEventListener("click", () => {
  post("write/cancel", {}).catch((error) => notice(error.message));
});

function showWritten(summary) {
  const undo = summary.kind === "undo";
  const counts = undo ? _("Restored: {written}, errors: {errors}")
    : _("Written: {written}, errors: {errors}");
  const content = [paragraph(format(counts,
    { written: number(summary.written), errors: number(summary.failed.length) }))];
  if (summary.written) {
    content.push(paragraph(undo
      ? _("Every restored photo is the same, to the byte, as before the write.")
      : _("The image data of every written file was verified as unchanged.")));
  }
  if (summary.not_written) {
    const left = undo
      ? ngettext("{count} photo was not restored, as the undoing was cancelled.",
        "{count} photos were not restored, as the undoing was cancelled.", summary.not_written)
      : ngettext("{count} photo was not written, as the writing was cancelled.",
        "{count} photos were not written, as the writing was cancelled.", summary.not_written);
    content.push(paragraph(format(left, { count: number(summary.not_written) })));
  }
  if (summary.failed.length) {
    const failures = document.createElement("ul");
    const failed = undo ? _("Could not restore {name}: {error} (file unchanged)")
      : _("Could not write {name}: {error} (file unchanged)");
    for (const failure of summary.failed) {
      const item = document.createElement("li");
      item.className = "error";
      item.textContent = format(failed, { name: failure.name, error: failure.message });
      failures.append(item);
    }
    content.push(failures);
  }
  let title = summary.cancelled ? _("Writing cancelled") : _("Writing finished");
  if (undo) {
    title = summary.cancelled ? _("Undoing cancelled") : _("Undoing finished");
  }
  ask(title, content, _("Close"), false);
}

// Closing the tab does not stop the photos being written, but the
// browser asks first
addEventListener("beforeunload", (event) => {
  if (state.writing) {
    event.preventDefault();
    event.returnValue = "";
  }
});

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
  if (state.writing) {
    notice(_("Wait until the locations are written, or cancel the writing."), "info");
    return;
  }
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
  state.matchVersion = null;
  state.warnings = [];
  state.selected = null;
  state.autoFit = true;
  // First the rows of the old photos go: selecting draws the list again
  list.setIds([]);
  list.select(null, false);
  photoMap.select(null);
  photoMap.setPhotos(generation, []);
  showSummary(null);
  showWarnings();
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
      state.matchVersion = null;
      state.tracksLoading = true;
      dropWarnings();
      photoMap.setPhotos(state.photoGeneration, []);
      showSummary(null);
      showChoices();
      showTracks();
      showPhotos();
    }
  },
  "tracks-done": (data) => {
    if (data.generation === state.trackGeneration) {
      state.tracksLoading = false;
      showWriteButton();
    }
  },
  "track": (data) => {
    if (data.generation === state.trackGeneration) {
      state.tracks.set(data.track.id, data.track);
      showTracks();
    }
  },
  "matches": applyMatch,
  "warnings": applyWarnings,
  "photos-changed": (data) => {
    if (data.generation === state.photoGeneration) {
      for (const photo of data.photos) {
        state.photos[photo.id] = photo;
      }
      showPhotos();
    }
  },
  "write-start": (data) => {
    state.writing = data;
    showWriting();
  },
  "write-progress": (data) => {
    // Only of the write under way; one that ended is done with
    if (state.writing) {
      state.writing = data;
      showProgress();
    }
  },
  "undo": (data) => {
    state.undo = data.count;
    showWriteButton();
  },
  "write-done": (data) => {
    state.writing = null;
    showWriting();
    showWritten(data);
  },
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
  state.tracksLoading = data.tracks.loading;
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
  state.writing = data.writing;
  state.undo = data.undo;
  showChoices();
  showTracks();
  showPhotos();
  showWriting();
  applyMatch(data.matches);
  if (data.warnings) {
    applyWarnings(data.warnings);
  } else {
    dropWarnings();
  }
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
