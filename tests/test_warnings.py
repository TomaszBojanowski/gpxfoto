"""The cards of the page about a suspicious match (gpxfoto.server.warnings)."""
from datetime import datetime, timedelta, timezone

import pytest

from gpxfoto.engine.matching import match_photos, placed_by_hand
from gpxfoto.engine.photos import TZ_CAMERA, Photo
from gpxfoto.engine.track import Track, find_stops
from gpxfoto.engine.checks import ShiftHint
from gpxfoto.server.warnings import JUMP_EXAMPLES, _shift_text, warnings_of
from test_checks import STOP_MINUTES, in_pauses, pauses_hike, stops_hike
from test_stops import T0 as HIKE_T0

T0 = datetime(2024, 5, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp()
# A point every ten seconds; every ten minutes, at once 111 km further north
POINTS = [(T0 + 600 * leap + second, 50.0 + leap, 20.0, 200.0)
          for leap in range(8) for second in range(0, 600, 10)]


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "C")


@pytest.fixture
def track():
    return Track(["track.gpx"], True, list(POINTS))


def photo(name, seconds, hours=2):
    zone = timezone(timedelta(hours=hours))
    return Photo(name, datetime.fromtimestamp(T0 + seconds, zone), TZ_CAMERA, None, False)


def test_a_match_without_signs_has_no_cards(track):
    photos = [photo("a.jpg", 10), photo("b.jpg", 20), photo("c.jpg", 900)]
    assert warnings_of(match_photos(photos, [track], 0, 120), [track], 120) == []
    assert warnings_of([], [], 120) == []


def test_a_pair_across_a_leap_is_named(track):
    photos = [photo("x.jpg", 10), photo("/photos/a.jpg", 590), photo("/photos/b.jpg", 600)]
    [card] = warnings_of(match_photos(photos, [track], 0, 120), [track], 120)
    assert card["kind"] == "jumps" and card["text"] and card["advice"]
    assert card["title"] == "Position jumps (1 pair)"
    assert card["items"] == [{"photos": [1, 2], "text": "a → b · 10 s · 111.2 km",
                              "detail": "a.jpg and b.jpg: taken 10 s apart, placed 111.2 km "
                                        "apart"}]
    assert card["more"] is None
    assert card["notes"] == {1: ["jump: 111.2 km from b in 10 s"],
                             2: ["jump: 111.2 km from a in 10 s"]}


def test_only_some_pairs_are_named_and_all_are_counted(track):
    photos = [photo(f"{leap}{side}.jpg", 600 * leap + second)
              for leap in range(1, 6) for side, second in (("a", -10), ("b", 0))]
    [card] = warnings_of(match_photos(photos, [track], 0, 120), [track], 120)
    assert [item["photos"] for item in card["items"]] == [[0, 1], [2, 3], [4, 5]]
    assert len(card["items"]) == JUMP_EXAMPLES and card["more"] == "+2"
    assert card["title"] == "Position jumps (5 pairs)"
    assert sorted(card["notes"]) == list(range(10))


def test_different_time_zones_are_told(track):
    # The second camera clock reads an hour less at the same moment
    photos = [photo("a.jpg", 590), photo("b.jpg", 600, hours=1)]
    results = match_photos(photos, [track], 0, 120)
    assert warnings_of(results, [track], 120) == []
    late = [photo("a.jpg", 590), photo("b.jpg", 4200, hours=1)]
    [card] = warnings_of(match_photos(late, [track], 0, 120), [track], 120)
    assert card["items"][0]["text"] == "a → b · 10 s · 778.4 km · UTC+02:00 → UTC+01:00"
    assert card["items"][0]["detail"] == ("a.jpg and b.jpg: taken 10 s apart, placed 778.4 km "
                                          "apart, time zones UTC+02:00 and UTC+01:00")


def test_photos_placed_by_hand_are_left_out(track):
    photos = [photo("a.jpg", 590), photo("b.jpg", 600), photo("c.jpg", 605)]
    results = match_photos(photos, [track], 0, 120)
    assert sorted(warnings_of(results, [track], 120)[0]["notes"]) == [0, 1]
    by_hand = list(results)
    by_hand[0] = placed_by_hand(photos[0], 0, 10.0, 10.0)
    # Far from the others, but put there on purpose
    assert warnings_of(by_hand, [track], 120, placed={0: (10.0, 10.0)}) == []
    by_hand = list(results)
    by_hand[1] = placed_by_hand(photos[1], 0, 10.0, 10.0)
    [card] = warnings_of(by_hand, [track], 120, placed={1: (10.0, 10.0)})
    assert sorted(card["notes"]) == [0, 2] and card["items"][0]["photos"] == [0, 2]


