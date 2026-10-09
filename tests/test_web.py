"""The JavaScript of the page, run in Node.js where it has no browser parts.

Skipped without Node.js; the page itself is checked in the browser.
"""
import json
import os
import shutil
import subprocess

import pytest

from conftest import ROOT

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
JS = os.path.join(ROOT, "gpxfoto", "web", "js")


def run_module(name, code):
    """Run code with the exports of web/js/name in scope; return its JSON output."""
    script = (f"import * as m from {json.dumps(os.path.join(JS, name))};\n"
              f"const out = (() => {{ {code} }})();\n"
              "console.log(JSON.stringify(out));\n")
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True,
                            text=True, check=True)
    return json.loads(result.stdout)


POLISH = {
    "language": "pl", "locale": "pl-PL",
    "messages": {"Matched: {matched}": "Dopasowano: {matched}",
                 "{count} photo": ["{count} zdjęcie", "{count} zdjęcia", "{count} zdjęć"]},
    "plural": [2, 0] + [1 if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 2
                        for n in range(2, 200)],
}


@needs_node
def test_translations_and_plural_forms():
    out = run_module("i18n.js", f"""
        m.setCatalog({json.dumps(POLISH)});
        const forms = [0, 1, 2, 5, 12, 22, 25, 101, 102, 112, 1001, 1002, 10012].map(
            n => m.format(m.ngettext("{{count}} photo", "{{count}} photos", n), {{count: n}}));
        return [m._("Matched: {{matched}}"), m._("Not translated"), forms];
    """)
    assert out == ["Dopasowano: {matched}", "Not translated", [
        "0 zdjęć", "1 zdjęcie", "2 zdjęcia", "5 zdjęć", "12 zdjęć", "22 zdjęcia", "25 zdjęć",
        "101 zdjęć", "102 zdjęcia", "112 zdjęć", "1001 zdjęć", "1002 zdjęcia", "10012 zdjęć"]]


@needs_node
def test_without_a_catalog_the_english_text_is_used():
    out = run_module("i18n.js", """
        m.setCatalog({messages: {}, plural: []});
        return [m.ngettext("{count} photo", "{count} photos", 1),
                m.ngettext("{count} photo", "{count} photos", 3)];
    """)
    assert out == ["{count} photo", "{count} photos"]


@needs_node
def test_format_fills_in_only_known_placeholders():
    out = run_module("i18n.js", """
        return [m.format("{a} and {b}, not {c}", {a: 1, b: "x"}),
                m.format("{toString}", {})];
    """)
    assert out == ["1 and x, not {c}", "{toString}"]


@needs_node
def test_numbers_follow_the_locale():
    out = run_module("i18n.js", """
        m.setCatalog({locale: "pl-PL", messages: {}, plural: []});
        const polish = [m.number(1234.5, 1), m.number(0.25, 2)];
        m.setCatalog({locale: "not a locale!", messages: {}, plural: []});
        return [polish, m.number(3.14159, 2)];
    """)
    assert out[0][1] == "0,25" and out[0][0].replace(" ", " ") in ("1 234,5", "1234,5")
    assert out[1] == "3.14"


@needs_node
def test_exact_duration_matches_the_terminal():
    out = run_module("format.js", """
        return [0, 4, 4.5, 59, 60, 61, 3600, 3610, 3660, 3661.25, -1800, 86400].map(
            s => m.exactDuration(s, true));
    """)
    assert out == ["+0 s", "+4 s", "+4.5 s", "+59 s", "+1 min", "+1 min 1 s", "+1 h",
                   "+1 h 0 min 10 s", "+1 h 1 min", "+1 h 1 min 1.25 s", "−30 min", "+24 h"]


@needs_node
def test_clock_times_keep_the_photo_s_time_zone():
    out = run_module("format.js", """
        return [m.clockTime("2024-05-01T12:00:50.000+02:00"), m.clockTime(null),
                m.clockTimeOfUnix(1714557650, "2024-05-01T12:00:50.000+02:00"),
                m.clockTimeOfUnix(1714557650, "2024-05-01T06:00:50.000-04:00")];
    """)
    assert out == ["12:00:50", "", "12:00:50", "06:00:50"]


@needs_node
def test_coordinates_in_the_regional_format():
    out = run_module("format.js", """
        return m.coordinates(50.0005, -20.001);
    """)
    assert out == "50.000500, -20.001000"


