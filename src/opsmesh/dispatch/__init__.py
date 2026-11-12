"""Public event dispatch and processor registration interfaces."""

from opsmesh.exceptions import IgnoredException as IgnoredException

from .concurrency import SessionBusyError as SessionBusyError
from .concurrency import SessionGuard as SessionGuard
from .dispatcher import dispatch_event as dispatch_event
from .dispatcher import handle_event as handle_event
from .execution import check_and_run_matcher as check_and_run_matcher
from .hooks import event_postprocessor as event_postprocessor
from .hooks import event_preprocessor as event_preprocessor
from .hooks import run_postprocessor as run_postprocessor
from .hooks import run_preprocessor as run_preprocessor
from .results import DispatchResult as DispatchResult
