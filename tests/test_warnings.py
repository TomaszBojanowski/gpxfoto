"""The cards of the page about a suspicious match (gpxfoto.server.warnings)."""
from datetime import datetime, timedelta, timezone

import pytest

from gpxfoto.engine.matching import match_photos, placed_by_hand
from gpxfoto.engine.photos import TZ_CAMERA, Photo
from gpxfoto.engine.track import Track
from gpxfoto.server.warnings import JUMP_EXAMPLES, warnings_of

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
