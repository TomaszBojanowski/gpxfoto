// Requests to the local server and the stream of its events.

import { _ } from "./i18n.js";

export class ApiError extends Error {}

const KEY = "gpxfoto-token";

// The token comes in the fragment of the address the program opened. It
// is kept for reloads of this tab and taken out of the address bar.
function readToken() {
  const given = location.hash.slice(1);
  if (given) {
    try {
      sessionStorage.setItem(KEY, given);
      // Out of the address bar only once it is kept for a reload
      history.replaceState(null, "", location.pathname);
    } catch (error) {
      // Kept in the address for reloads
    }
    return given;
  }
  try {
    return sessionStorage.getItem(KEY) || "";
  } catch (error) {
    return "";
  }
}

export const token = readToken();

// The address pasted into this tab differs only in the fragment, which
// does not load the page again by itself
addEventListener("hashchange", () => {
  if (location.hash.length > 1) {
    location.reload();
  }
});

// fetch, with the server's absence told in the user's language
async function request(url, options = {}) {
  try {
    return await fetch(url, { ...options,
      headers: { ...(options.headers || {}), "X-Gpxfoto-Token": token } });
  } catch (error) {
    throw new ApiError(_("gpxfoto is not running any more. Start it again with "
      + "“gpxfoto --ui”."));
  }
}

async function answer(response) {
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    throw new ApiError((data && data.error) || response.statusText);
  }
  return data;
}

export async function get(name, params = {}) {
  const query = new URLSearchParams(params).toString();
  return answer(await request("/api/" + name + (query ? "?" + query : "")));
}

export async function post(name, body) {
  return answer(await request("/api/" + name, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

// Calls handlers[name](data) for each event; onOpen runs on every
// (re)connection, when the page must catch up with the state.
export function listen(handlers, onOpen) {
  const source = new EventSource("/api/events?" + new URLSearchParams({ token }));
  source.addEventListener("open", onOpen);
  for (const name of Object.keys(handlers)) {
    // Only the server's events carry data, not those of the connection
    source.addEventListener(name, (event) => {
      if (typeof event.data === "string") {
        handlers[name](JSON.parse(event.data));
      }
    });
  }
  return source;
}

// The address of a request the browser makes itself, such as an image
export function address(name, params) {
  return "/api/" + name + "?" + new URLSearchParams({ ...params, token });
}

export function base64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}