def test_a_photo_between_two_leaps_has_a_note_of_each(track):
    quick = Track(["quick.gpx"], True, [(T0 + second, 50.0 + second // 20, 20.0, 200.0)
                                        for second in range(0, 600, 5)])
    photos = [photo("a.jpg", 15), photo("b.jpg", 25), photo("c.jpg", 45)]
    [card] = warnings_of(match_photos(photos, [quick], 0, 120), [quick], 120)
    assert card["notes"][1] == ["jump: 111.2 km from a in 10 s", "jump: 111.2 km from c in 20 s"]


# --- a whole-hour shift ---------------------------------------------------

@pytest.fixture(scope="module")
def hike():
    points = stops_hike()
    return Track(["hike.gpx"], True, points, find_stops(points))


def at_the_stops(error):
    """Photos taken during the stops of the hike by a clock error s ahead."""
    zone = timezone(timedelta(hours=2))
    return [Photo(f"{n}.jpg", datetime.fromtimestamp(HIKE_T0 + minute * 60 + second + error, zone),
                  TZ_CAMERA, None, False)
            for n, (minute, second) in enumerate((m, s) for m in STOP_MINUTES for s in (40, 80))]


def test_a_clock_an_hour_behind_gets_a_card_that_applies_the_shift(hike):
    results = match_photos(at_the_stops(-3600), [hike], 0, 120)
    [card] = warnings_of(results, [hike], 120, limit=86400)
    assert (card["kind"], card["title"]) == ("shift", "Shift the photo times by +1 h?")
    # The first six photos are from before the start of the track
    assert card["text"] == ("Now no photo falls during a stop, and 6 of 12 are outside the time "
                            "of the track. Shifted by +1 h, all 12 fall during stops.")
    assert "summer time" in card["advice"]
    assert card["action"] == {"label": "Apply +1 h", "correction": 3600}
    assert (card["items"], card["more"], card["notes"]) == ([], None, {})
    # Applied, the photos are at the stops and nothing is left to warn of
    applied = match_photos(at_the_stops(-3600), [hike], 3600, 120)
    assert sum(result.stop is not None for result in applied) == 12
    assert warnings_of(applied, [hike], 120, correction=3600) == []


def test_the_shift_is_added_to_the_correction_in_effect(hike):
    # Two hours and ten minutes ahead, of which the correction takes the minutes
    results = match_photos(at_the_stops(7800), [hike], -600, 120)
    [card] = warnings_of(results, [hike], 120, correction=-600, limit=86400)
    assert card["title"] == "Shift the photo times by -2 h?" and "time zone" in card["advice"]
    assert "summer time" not in card["advice"]
    assert card["action"] == {"label": "Apply -2 h", "correction": -7800}


def test_a_shift_beyond_the_limit_of_corrections_has_no_button(hike):
    results = match_photos(at_the_stops(-3600), [hike], 0, 120)
    [card] = warnings_of(results, [hike], 120, limit=3000)
    assert card["kind"] == "shift" and card["action"] is None


def test_no_shift_is_looked_for_without_stops(hike):
    results = match_photos(at_the_stops(-3600), [hike], 0, 120)
    assert warnings_of(results, [hike], 120, stops=False) == []


@pytest.mark.parametrize("hint, total, outside, expected", [
    (ShiftHint(3600, 12, 12, 6, 0), 12, 6,
     "Now no photo falls during a stop, and 6 of 12 are outside the time of the track. "
     "Shifted by +1 h, all 12 fall during stops."),
    # No photo is outside the track: nothing is said of that
    (ShiftHint(-1800, 12, 12, 6, 0), 12, 0,
     "Now no photo falls during a stop. Shifted by -30 min, all 12 fall during stops."),
    (ShiftHint(3600, 11, 14, 6, 1), 14, 1,
     "Now 1 photo falls during a stop, and 1 of 14 is outside the time of the track. "
     "Shifted by +1 h, 11 of 14 fall during stops."),
    # With the shift, two photos are still off the track
    (ShiftHint(7200, 10, 10, 5, 3), 12, 0,
     "Now 3 photos fall during stops. Shifted by +2 h, 10 of 10 fall during stops."),
])
def test_the_text_of_a_shift_tells_of_now_and_then(hint, total, outside, expected):
    assert _shift_text(hint, total, outside) == expected


# --- photos in motion -----------------------------------------------------

@pytest.fixture(scope="module")
def walk():
    points = pauses_hike()
    return Track(["walk.gpx"], True, points, find_stops(points))


def in_the_pauses(count, error):
    """Photos taken in the pauses of the walk by a clock error s ahead."""
    zone = timezone(timedelta(hours=2))
    return [Photo(f"{n}.jpg", datetime.fromtimestamp(t, zone), TZ_CAMERA, None, False)
            for n, t in enumerate(in_pauses(count, error))]


def test_photos_at_full_pace_get_a_card(walk):
    # The clock is 90 s ahead: every photo lands while walking
    results = match_photos(in_the_pauses(20, 90), [walk], 0, 120)
    [card] = warnings_of(results, [walk], 120)
    assert (card["kind"], card["title"]) == ("motion", "Photos in motion (20 of 20)")
    assert card["text"] == ("20 of 20 matched photos were taken while the track shows movement "
                            "at full pace, so the camera clock may be off.")
    assert "until this warning goes, or hide it" in card["advice"]
    assert (card["items"], card["more"], card["notes"], card["action"]) == ([], None, {}, None)


def test_photos_where_the_walker_paused_get_no_card(walk):
    results = match_photos(in_the_pauses(20, 0), [walk], 0, 120)
    assert warnings_of(results, [walk], 120) == []
    # With the clock corrected, the photos of the wrong clock are there too;
    # the pauses of 20 s are too short to be stops, so none is at a stop
    results = match_photos(in_the_pauses(20, 90), [walk], -90, 120)
    assert warnings_of(results, [walk], 120, correction=-90) == []
    assert walk.stops == [] and not any(result.stop for result in results)


def test_the_card_of_a_shift_comes_first_and_that_of_motion_not_with_it(hike):
    photos = at_the_stops(-3600)
    results = match_photos(photos, [hike], 0, 120)
    assert [card["kind"] for card in warnings_of(results, [hike], 120)] == ["shift"]
