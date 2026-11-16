"""Offline tests for the readers' delivery-claim arithmetic (app.adapters.base.arrival_window).

Lebanon keeps UTC+2 in winter and UTC+3 in summer; windows take the wider reading of both, so a
claim is never read as faster than the store said. Clocks are fixed and no store is contacted.
"""
from datetime import datetime, timedelta, timezone
import unittest

from app.adapters.base import arrival_window
from app.eligibility import check_eligibility
from tests.test_eligibility import check_of, deadline, make_offer, make_request

UTC = timezone.utc


def utc(*parts):
    return datetime(*parts, tzinfo=UTC)


def end_of_day(year, month, day):
    """The end of one Beirut day at UTC+2, the later of its two readings."""
    return utc(year, month, day, 21, 59, 59, 999999)


def window(observed, low, high, unit='working'):
    w = arrival_window(observed, low, high, unit=unit, basis='estimate')
    return w.earliest, w.latest


def beirut_day(month, day):
    """The widest reading of one Beirut day in 2026: midnight at UTC+3 to the end of the day at UTC+2."""
    return utc(2026, month, day) - timedelta(hours=3), end_of_day(2026, month, day)


class ArrivalWindowTests(unittest.TestCase):
    """The reader tests observe on a Saturday evening, where several miscounts give the same window."""

    def test_one_working_day_from_each_day_of_the_week(self):
        # Monday 9 to Sunday 15 November 2026, at 09:00 UTC; Saturday and Sunday are not working days.
        for start, day in {9: 10, 10: 11, 11: 12, 12: 13, 13: 16, 14: 16, 15: 16}.items():
            with self.subTest(start=start):
                self.assertEqual(window(utc(2026, 11, start, 9), 1, 1), beirut_day(11, day))

    def test_when_utc2_and_utc3_name_different_days_both_ends_widen(self):
        # 21:30 UTC on a Friday is 23:30 on Friday at UTC+2 and 00:30 on Saturday at UTC+3.
        for month, friday in ((11, 13), (7, 10)):
            with self.subTest(month=month):
                observed = utc(2026, month, friday, 21, 30)
                self.assertEqual(window(observed, 2, 3, 'calendar'),
                                 (beirut_day(month, friday + 2)[0], beirut_day(month, friday + 4)[1]))
                # The earliest reading of 'tomorrow' is already past, so it starts at the observation.
                self.assertEqual(window(observed, 1, 1, 'calendar'),
                                 (observed, beirut_day(month, friday + 2)[1]))

    def test_a_same_day_claim_starts_at_the_observation(self):
        observed = utc(2026, 11, 16, 9)
        self.assertEqual(window(observed, 0, 3), (observed, beirut_day(11, 19)[1]))


class HolidayTests(unittest.TestCase):
    def test_a_fixed_holiday_extends_the_latest_bound_of_a_working_day_window(self):
        # Wednesday 23 December 2026, 10:00 in Beirut, 'up to 3 working days': Friday 25 December is
        # Christmas, so the third working day is Tuesday 29 December, not Monday 28.
        observed = utc(2026, 12, 23, 8)
        self.assertEqual(window(observed, 0, 3), (observed, end_of_day(2026, 12, 29)))
        # Monday 22 March 2027: Thursday 25 March (the Annunciation) is skipped. Good Friday, a
        # movable holiday, is not modelled, so Friday 26 March counts as the third working day.
        observed = utc(2027, 3, 22, 8)
        self.assertEqual(window(observed, 0, 3), (observed, end_of_day(2027, 3, 26)))

    def test_holidays_never_move_the_earliest_bound_or_a_calendar_window(self):
        observed = utc(2026, 12, 23, 8)
        # Christmas still counts as a possible second working day for the earliest bound, so a
        # holiday can never be the reason an offer fails a deadline.
        self.assertEqual(window(observed, 2, 3)[0], utc(2026, 12, 24, 21))
        self.assertEqual(window(observed, 0, 3, 'calendar'), (observed, end_of_day(2026, 12, 26)))
        # A window with no holiday in it is unchanged: Monday 16 to Thursday 19 November 2026.
        self.assertEqual(window(utc(2026, 11, 16, 9), 0, 3)[1], end_of_day(2026, 11, 19))

    def test_a_holiday_keeps_an_estimate_from_meeting_a_deadline_it_may_miss(self):
        offer = make_offer(arrival=arrival_window(utc(2026, 12, 23, 8), 0, 3, unit='working',
                                                  basis='estimate').model_dump())
        request = make_request(deadline=deadline('estimate_ok', utc(2026, 12, 28, 22)))
        result = check_eligibility(request, offer)
        self.assertEqual(check_of(request, offer, 'deadline').outcome, 'unresolved')
        self.assertIn(('arrival', 'delivery window extends past the deadline'),
                      [(gap.field_path, gap.reason) for gap in result.gaps])


if __name__ == '__main__':
    unittest.main()
