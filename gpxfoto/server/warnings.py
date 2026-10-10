"""The signs of a suspicious match, as the page shows them.

Each is a card: {"kind", "title", "text", "items", "advice", "note",
"photos"}. items are {"text", "detail", "photos"}, the examples the card
lists: a short line, the same in full, and the ids of its photos. The
title tells how many there are in all. photos are the ids of all the
photos the card is about, and note is what the row of each of them says.
The cards only inform: they change nothing and never stop the locations
from being written.
"""
import os
from datetime import timedelta
from gettext import gettext as _, ngettext

from gpxfoto import i18n
from gpxfoto.engine.matching import shots_of, suspicion
from gpxfoto.engine.photos import format_utc_offset

# At most this many pairs of photos are named in the card about jumps
JUMP_EXAMPLES = 3


def warnings_of(results, tracks, max_gap, stops=True, placed=()):
    """The cards for results, the PhotoResult of each photo by its id, on tracks.

    placed are the ids of the photos placed by hand, which the checks
    leave out: where they are says nothing about the camera clock.
    """
    ids = [index for index in range(len(results)) if index not in placed]
    checked = [results[index] for index in ids]
    shots, indices = shots_of(checked)
    ids = [ids[index] for index in indices]
    found = suspicion(checked, tracks, shots, max_gap, stops)
    cards = []
    if found.jumps:
        cards.append(_jumps(found.jumps, shots, ids, results))
    return cards


def _jumps(jumps, shots, ids, results):
    """The card about photos taken one after the other but placed far apart."""
    items = []
    for jump in jumps[:JUMP_EXAMPLES]:
        pair = (jump.first, jump.second)
        zones = [timedelta(seconds=round(shots[i].clock - shots[i].time)) for i in pair]
        first, second = (os.path.basename(results[ids[i]].photo.path) for i in pair)
        values = dict(first=first, second=second,
                      interval=i18n.exact_duration(jump.clock_gap),
                      distance=i18n.distance(jump.distance))
        # The short line names the photos without their extensions
        short = [" → ".join(os.path.splitext(name)[0] for name in (first, second)),
                 values["interval"], values["distance"]]
        if zones[0] == zones[1]:
            detail = _("{first} and {second}: taken {interval} apart, placed {distance} "
                       "apart").format(**values)
        else:
            shown = [_("UTC{offset}").format(offset=format_utc_offset(zone)) for zone in zones]
            short.append(" → ".join(shown))
            detail = _("{first} and {second}: taken {interval} apart, placed {distance} apart, "
                       "time zones {first_zone} and {second_zone}").format(
                           first_zone=shown[0], second_zone=shown[1], **values)
        items.append({"text": " · ".join(short), "detail": detail,
                      "photos": [ids[i] for i in pair]})
    return {
        "kind": "jumps",
        # Translators: the title of a warning, which must stay short; {count}
        # is the number of pairs of photos placed implausibly far apart
        "title": ngettext("Position jumps ({count} pair)", "Position jumps ({count} pairs)",
                          len(jumps)).format(count=i18n.number(len(jumps))),
        "text": _("These photos were taken less than a minute apart, but are placed far "
                  "from each other:"),
        "items": items,
        "advice": _("Check the time zones of these photos, and whether the GPX files "
                    "record different trips at the same time."),
        # Translators: a note on a photo in the list
        "note": _("placed implausibly far from a photo taken less than a minute apart"),
        "photos": sorted({ids[i] for jump in jumps for i in (jump.first, jump.second)}),
    }
