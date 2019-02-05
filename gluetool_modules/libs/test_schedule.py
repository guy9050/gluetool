import gluetool
import gluetool.log
from gluetool.log import LoggingFunctionType, LoggingWarningFunctionType

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import TYPE_CHECKING, cast, Any, List, Optional  # noqa

if TYPE_CHECKING:
    import libci.guest  # noqa
    import gluetool_modules.libs.testing_environment  # noqa


class TestScheduleEntryAdapter(gluetool.log.ContextAdapter):
    def __init__(self, logger, entry_id):
        # type: (gluetool.log.ContextAdapter, str) -> None

        super(TestScheduleEntryAdapter, self).__init__(logger, {
            'ctx_schedule_entry_index': (200, entry_id)
        })


class TestScheduleEntry(object):
    # pylint: disable=too-few-public-methods

    """
    Internal representation of stuff to run, where to run and other bits necessary for scheduling
    all things the module was asked to perform.

    Follows :doc:`Test Schedule Entry Protocol </protocols/test-schedule-entry`.

    :param logger: logger used as a parent of this entry's own logger.
    :param str entry_id: ID of the entry.
    """

    # Logging type stubs
    #
    # These methods are added dynamically, therefore without intruducing them to mypy, every use of `self.debug`
    # would cause an error when checking types. We cannot simply set them to `None`, that makes pylint go crazy
    # because `None` is apparently not callable, and we're calling `self.debug` often :) So, we use dummy method
    # for initialization, to make pylint happy, but we wrap it with `cast` to enforce proper types to make mypy
    # happy as well :) It must a full-fledge method, because lambda cannot take keyword arguments (like sentry),
    # and pylint can discover that.
    def _fake_log_fn(self, *args, **kwargs):
        # type: (*Any, **Any) -> None

        pass

    verbose = cast(LoggingFunctionType, _fake_log_fn)
    debug = cast(LoggingFunctionType, _fake_log_fn)
    info = cast(LoggingFunctionType, _fake_log_fn)
    warn = cast(LoggingWarningFunctionType, _fake_log_fn)
    error = cast(LoggingFunctionType, _fake_log_fn)
    exception = cast(LoggingFunctionType, _fake_log_fn)

    def __init__(self, logger, entry_id):
        # type: (gluetool.log.ContextAdapter, str) -> None

        # pylint: disable=C0103
        self.id = entry_id

        self.logger = TestScheduleEntryAdapter(logger, self.id)
        self.logger.connect(self)

        self.testing_environment = None  # type: Optional[gluetool_modules.libs.testing_environment.TestingEnvironment]
        self.guest = None  # type: Optional[libci.guest.NetworkedGuest]
        self.package = None  # type: Any

    def log(self, log_fn=None):
        # type: (Optional[gluetool.log.LoggingFunctionType]) -> None

        log_fn = log_fn or self.debug

        log_fn('testing environment: {}'.format(self.testing_environment))
        log_fn('guest: {}'.format(self.guest))
