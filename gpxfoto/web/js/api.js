// Requests to the local server and the stream of its events.

export class ApiError extends Error {}

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
  return answer(await fetch("/api/" + name + (query ? "?" + query : "")));
}

export async function post(name, body) {
  return answer(await fetch("/api/" + name, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

// Calls handlers[name](data) for each event; onOpen runs on every
// (re)connection, when the page must catch up with the state.
export function listen(handlers, onOpen) {
  const source = new EventSource("/api/events");
  source.addEventListener("open", onOpen);
  for (const name of Object.keys(handlers)) {
    source.addEventListener(name, (event) => handlers[name](JSON.parse(event.data)));
  }
  return source;
}

export function base64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}
