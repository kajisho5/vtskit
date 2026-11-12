"""The bitrate planner and the chapters it gives a title, one a session."""

import unittest

from vtskit import VtsError
from vtskit.plan import (DISCS, MAX_MUX, MAX_VIDEO, mux_bitrate, parse_chapters, parse_time, plan,
                         payload_bitrate)
from vtskit.titleset import Title

THREE_HOURS = """\
# a conference day, a line a session
0:00 Opening
0:12:30 Keynote
1:05:00 Panel: archiving live events
1:58:10.5 Lightning talks
2:41:00 Closing
"""


class TestParse(unittest.TestCase):
    def test_times(self):
        self.assertEqual(parse_time("0:00"), 0)
        self.assertEqual(parse_time("12:30"), 750)
        self.assertEqual(parse_time("1:05:00"), 3900)
        self.assertEqual(parse_time("1:58:10.5"), 7090.5)
        self.assertEqual(parse_time("90:00"), 5400)       # minutes past the hour, without hours
        self.assertEqual(parse_time("42.25"), 42.25)
        for bad in ("1:60:00", "0:61", "abc", "-3", "nan", ""):
            with self.assertRaises(VtsError, msg=bad):
                parse_time(bad)

    def test_chapter_list(self):
        s = parse_chapters(THREE_HOURS)
        self.assertEqual([x.start for x in s], [0, 750, 3900, 7090.5, 9660])
        self.assertEqual(s[2].label, "Panel: archiving live events")
        with self.assertRaisesRegex(VtsError, "line 2"):
            parse_chapters("0:00 a\nsoon b")


class TestPlan(unittest.TestCase):
    def test_three_hours_on_dvd9(self):
        # the roadmap's check: a 3 h chapter list plans on DVD-9
        p = plan(THREE_HOURS, 3 * 3600)
        self.assertEqual(p.disc.name, "DVD-9")            # DVD-5 is under the floor
        self.assertTrue(p.meets_floor)
        self.assertEqual(p.warnings, [])
        self.assertEqual(p.shortfall_bytes, 0)
        self.assertGreater(p.video_bitrate, 5_500_000)
        self.assertLessEqual(p.size, DISCS["dvd9"].bytes)
        self.assertEqual(p.chapters, (0, 750, 3900, 7090.5, 9660))
        self.assertIn("DVD-9", p.summary())
        # the chapters go into a title as they are
        self.assertEqual(Title(parts=(), chapters=p.chapters).chapters, p.chapters)

    def test_three_hours_on_dvd5_reports_the_shortfall(self):
        p = plan(THREE_HOURS, 3 * 3600, "dvd5")
        self.assertFalse(p.meets_floor)
        self.assertLess(p.video_bitrate, p.floor)
        self.assertGreater(p.shortfall_bytes, 0)
        self.assertAlmostEqual(p.duration - p.shortfall_seconds, p.max_duration)
        self.assertTrue(any("below the floor" in w for w in p.warnings))
        self.assertIn("short by", p.summary())
        # at the floor, the disc's room plus the shortfall is exactly the programme
        need = mux_bitrate(p.floor + p.audio_bitrate) * p.duration / 8
        room = (DISCS["dvd5"].bytes - 32 * 1024 * 1024) * 0.98
        self.assertAlmostEqual(room + p.shortfall_bytes, need, delta=1)

    def test_four_hours_too_long_for_a_high_floor(self):
        p = plan(THREE_HOURS, 4 * 3600, "dvd9", floor=5_000_000)
        self.assertFalse(p.meets_floor)
        self.assertAlmostEqual(p.shortfall_seconds, 4 * 3600 - p.max_duration)
        self.assertGreater(p.shortfall_seconds, 0)

    def test_auto_takes_dvd5_when_it_will_do(self):
        p = plan("0:00 One\n45:00 Two", 3600)
        self.assertEqual(p.disc.name, "DVD-5")
        self.assertTrue(p.meets_floor)

    def test_short_programme_is_capped(self):
        p = plan([0], 600, "dvd9", audio=448_000, audio_tracks=2)
        self.assertLessEqual(p.video_bitrate, MAX_VIDEO)
        self.assertLessEqual(p.mux_bitrate, MAX_MUX + 1)
        self.assertEqual(p.audio_bitrate, 896_000)

    def test_bitrate_falls_with_length_and_tracks(self):
        rates = [plan([0], h * 3600, "dvd9").video_bitrate for h in (1, 2, 3, 4)]
        self.assertEqual(rates, sorted(rates, reverse=True))
        self.assertGreater(plan([0], 3 * 3600, "dvd9").video_bitrate,
                           plan([0], 3 * 3600, "dvd9", audio_tracks=2, subtitles=40_000).video_bitrate)

    def test_payload_and_mux_are_inverse(self):
        for x in (1_000_000, 6_000_000, 9_000_000):
            self.assertAlmostEqual(payload_bitrate(mux_bitrate(x)), x)

    def test_chapters(self):
        p = plan([(300, "Late start"), ("1:00:00", "Second")], 7200, "dvd9")
        self.assertEqual(p.chapters, (0, 300, 3600))       # the first chapter is at 0
        self.assertEqual(p.sessions[1].label, "Late start")
        p = plan([0, 100, 100.5], 7200, "dvd9")
        self.assertTrue(any("under a second" in w for w in p.warnings))
        for bad, msg in (([0, 50, 40], "does not come after"), ([0, 8000], "before the end"),
                         (list(range(100)), "a title holds 99")):
            with self.assertRaisesRegex(VtsError, msg):
                plan(bad, 7200, "dvd9")

    def test_bad_arguments(self):
        for kw in ({"disc": "bd25"}, {"floor": 0}, {"floor": 12_000_000}, {"audio": -1},
                   {"margin": 1}, {"reserve": DISCS["dvd5"].bytes}, {"audio": 6_000_000, "audio_tracks": 2}):
            disc = kw.pop("disc", "dvd5")
            with self.assertRaises(VtsError, msg=str(kw)):
                plan([0], 3600, disc, **kw)
        with self.assertRaises(VtsError):
            plan([0], 0)


if __name__ == "__main__":
    unittest.main()
