"""The signs of a suspicious match, as the page shows them.

Each is a card: {"kind", "title", "text", "items", "more", "advice",
"notes"}. items are {"text", "detail", "photos"}, the examples the card
lists: a short line, the same in full, and the ids of its photos; more is
a short text such as “+2” when some were left out, or None. notes maps
the id of each photo the card is about to the short lines its row says.
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
    notes = {}
    for number, jump in enumerate(jumps):
        pair = (jump.first, jump.second)
        zones = [timedelta(seconds=round(shots[i].clock - shots[i].time)) for i in pair]
        first, second = (os.path.basename(results[ids[i]].photo.path) for i in pair)
        values = dict(first=first, second=second,
                      interval=i18n.exact_duration(jump.clock_gap),
                      distance=i18n.distance(jump.distance))
        # The short lines name the photos without their extensions
        stems = [os.path.splitext(name)[0] for name in (first, second)]
        for i, other in zip(pair, reversed(stems)):
            # Translators: a note on a photo in the list, which must stay
            # short; {name} is the other photo, {distance} a distance such
            # as “1.3 km” and {interval} a time span such as “4 s”
            note = _("jump: {distance} from {name} in {interval}")
            notes.setdefault(ids[i], []).append(note.format(name=other, **values))
        if number >= JUMP_EXAMPLES:
            continue
        short = [" → ".join(stems), values["interval"], values["distance"]]
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
        "more": None if len(jumps) <= JUMP_EXAMPLES else "+" + i18n.number(
            len(jumps) - JUMP_EXAMPLES),
        "advice": _("Check the time zones of these photos, and whether the GPX files "
                    "record different trips at the same time."),
        "notes": notes,
    }
