import enum
import gluetool
import gluetool.log
from gluetool.log import LoggingFunctionType, LoggingWarningFunctionType, log_table
import libci.sentry

# Type annotations
# pylint: disable=unused-import,wrong-import-order,ungrouped-imports
from typing import TYPE_CHECKING, cast, Any, List, Optional  # noqa

if TYPE_CHECKING:
    import libci.guest  # noqa
    import gluetool_modules.libs.testing_environment  # noqa


class EmptyTestScheduleError(libci.sentry.PrimaryTaskFingerprintsMixin, gluetool.SoftGlueError):
    def __init__(self, task):
        # type: (Any) -> None

        super(EmptyTestScheduleError, self).__init__(task, 'No tests were found for the component')

    @property
    def submit_to_sentry(self):
        # type: () -> bool

        return False


class TestScheduleEntryStage(enum.Enum):
    """
    Enumerates different stages of a test schedule entry.

    During its lifetime, entry progress from one stage to another. Unlike :py:ref:`TestScheduleEntryState`,
    stage changes multiple times, and it may be even possible to return to previously visited stages.
    """

    #: Freshly created entry, nothing has happened yet to fulfil its goal.
    CREATED = 'created'

    #: A provisioning process started, to acquire a guest for the entry.
    GUEST_PROVISIONING = 'guest-provisioning'

    #: A guest has been provisioned.
    GUEST_PROVISIONED = 'guest-provisioned'

    #: A guest setup process started.
    GUEST_SETUP = 'guest-setup'

    #: The entry is prepared and tests can be executed.
    PREPARED = 'prepared'

    #: Test schedule runner began running tests of this entry.
    RUNNING = 'running'

    #: Tests finished, there is nothing left to perform.
    COMPLETE = 'complete'


class TestScheduleEntryState(enum.Enum):
    """
    Enumerates different possible (final) states of a test schedule entry.

    Unlike :py:ref:`TestScheduleEntryStage`, state changes once and only once, representing
    the final state of the entry.
    """

    #: Everything went well.
    OK = 'ok'

    #: An error appeared while processing the entry.
    ERROR = 'error'


class TestScheduleResult(enum.Enum):
    """
    Enumerates different possible results of both the tests performed by the entry and the schedule as whole.
    """

    #: We can tell nothing better about the result, as we don't have any relevant information (yet).
    UNDEFINED = 'undefined'

    #: Special value, should be used for the schedule as a whole. Signals at least one crashed schedule entry.
    ERROR = 'error'

    PASSED = 'passed'
    FAILED = 'failed'
    INFO = 'info'
    NOT_APPLICABLE = 'not_applicable'


class TestScheduleEntryAdapter(gluetool.log.ContextAdapter):
    def __init__(self, logger, entry_id):
        # type: (gluetool.log.ContextAdapter, str) -> None

        super(TestScheduleEntryAdapter, self).__init__(logger, {
            'ctx_schedule_entry_index': (200, entry_id)
        })


class TestScheduleEntry(object):
    # pylint: disable=too-few-public-methods

    """
    Internal representation of stuff to run, where to run it and other bits necessary for scheduling
    all things the module was asked to perform.

    :param logger: logger used as a parent of this entry's own logger.
    :param str entry_id: ID of the entry.
    :param str runner_capability: what runner capability is necessary to run the tests. Each runner
        supports some cabilities, and it is therefore able to take care of compatible entries only.
    :ivar str id: ID of the entry.
    :ivar str runner_capability: what runner capability is necessary to run the tests.
    :ivar TestScheduleEntryStage stage: current stage of the entry. It is responsibility of those
        who consume the entry to update its stage properly.
    :ivar TestScheduleEntryState state: current state of the entry. It is responsibility of those
        who consume the entry to update its state properly.
    :ivar TestScheduleResult result: result of the tests performed by the entry.
    :ivar TestingEnvironment testing_environment: environment required for the entry.
    :ivar NetworkedGuest guest: guest assigned to this entry.
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

    def __init__(self, logger, entry_id, runner_capability):
        # type: (gluetool.log.ContextAdapter, str, str) -> None

        # pylint: disable=C0103
        self.id = entry_id
        self.runner_capability = runner_capability

        self.logger = TestScheduleEntryAdapter(logger, self.id)
        self.logger.connect(self)

        self.stage = TestScheduleEntryStage.CREATED
        self.state = TestScheduleEntryState.OK
        self.result = TestScheduleResult.UNDEFINED

        self.testing_environment = None  # type: Optional[gluetool_modules.libs.testing_environment.TestingEnvironment]
        self.guest = None  # type: Optional[libci.guest.NetworkedGuest]

    def log(self, log_fn=None):
        # type: (Optional[gluetool.log.LoggingFunctionType]) -> None

        log_fn = log_fn or self.debug

        log_fn('testing environment: {}'.format(self.testing_environment))
        log_fn('guest: {}'.format(self.guest))


class TestSchedule(List[TestScheduleEntry]):
    """
    Represents a test schedule - a list of entries, each describing what tests to run and the necessary
    environment. Based on a list, supports basic sequence operations while adding convenience logging
    helper.
    """

    # pylint: disable=too-few-public-methods

    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None

        super(TestSchedule, self).__init__(*args, **kwargs)

        self.result = TestScheduleResult.UNDEFINED

    def log(self, log_fn, label=None):
        # type: (gluetool.log.LoggingFunctionType, Optional[str]) -> None
        """
        Log a table giving a nice, user-readable overview of the test schedule.

        At this moment, public properties of schedule entries are logged - guest, environment, etc.
        in the future more information would be added (passed the setup, running tests, finished tests,
        etc., but that will require a bit more info being accessible via schedule entry, which is work
        for the future patches.

        :param callable log_fn: function to use for logging.
        :param str label: if set, it is used as a label of the logged table.
        """

        label = label or 'test schedule'

        headers = [
            'SE', 'Stage', 'State', 'Result', 'Environment', 'Guest', 'Runner'
        ]

        rows = []

        # Helper - convert testing environment to a nice human-readable string.
        # `serialize_to_string is not that nice, id adds field names and no spaces between fields,
        # it is for machines mostly, and output of this function is supposed to be easily
        # readable by humans.
        def _env_to_str(testing_environment):
            # type: (Optional[gluetool_modules.libs.testing_environment.TestingEnvironment]) -> str

            if not testing_environment:
                return ''

            # Use serialized form for quick access to fields and their values, omit keys
            # and show values only - readable so far.
            serialized = testing_environment.serialize_to_json()

            return ', '.join([
                str(serialized[field]) for field in sorted(serialized.iterkeys())
            ])

        # pylint: disable=invalid-name
        for se in self:
            se_environment = _env_to_str(se.testing_environment)

            # if we have a guest, add provisioned environment and guest's name
            if se.guest:
                guest_info = '{}\n{}'.format(_env_to_str(se.guest.environment), se.guest.name)

            else:
                guest_info = ''

            rows.append([
                se.id, se.stage.name, se.state.name, se.result.name, se_environment, guest_info, se.runner_capability
            ])

        log_table(log_fn, label, [headers] + rows,
                  tablefmt='psql', headers='firstrow')
