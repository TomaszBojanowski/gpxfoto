// Times, time spans and positions as the page shows them; the texts are
// those of the terminal, so they share its translations.

import { _, format, number } from "./i18n.js";

// The clock time of an ISO time as recorded, e.g. "12:00:50", without
// converting it to the browser's time zone
export function clockTime(iso) {
  return iso ? iso.slice(11, 19) : "";
}

export function clockTimeOfUnix(seconds, isoForZone) {
  // The time zone of the photo, from its own time, e.g. "+02:00"
  const zone = isoForZone ? isoForZone.slice(-6) : "+00:00";
  const sign = zone[0] === "-" ? -1 : 1;
  const offset = sign * (Number(zone.slice(1, 3)) * 3600 + Number(zone.slice(4, 6)) * 60);
  const local = new Date((seconds + offset) * 1000);
  return local.toISOString().slice(11, 19);
}

// A time span to the millisecond, as gpxfoto.i18n.exact_duration()
export function exactDuration(seconds, sign = false) {
  const milliseconds = Math.round(Math.abs(seconds) * 1000);
  const hours = Math.floor(milliseconds / 3600000);
  const minutes = Math.floor((milliseconds % 3600000) / 60000);
  const rest = milliseconds % 60000;
  const secondText = rest % 1000
    ? number(rest / 1000, 3).replace(/0+$/, "")
    : number(rest / 1000);
  let text;
  if (hours && rest) {
    text = format(_("{hours} h {minutes} min {seconds} s"),
      { hours: number(hours), minutes: number(minutes), seconds: secondText });
  } else if (hours && minutes) {
    text = format(_("{hours} h {minutes} min"), { hours: number(hours), minutes: number(minutes) });
  } else if (hours) {
    text = format(_("{hours} h"), { hours: number(hours) });
  } else if (minutes && rest) {
    text = format(_("{minutes} min {seconds} s"), { minutes: number(minutes), seconds: secondText });
  } else if (minutes) {
    text = format(_("{minutes} min"), { minutes: number(minutes) });
  } else {
    text = format(_("{seconds} s"), { seconds: secondText });
  }
  if (sign) {
    return (seconds < 0 ? "−" : "+") + text;
  }
  return text;
}

// A time span as typed by the user, in seconds: a number of seconds, or
// minutes:seconds or hours:minutes:seconds, with an optional sign and a
// comma or a point before the fraction; null for anything else
export function parseDuration(text) {
  const match = /^\s*([+\-−]?)\s*(?:(?:(\d+):)?(\d+):)?(\d+(?:[.,]\d+)?)\s*s?\s*$/.exec(text);
  if (!match) {
    return null;
  }
  const [, sign, hours, minutes] = match;
  const seconds = Number(match[4].replace(",", "."));
  if ((minutes !== undefined && seconds >= 60) || (hours !== undefined && Number(minutes) >= 60)) {
    return null;
  }
  const value = Number(hours || 0) * 3600 + Number(minutes || 0) * 60 + seconds;
  if (!Number.isFinite(value)) {
    return null;
  }
  return (sign === "+" || sign === "" ? 1 : -1) * Math.round(value * 1000) / 1000;
}

// Latitude and longitude, separated by a semicolon where the decimal
// separator is a comma, as gpxfoto.i18n.coordinates()
export function coordinates(lat, lon) {
  const separator = number(1.5, 1).includes(",") ? "; " : ", ";
  return number(lat, 6) + separator + number(lon, 6);
}
