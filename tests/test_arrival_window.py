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
