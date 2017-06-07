"""
Logging support.

Sets up logging environment for use by citool and modules.
"""

import atexit
import logging

try:
    import colorama

except ImportError:
    colorama = None

# Add our custom "verbose" loglevel - it's even bellow DEBUG
logging.VERBOSE = 5
logging.addLevelName(logging.VERBOSE, 'VERBOSE')


def verbose_logger(self, message, *args, **kwargs):
    if self.isEnabledFor(logging.VERBOSE):
        # pylint: disable-msg=protected-access
        self._log(logging.VERBOSE, message, args, **kwargs)


def verbose_adapter(self, message, *args, **kwargs):
    message, kwargs = self.process(message, kwargs)
    self.logger.verbose(message, *args, **kwargs)


logging.Logger.verbose = verbose_logger
logging.LoggerAdapter.verbose = verbose_adapter


class ContextAdapter(logging.LoggerAdapter):
    """
    Generic logger adapter that collects "contexts", and prepends them
    to the message.

    "context" is any key in `extra` dictionary starting with `ctx_`,
    the its is expected to be tuple(priority, value). Contexts are
    then sorted by their priorities before inserting them into the
    message (lower priority means context will be placed closer to
    the beggining of the line - highest priority comes last.
    """

    def __init__(self, logger, extra=None):
        super(ContextAdapter, self).__init__(logger, extra or {})

        self.warn = self.warning

    def process(self, msg, kwargs):
        """
        Original `process` overwrites `kwargs['extra']` which doesn't work
        for us - we want to chain adapters, getting more and more contexts
        on the way. Therefore `update` instead of assignment.
        """

        if 'extra' not in kwargs:
            kwargs['extra'] = {}

        kwargs['extra'].update(self.extra)
        return msg, kwargs

    def connect(self, parent):
        """
        Create helper loggign methods in parrent, by assigning adapter's
        methods to it. Simply instantiate adapter and call its `connect`
        with your instance as `parent` argument, and your instance will
        get all these logging helpers.
        """

        parent.debug = self.debug
        parent.verbose = self.verbose
        parent.info = self.info
        parent.warn = self.warning
        parent.error = self.error
        parent.exception = self.exception


class ModuleAdapter(ContextAdapter):
    """
    Custom logger adapter, adding module name as a context.
    """

    def __init__(self, logger, module):
        super(ModuleAdapter, self).__init__(logger, {'ctx_module_name': (10, module.name)})


class LoggingFormatter(logging.Formatter):
    """
    Custom log record formatter. Produces output in form of:

      [timestamp] [logelevel] message
    """

    _level_tags = {
        logging.VERBOSE: 'V',
        logging.DEBUG: 'D',
        logging.INFO: '+',
        logging.WARNING: 'W',
        logging.ERROR: 'E',
        logging.CRITICAL: 'C'
    }

    if colorama is not None:
        _level_color = {
            logging.INFO: colorama.Fore.GREEN,
            logging.WARNING: colorama.Fore.YELLOW,
            logging.ERROR: colorama.Fore.RED,
            logging.CRITICAL: colorama.Fore.RED
        }

    def __init__(self, log_tracebacks=False, colors=False):
        super(LoggingFormatter, self).__init__()

        self.log_tracebacks = log_tracebacks
        self.colors = colors

    def format(self, record):
        fmt = ['[{stamp}]', '[{level}]', '{msg}']
        values = {
            'stamp': self.formatTime(record, datefmt='%H:%M:%S'),
            'level': LoggingFormatter._level_tags[record.levelno],
            'msg': record.getMessage()
        }

        if record.exc_info \
                and (self.log_tracebacks is True or Logging.stderr_handler.level in (logging.DEBUG, logging.VERBOSE)):
            fmt.append('{exc_text}')
            values['exc_text'] = '\n' + self.formatException(record.exc_info)

        # List all context properties of record
        ctx_properties = [prop for prop in dir(record) if prop.startswith('ctx_')]

        if ctx_properties:
            # Sorting them in reverse order of priorities - we're goign to insert
            # their values into `fmt`, so the highest priority context must be
            # inserted as the last one.
            sorted_ctxs = sorted(ctx_properties, key=lambda x: x[0], reverse=True)

            for name in sorted_ctxs:
                _, value = getattr(record, name)

                fmt.insert(2, '[{%s}]' % name)
                values[name] = value

        msg = ' '.join(fmt).format(**values)

        if self.colors is True:
            color = self._level_color.get(record.levelno, None)
            if color:
                msg = color + msg + colorama.Fore.RESET

        return msg


class Logging(object):
    """
    Top-level wrapper of a logger instance.
    """

    #: Logger singleton - if anyone asks for a logger, they will get this one.
    logger = None

    #: Stream handler printing out to stderr.
    stderr_handler = None

    #: If enabled, handles output to catch-everything file.
    output_file = None
    output_file_handler = None

    @staticmethod
    def _close_output_file():
        """
        If opened, close output file used for logging.

        This method is registered with atexit.
        """

        if Logging.output_file_handler is None:
            return

        Logging.get_logger().debug("closing output file '{}'".format(Logging.output_file))

        Logging.output_file_handler.flush()
        Logging.output_file_handler.close()
        Logging.output_file_handler = None

    @staticmethod
    def get_logger():
        """
        Return a logger instance.

        Expects there was a call to create_logger method somewhere in the
        history, creating such an instance.
        """

        assert Logging.logger is not None

        return Logging.logger

    @staticmethod
    def create_logger(output_file=None, level=None, colors=False, sentry=None):
        """
        Create and setup logger.

        This method is called at least twice:
          - when libci.CI is instantiated, only a stderr handler is set up,
          - when all arguments and options are processed, and CI instance get
            determine desired log level, and whether it's expected to use an
            output file. This time, method only modifies log level, and adds
            FileHandler if necessary.
        """

        level = level or logging.INFO

        if Logging.logger is None:
            logger = Logging.logger = logging.getLogger('citool')
            logger.propagate = False

            # logger actually emits everything, handlers do filtering
            logger.setLevel(logging.VERBOSE)

            # stderr handler
            Logging.stderr_handler = handler = logging.StreamHandler()
            handler.setLevel(level)
            handler.setFormatter(LoggingFormatter())
            logger.addHandler(handler)

        else:
            logger = Logging.logger

            # set log level to new value
            Logging.stderr_handler.setLevel(level)

            # set color settings according to requested value
            if colors is True:
                if colorama is None:
                    logger.warn("Unable to turn on colorized terminal messages, please install 'colorama' package")

                else:
                    Logging.stderr_handler.setFormatter(LoggingFormatter(colors=True))

        if output_file is not None:
            # catch-everything file requested
            handler = logging.FileHandler(output_file, 'w')
            handler.setLevel(logging.VERBOSE)

            formatter = LoggingFormatter(log_tracebacks=True)
            formatter.log_tracebacks = True
            handler.setFormatter(formatter)

            logger.addHandler(handler)

            # Overwrites previously set output files (not our case but worth mentioning...)
            Logging.output_file = output_file
            Logging.output_file_handler = handler

            logger.debug("created output file '{}'".format(output_file))

            atexit.register(Logging._close_output_file)

        if sentry is not None:
            import raven.breadcrumbs

            raven.breadcrumbs.register_special_log_handler(logger, lambda *args: False)

        logger.debug("logger set up: output_file='{}', level={}".format(output_file, level))

        return logger