def test_every_text_of_the_page_is_marked_for_translation():
    """Texts in index.html are translated by data-text and data-label, from
    the list in texts.js that xgettext reads."""
    import re
    with open(os.path.join(ROOT, "gpxfoto", "web", "index.html"), encoding="utf-8") as f:
        page = f.read()
    with open(os.path.join(JS, "texts.js"), encoding="utf-8") as f:
        marked = set(re.findall(r'N_\("((?:[^"\\]|\\.)*)"\)', f.read()))
    used = set(re.findall(r'data-(?:text|label)="([^"]*)"', page))
    assert used and used <= marked
    assert marked - used == set()


@needs_node
def test_colours_of_the_map_styles_are_read():
    out = run_module("styles.js", """
        return ["#fff", "#f8f4f0", "#ffffff80", "rgb(158,189,255)", "rgba(176, 213, 154, 1)",
                "rgb(27 ,27 ,29)", "hsl(35,8%,85%)", "hsla(98,61%,72%,0.7)", "interpolate",
                "red", "#12345", "rgb(1,2)"].map(c => m.parseColor(c) && m.formatColor(m.parseColor(c)));
    """)
    assert out == ["hsla(0,0%,100%,1)", "hsla(30,36.4%,95.7%,1)", "hsla(0,0%,100%,0.502)",
                   "hsla(220.8,100%,81%,1)", "hsla(97.6,41.3%,72%,1)", "hsla(240,3.6%,11%,1)",
                   "hsla(35,8%,85%,1)", "hsla(98,61%,72%,0.7)", None, None, None, None]


@needs_node
def test_the_dark_map_is_made_from_the_light_one():
    light = {
        "version": 8, "sources": {}, "layers": [
            {"id": "background", "type": "background", "paint": {"background-color": "#f8f4f0"}},
            {"id": "wood", "type": "fill", "paint": {"fill-color": [
                "interpolate", ["linear"], ["zoom"], 9, "hsla(98,61%,72%,0.7)", 12, "#fff"]}},
            {"id": "path", "type": "line", "paint": {"line-color": "hsl(0,0%,100%)"}},
            {"id": "label", "type": "symbol", "layout": {"text-field": "{name}"},
             "paint": {"text-halo-color": "#fff"}},
            {"id": "wetland", "type": "fill", "paint": {"fill-pattern": "wetland",
                                                       "fill-opacity": 0.8}},
            {"id": "relief", "type": "raster", "source": "x"},
        ]}
    out = run_module("styles.js", f"""
        const light = {json.dumps(light)};
        const before = JSON.stringify(light);
        const dark = m.darkStyle(light);
        const lightness = c => m.parseColor(c).l;
        const paint = id => dark.layers.find(l => l.id === id).paint;
        return {{
            unchanged: JSON.stringify(light) === before,
            background: lightness(paint("background")["background-color"]),
            wood: paint("wood")["fill-color"].slice(0, 4),
            woodLight: lightness(paint("wood")["fill-color"][4]),
            path: lightness(paint("path")["line-color"]),
            label: lightness(paint("label")["text-color"]),
            halo: lightness(paint("label")["text-halo-color"]),
            wetland: paint("wetland")["fill-opacity"],
            relief: paint("relief")["raster-brightness-max"],
        }};
    """)
    assert out["unchanged"]
    # Dark, but not black: the land, its cover and the paths can be told apart
    assert 0.15 < out["background"] < 0.2
    assert out["wood"] == ["interpolate", ["linear"], ["zoom"], 9]
    assert out["background"] < out["woodLight"] < out["path"]
    assert out["path"] > 0.45
    assert out["halo"] < 0.15 < 0.85 < out["label"]
    assert out["wetland"] == pytest.approx(0.28)
    assert out["relief"] < 1


@needs_node
def test_photos_at_one_place_fan_out_apart():
    out = run_module("fan.js", """
        return [1, 2, 5, 8, 9, 40].map(n => {
            const o = m.fanOffsets(n);
            let apart = Infinity;
            for (let i = 0; i < n; i++) {
                for (let j = i + 1; j < n; j++) {
                    apart = Math.min(apart, Math.hypot(o[i][0] - o[j][0], o[i][1] - o[j][1]));
                }
            }
            const near = Math.min(...o.map(([x, y]) => Math.hypot(x, y)));
            return [o.length, apart, near];
        });
    """)
    for n, (count, apart, near) in zip([1, 2, 5, 8, 9, 40], out):
        assert count == n
        # Thumbnails 40 px across neither touch each other nor their place
        assert (apart is None or apart > 43) and near > 43.9
