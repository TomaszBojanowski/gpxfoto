// The dialog for choosing the folder of photos or the track files. The
// server lists the folders: the browser itself never gives a page paths.

import { get } from "./api.js";
import { _, format, ngettext, number } from "./i18n.js";

const dialog = document.getElementById("chooser");
const title = document.getElementById("chooser-title");
const places = document.getElementById("chooser-places");
const pathField = document.getElementById("chooser-path");
const crumbs = document.getElementById("chooser-crumbs");
const errorText = document.getElementById("chooser-error");
const entries = document.getElementById("chooser-entries");
const recursive = document.getElementById("chooser-recursive");
const accept = document.getElementById("chooser-accept");

let kind = "photos";
let current = null;          // the listing shown
let checked = new Set();     // track files ticked, by full path
let finish = null;

function join(folder, name) {
  return folder.endsWith("/") ? folder + name : folder + "/" + name;
}

function button(text, onClick) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = text;
  element.title = text;
  element.addEventListener("click", onClick);
  return element;
}

function heading(text) {
  const element = document.createElement("h3");
  element.textContent = text;
  return element;
}

async function showPlaces() {
  places.replaceChildren();
  let data;
  try {
    data = await get("places");
  } catch (error) {
    return;
  }
  places.append(heading(_("Places")));
  for (const place of data.places) {
    const item = button(place.name, () => open(place.path));
    item.title = place.path;
    places.append(item);
  }
  const recent = data.recent[kind] || [];
  if (recent.length) {
    places.append(heading(_("Recent")));
    for (const path of recent) {
      const name = path.split("/").filter(Boolean).pop() || path;
      const item = button(name, () => {
        if (kind === "tracks" && /\.gpx$/i.test(path)) {
          done({ files: [path] });
        } else {
          open(path);
        }
      });
      item.title = path;
      places.append(item);
    }
  }
}

function counts(folder) {
  if (folder.photos === null) {
    return "";
  }
  const parts = [];
  if (folder.photos) {
    parts.push(format(ngettext("{count} photo", "{count} photos", folder.photos),
      { count: number(folder.photos) }));
  }
  if (folder.tracks) {
    parts.push(format(ngettext("{count} track", "{count} tracks", folder.tracks),
      { count: number(folder.tracks) }));
  }
  return parts.join(", ");
}

function entry(icon, name, detail, onClick) {
  const item = document.createElement("li");
  const iconElement = document.createElement("span");
  iconElement.className = "icon";
  iconElement.textContent = icon;
  const nameElement = document.createElement("span");
  nameElement.className = "name";
  nameElement.textContent = name;
  nameElement.title = name;
  const detailElement = document.createElement("span");
  detailElement.className = "counts";
  detailElement.textContent = detail;
  item.append(iconElement, nameElement, detailElement);
  item.addEventListener("click", (event) => {
    // A double click, as in a file manager, would act again on the entry
    // that the first click put under the pointer
    if (event.detail <= 1) {
      onClick(event);
    }
  });
  return item;
}

function updateAccept() {
  if (kind === "photos") {
    accept.textContent = _("Choose this folder");
    accept.disabled = !current;
  } else if (checked.size) {
    accept.textContent = format(ngettext("Use {count} track file", "Use {count} track files",
      checked.size), { count: number(checked.size) });
    accept.disabled = false;
  } else {
    accept.textContent = _("Use every track in this folder");
    accept.disabled = !current || !current.tracks.length && !current.folders.length;
  }
}

function showCrumbs(path) {
  crumbs.replaceChildren();
  const parts = path.split("/").filter(Boolean);
  const root = document.createElement("li");
  root.append(button("/", () => open("/")));
  crumbs.append(root);
  let sofar = "";
  for (const part of parts) {
    sofar += "/" + part;
    const target = sofar;
    const item = document.createElement("li");
    item.append(button(part, () => open(target)));
    crumbs.append(item);
  }
}

let opening = 0;

async function open(path) {
  errorText.hidden = true;
  const request = ++opening;
  let listing;
  try {
    listing = await get("browse", { path });
    if (request !== opening) {
      return;                   // another folder was asked for meanwhile
    }
  } catch (error) {
    if (request !== opening) {
      return;
    }
    if (kind === "tracks" && /\.gpx$/i.test(path)) {
      // A track file typed or pasted in the field
      done({ files: [path] });
      return;
    }
    errorText.textContent = error.message;
    errorText.hidden = false;
    return;
  }
  current = listing;
  checked = new Set();
  pathField.value = listing.path;
  showCrumbs(listing.path);
  entries.replaceChildren();
  if (listing.parent) {
    entries.append(entry("↰", "..", "", () => open(listing.parent)));
  }
  for (const folder of listing.folders) {
    entries.append(entry("📁", folder.name, counts(folder),
      () => open(join(listing.path, folder.name))));
  }
  if (kind === "tracks") {
    for (const name of listing.tracks) {
      const path = join(listing.path, name);
      const box = document.createElement("input");
      box.type = "checkbox";
      const item = entry("", name, "", (event) => {
        if (event.target !== box) {
          box.checked = !box.checked;
        }
        if (box.checked) {
          checked.add(path);
        } else {
          checked.delete(path);
        }
        updateAccept();
      });
      item.querySelector(".icon").append(box);
      entries.append(item);
    }
  }
  if (!entries.children.length || (listing.parent && entries.children.length === 1)) {
    const empty = document.createElement("li");
    empty.className = "counts";
    empty.textContent = kind === "photos"
      ? format(ngettext("No subfolders; {count} photo here", "No subfolders; {count} photos here",
        listing.photos), { count: number(listing.photos) })
      : _("No subfolders or GPX files here");
    entries.append(empty);
  }
  updateAccept();
}

function done(result) {
  const resolve = finish;
  finish = null;
  dialog.close();
  if (resolve) {
    resolve(result);
  }
}

accept.addEventListener("click", () => {
  if (!current) {
    return;
  }
  if (kind === "tracks" && checked.size) {
    done({ files: [...checked] });
  } else {
    done({ folder: current.path, recursive: recursive.checked });
  }
});

document.getElementById("chooser-go").addEventListener("click", () => open(pathField.value));
pathField.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    open(pathField.value);
  }
});
dialog.addEventListener("close", () => {
  if (finish) {
    const resolve = finish;
    finish = null;
    resolve(null);
  }
});

// Resolves to {folder, recursive} or {files}, or null when cancelled
export function choose(which, start) {
  kind = which;
  title.textContent = which === "photos"
    ? _("Choose the folder with photos")
    : _("Choose the track: GPX files or a folder of them");
  recursive.checked = false;
  current = null;
  updateAccept();
  dialog.showModal();
  showPlaces();
  open(start || "");
  return new Promise((resolve) => { finish = resolve; });
}
