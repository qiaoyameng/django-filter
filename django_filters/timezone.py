from datetime import datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils.translation import gettext_lazy as _


RAISE = "raise"
EARLIER = "earlier"
LATER = "later"
SHIFT_FORWARD = "shift_forward"
SHIFT_BACKWARD = "shift_backward"

NONEXISTENT_POLICIES = {RAISE, SHIFT_FORWARD, SHIFT_BACKWARD}
AMBIGUOUS_POLICIES = {RAISE, EARLIER, LATER}


class TimezoneResolutionError(ValueError):
    pass


class NonexistentDateTimeError(ValueError):
    pass


class AmbiguousDateTimeError(ValueError):
    pass


def resolve_timezone(value):
    """Resolve a timezone name, tzinfo, or zero-argument provider."""
    if callable(value):
        try:
            value = value()
        except Exception as exc:
            raise TimezoneResolutionError(str(exc)) from exc
    if isinstance(value, tzinfo):
        return value
    if isinstance(value, bytes):
        value = value.decode()
    if isinstance(value, str):
        try:
            return ZoneInfo(value)
        except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise TimezoneResolutionError(str(exc)) from exc
    raise TimezoneResolutionError(_("A timezone name or tzinfo instance is required."))


def validate_policies(nonexistent, ambiguous):
    """Validate explicit policies for missing and repeated wall times."""
    if nonexistent not in NONEXISTENT_POLICIES:
        raise ValueError(_("Invalid nonexistent datetime policy."))
    if ambiguous not in AMBIGUOUS_POLICIES:
        raise ValueError(_("Invalid ambiguous datetime policy."))


def localize_datetime(value, tz, *, nonexistent, ambiguous):
    """Localize a naive wall time, resolving DST gaps and repetitions."""
    if value.tzinfo is not None:
        return value

    first_candidate = value.replace(tzinfo=tz, fold=0)
    second_candidate = value.replace(tzinfo=tz, fold=1)
    first_offset = first_candidate.utcoffset()
    second_offset = second_candidate.utcoffset()

    if first_offset == second_offset:
        return first_candidate

    transition_duration = abs(first_offset - second_offset)
    first_utc = first_candidate.astimezone(timezone.utc)
    second_utc = second_candidate.astimezone(timezone.utc)
    earlier = first_candidate if first_utc < second_utc else second_candidate
    later = second_candidate if first_utc < second_utc else first_candidate

    if first_offset < second_offset:
        if nonexistent == RAISE:
            raise NonexistentDateTimeError(_("Datetime does not exist in this timezone."))
        if nonexistent == SHIFT_FORWARD:
            return second_candidate + transition_duration
        if nonexistent == SHIFT_BACKWARD:
            return first_candidate - transition_duration
        raise ValueError("Invalid nonexistent datetime policy.")

    if ambiguous == RAISE:
        raise AmbiguousDateTimeError(_("Datetime is ambiguous in this timezone."))
    if ambiguous == EARLIER:
        return earlier
    if ambiguous == LATER:
        return later
    raise ValueError("Invalid ambiguous datetime policy.")


def date_to_utc_datetime(value, tz, *, nonexistent, ambiguous, end=False):
    """Return a local date boundary as an inclusive or exclusive UTC datetime."""
    if end:
        value = value + timedelta(days=1)
    local_datetime = datetime.combine(value, time.min)
    return localize_datetime(
        local_datetime, tz, nonexistent=nonexistent, ambiguous=ambiguous
    ).astimezone(timezone.utc)
