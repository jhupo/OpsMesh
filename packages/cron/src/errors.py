"""Public scheduling failures."""


class ScheduleError(RuntimeError):
    """A schedule could not be validated or applied."""


class ScheduleNotFound(ScheduleError):
    """The requested schedule is outside the caller's namespace."""


class SchedulerUnavailable(ScheduleError):
    """The scheduler is stopped or has lost its exclusive lock."""
