"""The signs of a suspicious match, as the page shows them.

Each is a card: {"kind", "title", "text", "items", "more", "advice",
"notes", "action"}. items are {"text", "detail", "photos"}, the examples
the card lists: a short line, the same in full, and the ids of its photos;
more is a short text such as “+2” when some were left out, or None. notes
maps the id of each photo the card is about to the short lines its row
says. action is {"label", "correction"}, a button that sets the clock
correction to that many seconds, or None. The cards only inform: nothing
changes unless the user presses that button, and they never stop the
locations from being written.
"""
import math
import os
from datetime import timedelta
from gettext import gettext as _, ngettext

from gpxfoto import i18n
from gpxfoto.engine.matching import shots_of, suspicion
from gpxfoto.engine.photos import format_utc_offset

# At most this many pairs of photos are named in the card about jumps
JUMP_EXAMPLES = 3


def warnings_of(results, tracks, max_gap, stops=True, placed=(), correction=0.0,
                limit=math.inf):
    """The cards for results, the PhotoResult of each photo by its id, on tracks.

    placed are the ids of the photos placed by hand, which the checks
    leave out: where they are says nothing about the camera clock.
    correction is the clock correction the results were made with, and
    limit how far a correction may go, both in seconds.
    """
    ids = [index for index in range(len(results)) if index not in placed]
    checked = [results[index] for index in ids]
    shots, indices = shots_of(checked)
    ids = [ids[index] for index in indices]
    found = suspicion(checked, tracks, shots, max_gap, stops)
    cards = []
    if found.shift is not None:
        # Of the photos with a capture time, those no track covers
        outside = sum(not checked[index].covered for index in indices)
        cards.append(_shift(found.shift, len(shots), outside, correction, limit))
    if found.motion is not None:
        cards.append(_motion(found.motion))
    if found.jumps:
        cards.append(_jumps(found.jumps, shots, ids, results))
    return cards


def _shift_text(hint, total, outside):
    """What the shift changes: where the photos fall now, then with the shift.

    total photos have a capture time, and outside of them are taken at a
    time that no track covers.
    """
    if hint.pinned_now:
        # Translators: the start of a sentence, about the photos as they are
        # placed now; {count} is a number of photos
        now = ngettext("Now {count} photo falls during a stop",
                       "Now {count} photos fall during stops", hint.pinned_now).format(
                           count=i18n.number(hint.pinned_now))
    else:
        # Translators: the start of a sentence, about the photos as they are
        # placed now
        now = _("Now no photo falls during a stop")
    if outside:
        # Translators: the end of a sentence that starts with “Now no photo
        # falls during a stop” or “Now {count} photos fall during stops”;
        # {count} and {total} are numbers of photos
        now = ngettext("{now}, and {count} of {total} is outside the time of the track.",
                       "{now}, and {count} of {total} are outside the time of the track.",
                       outside).format(now=now, count=i18n.number(outside),
                                       total=i18n.number(total))
    else:
        now += "."
    shift = i18n.exact_duration(hint.shift, sign=True)
    if hint.pinned == hint.matched == total:
        # Translators: {shift} is a whole number of hours or half an hour with
        # a sign, such as “+1 h” or “-30 min”; {count} is the number of all
        # the photos
        then = ngettext("Shifted by {shift}, the {count} photo falls during a stop.",
                        "Shifted by {shift}, all {count} fall during stops.", total).format(
                            shift=shift, count=i18n.number(total))
    else:
        # Translators: {shift} is a whole number of hours or half an hour with
        # a sign, such as “+1 h” or “-30 min”; {pinned} of the {matched}
        # photos on the track then fall during stops
        then = ngettext("Shifted by {shift}, {pinned} of {matched} falls during a stop.",
                        "Shifted by {shift}, {pinned} of {matched} fall during stops.",
                        hint.pinned).format(shift=shift, pinned=i18n.number(hint.pinned),
                                            matched=i18n.number(hint.matched))
    return now + " " + then


def _shift(hint, total, outside, correction, limit):
    """The card about a whole-hour shift that puts clearly more photos at stops."""
    shift = i18n.exact_duration(hint.shift, sign=True)
    if abs(hint.shift) == 3600:
        advice = _("A difference of exactly one hour usually means that the camera "
                   "was not switched to or from summer time, or that its time zone "
                   "is set wrong.")
    else:
        advice = _("A difference of whole hours or of half an hour usually means "
                   "that the time zone set in the camera is wrong, for example "
                   "still the home one while travelling.")
    action = None
    if abs(correction + hint.shift) <= limit:
        # Translators: a button; {shift} is a whole number of hours or half
        # an hour with a sign, such as “+1 h” or “-30 min”
        action = {"label": _("Apply {shift}").format(shift=shift),
                  "correction": correction + hint.shift}
    return {
        "kind": "shift",
        # Translators: the title of a warning, which must stay short; {shift}
        # is a whole number of hours or half an hour with a sign, such as
        # “+1 h” or “-30 min”
        "title": _("Shift the photo times by {shift}?").format(shift=shift),
        "text": _shift_text(hint, total, outside),
        "items": [], "more": None, "advice": advice, "notes": {}, "action": action,
    }


def _motion(motion):
    """The card about photos taken while the track moves at full pace."""
    values = dict(fast=i18n.number(motion.fast), matched=i18n.number(motion.matched))
    return {
        "kind": "motion",
        # Translators: the title of a warning, which must stay short; {fast}
        # and {matched} are numbers of photos
        "title": _("Photos in motion ({fast} of {matched})").format(**values),
        # Translators: {fast} and {matched} are numbers of photos
        "text": ngettext("{fast} of {matched} matched photo was taken while the track shows "
                         "movement at full pace, so the camera clock may be off.",
                         "{fast} of {matched} matched photos were taken while the track shows "
                         "movement at full pace, so the camera clock may be off.",
                         motion.matched).format(**values),
        "items": [], "more": None,
        # The warning goes where the track slows down, also for pauses too
        # short to be stops, so the advice does not send the user to the stops
        "advice": _("Photos are usually taken at stops or while slowing down. Move the time "
                    "correction until this warning goes, or hide it if the photos really "
                    "were taken on the move."),
        "notes": {}, "action": None,
    }


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
        "action": None,
    }
