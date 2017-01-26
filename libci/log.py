"""
Logging support.

Sets up logging environment for use by citool and modules.
"""

import atexit
import logging


# Add our custom "verbose" loglevel - it's even bellow DEBUG
logging.VERBOSE = 5
logging.addLevelName(logging.VERBOSE, 'VERBOSE')


def verbose(self, message, *args, **kwargs):
    if self.isEnabledFor(logging.VERBOSE):
        # pylint: disable-msg=protected-access
        self._log(logging.VERBOSE, message, args, **kwargs)


logging.Logger.verbose = verbose


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

    log_tracebacks = False

    def format(self, record):
        exc_text = ''

        if record.exc_info \
                and (self.log_tracebacks is True or Logging.stderr_handler.level in (logging.DEBUG, logging.VERBOSE)):
            exc_text = '\n' + self.formatException(record.exc_info)

        return '[{stamp}] [{level}] {msg} {exc_text}'.format(
            stamp=self.formatTime(record, datefmt='%H:%M:%S'),
            level=LoggingFormatter._level_tags[record.levelno],
            msg=record.getMessage(),
            exc_text=exc_text)


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
    def create_logger(output_file=None, level=None):
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

        if output_file is not None:
            # catch-everything file requested
            handler = logging.FileHandler(output_file)
            handler.setLevel(logging.VERBOSE)

            formatter = LoggingFormatter()
            formatter.log_tracebacks = True
            handler.setFormatter(formatter)

            logger.addHandler(handler)

            # Overwrites previously set output files (not our case but worth mentioning...)
            Logging.output_file = output_file
            Logging.output_file_handler = handler

            logger.debug("created output file '{}'".format(output_file))

            atexit.register(Logging._close_output_file)

        logger.debug("logger set up: output_file='{}', level={}".format(output_file, level))

        return logger
