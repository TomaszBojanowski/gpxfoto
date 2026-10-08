// Translations from the program's gettext catalogs, which the server
// gives out at /api/i18n, and numbers in the system's regional format.

let messages = {};
let plural = [];
let locale = "en";

export function setCatalog(catalog) {
  messages = catalog.messages || {};
  plural = catalog.plural || [];
  locale = catalog.locale || catalog.language || "en";
  if (typeof document !== "undefined") {
    document.documentElement.lang = catalog.language || "en";
  }
}

export async function loadCatalog() {
  const response = await fetch("/api/i18n");
  setCatalog(await response.json());
}

export function _(text) {
  const translated = messages[text];
  return typeof translated === "string" && translated ? translated : text;
}

export function ngettext(one, many, n) {
  const forms = messages[one];
  if (Array.isArray(forms)) {
    const count = Math.abs(Math.trunc(n));
    const index = count < 200 ? plural[count] : plural[100 + (count % 100)];
    if (forms[index]) {
      return forms[index];
    }
  }
  return n === 1 ? one : many;
}

// Fills in {name} placeholders, as Python's str.format() does
export function format(text, values) {
  return text.replace(/\{(\w+)\}/g, (whole, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole);
}

export function number(value, decimals = 0) {
  try {
    return new Intl.NumberFormat(locale, {
      minimumFractionDigits: decimals, maximumFractionDigits: decimals,
    }).format(value);
  } catch (error) {
    return value.toFixed(decimals);
  }
}

// The locale for formatting numbers, one the browser knows
export function currentLocale() {
  try {
    return Intl.NumberFormat.supportedLocalesOf([locale]).length ? locale : "en";
  } catch (error) {
    return "en";
  }
}
