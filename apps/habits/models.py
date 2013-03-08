from __future__ import division

from collections import namedtuple
import datetime

from django.core.exceptions import ValidationError
from django.db import models
import django.dispatch
from django.utils.translation import ugettext_lazy as _

record_habit_data = django.dispatch.Signal()

RESOLUTION_NAMES = (
    _('daily'),
    _('on weekdays'),
    _('on weekends'),
    _('weekly'),
    _('monthly'),
)

RESOLUTIONS = (
    'day',
    'weekday',
    'weekendday',
    'week',
    'month',
)


def _validate_non_negative(val):
    if val < 0:
        raise ValidationError(u'%s is not greater than or equal to zero' % val)


TimePeriod = namedtuple('TimePeriod', 'resolution index date')


class Habit(models.Model):
    """
    A habit and its associated data.
    """

    user = models.ForeignKey('accounts.User', related_name='habits')
    resolution = models.CharField(
        max_length=10,
        choices=zip(RESOLUTIONS, RESOLUTION_NAMES),
        default='day',
    )
    start = models.DateField()
    target_count = models.PositiveIntegerField(default=1)

    def get_current_time_period(self):
        return get_time_period(datetime.date.today())

    def get_time_period(self, when, resolution=None):
        """
        Return the TimePeriod for the date given by ``when``, on the basis of
        the passed ``resolution`` (defaults to the Habit's ``resolution``).
        """
        if resolution is None:
            resolution = self.resolution

        idx = None
        date = None

        if resolution == 'day':
            idx = (when - self.start).days
            date = when

        elif resolution == 'week':
            start_week = self.start - datetime.timedelta(days=self.start.weekday())
            when_week = when - datetime.timedelta(days=when.weekday())

            idx = (when_week - start_week).days // 7
            date = when_week

        elif resolution in ['weekday', 'weekendday']:
            start_week = self.start - datetime.timedelta(days=self.start.weekday())
            when_wday = when.weekday()
            when_week = when - datetime.timedelta(days=when_wday)

            num_weekend_days = 2 * (when_week - start_week).days // 7

            # If start is a Sunday, one less weekendday
            if self.start.weekday() == 6:
                num_weekend_days -= 1
            # If when is a Saturday, one more weekendday
            if when.weekday() == 5:
                num_weekend_days += 1
            # If when is a Sunday, two more weekenddays
            if when.weekday() == 6:
                num_weekend_days += 2

            num_days = (when - self.start).days + 1

            if resolution == 'weekday':
                idx = num_days - num_weekend_days - 1
                if idx < 0:
                    raise ValueError("No weekdays have passed since start")
                if when_wday < 5:
                    date = when
                else:
                    date = when - datetime.timedelta(days=(when_wday - 4))
            else:
                idx = num_weekend_days - 1
                if idx < 0:
                    raise ValueError("No weekends have passed since start")
                if when_wday >= 5:
                    date = when
                else:
                    date = when_week - datetime.timedelta(days=1)

        elif resolution == 'month':
            start_month = self.start.replace(day=1)
            when_month = when.replace(day=1)

            year_diff = when_month.year - start_month.year
            month_diff = when_month.month - start_month.month

            idx = year_diff * 12 + month_diff
            date = when_month

        if idx is not None:
            return TimePeriod(resolution, idx, date)
        else:
            raise RuntimeError("Unhandled resolution: %s" % resolution)

    def record(self, time_period, value):
        """
        Record a data point for the habit in a time period and all lower
        resolution time periods.
        """

        if value < 0:
            raise ValueError("value must not be negative")
        if not isinstance(time_period, TimePeriod):
            raise ValueError("when must be a TimePeriod")
        if time_period.resolution != self.resolution:
            raise ValueError("passed TimePeriod must have resolution matching Habit")

        _increment_bucket(self, time_period, value)

        if self.resolution in ['day', 'weekday', 'weekendday']:
            tp = self.get_time_period(time_period.date, 'week')
            _increment_bucket(self, tp, value)

        if self.resolution != 'month':
            tp = self.get_time_period(time_period.date, 'month')
            _increment_bucket(self, tp, value)

        record_habit_data.send(sender=self)

    def __unicode__(self):
        return 'Habit(start=%s resolution=%s)' % (self.start, self.resolution)

def _increment_bucket(habit, time_period, value):
    try:
        b = habit.buckets.get(resolution=time_period.resolution,
                              index=time_period.index)
    except Bucket.DoesNotExist:
        b = Bucket(habit=habit,
                   resolution=time_period.resolution,
                   index=time_period.index)

    b.value += value
    b.save()
    return b

class Bucket(models.Model):
    """
    A value in a specified time resolution series for a habit
    """

    habit = models.ForeignKey(Habit, related_name='buckets')
    index = models.IntegerField(
        validators=[_validate_non_negative]
    )
    value = models.IntegerField(
        validators=[_validate_non_negative],
        default=0,
    )
    resolution = models.CharField(
        max_length=10,
        choices=zip(RESOLUTIONS, RESOLUTION_NAMES),
        default='day',
    )

    class Meta(object):
        unique_together = ['habit', 'resolution', 'index']