"""
Heart of the "citool" script. Referred to by setuptools' entry point.
"""

import functools
import os
import signal
import sys
import traceback

import libci
import libci.sentry

from libci import CIError, CIRetryError, Failure
from libci.log import log_dict
from libci.utils import format_command_line


DEFAULT_CITOOL_CONFIG_PATHS = [
    '/etc/citool.d/citool',
    os.path.expanduser('~/.citool.d/citool')
]

DEFAULT_HANDLED_SIGNALS = (signal.SIGUSR2,)


class TimeoutExpiredError(libci.CIError):
    def __init__(self):
        super(TimeoutExpiredError, self).__init__('Pipeline timeout expired')


def split_module_argv(argv, modules):
    """
    Split command-line arguments, left by ``citool``, into groups starting with module names.

    :param list argv: Remainder of :py:data:`sys.argv` after removing ``citool``'s own options.
    :param list(str) modules: List of module names.
    :returns: List of module names and their arguments, in a for of tuples ``(module_name, [arg1, arg2, ...])``.
    """

    groups = []
    module_args = None

    while argv:
        arg = argv.pop(0)

        if arg in modules:
            module_args = []
            groups.append((arg, module_args))
            continue

        if module_args is None:
            raise CIError("Cannot parse module argument: '{}'".format(arg))

        module_args.append(arg)

    return groups


def log_cmdline(ci, argv, pipeline_description):
    cmdline = [
        [sys.argv[0]] + argv
    ]

    for module_name, module_argv in pipeline_description:
        cmdline.append([module_name] + module_argv)

    ci.info('command-line:\n{}'.format(format_command_line(cmdline)))


def _get_exit_logger():
    """
    Return logger for use when finishing the ``citool`` pipeline.
    """

    # We want to use the current logger, if there's any set up.
    logger = libci.log.Logging.get_logger()

    if logger:
        return logger

    # This may happen only when something went wrong during logger initialization
    # when CI instance was created. Falling back to a very basic Logger seems
    # to be the best option here.

    import logging

    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger()

    logger.warn('Cannot use custom logger, falling back to a default one')

    return logger


def _quit(exit_status):
    """
    Log exit status and quit.
    """

    logger = _get_exit_logger()

    (logger.debug if exit_status == 0 else logger.error)('Exiting with status {}'.format(exit_status))

    sys.exit(exit_status)


# pylint: disable=invalid-name
def _cleanup(CI, failure=None):
    """
    Clear CI pipeline by calling modules' ``destroy`` methods.
    """

    if not CI:
        return 0

    destroy_failure = CI.destroy_modules(failure=failure)

    # if anything happend while destroying modules, crash the pipeline
    if not destroy_failure:
        return 0

    logger = _get_exit_logger()
    logger.warn('Exception raised when destroying modules, overriding exit status')

    return -1


# pylint: disable=invalid-name
def _handle_failure_core(failure, CI, sentry):
    logger = _get_exit_logger()

    # Handle simple 'sys.exit(0)' - no exception happened
    if failure.exc_info[0] == SystemExit and failure.exc_info[1].code == 0:
        _quit(0)

    # soft errors are up to users to fix, no reason to kill pipeline
    exit_status = 0 if failure.soft is True else -1

    if failure.module:
        msg = "Exception raised in module '{}': {}".format(failure.module.unique_name, failure.exc_info[1].message)

    else:
        msg = "Exception raised: {}".format(failure.exc_info[1].message)

    logger.exception(msg, exc_info=failure.exc_info)

    sentry.submit_exception(failure, logger=logger)

    exit_status = min(exit_status, _cleanup(CI, failure=failure))

    _quit(exit_status)


# pylint: disable=invalid-name
def _handle_failure(failure, CI, sentry):
    try:
        _handle_failure_core(failure, CI, sentry)

    # pylint: disable=broad-except
    except Exception:
        exc_info = sys.exc_info()

        # Don't trust anyone, the exception might have occured inside logging code, therefore
        # resorting to plain print.

        print >> sys.stderr
        print >> sys.stderr, '!!! While handling an exception, another one appeared !!!'
        print >> sys.stderr
        print >> sys.stderr, 'Will try to submit it to Sentry but giving up on everything else.'

        try:
            # pylint: disable=protected-access
            print >> sys.stderr, libci.log.LoggingFormatter._format_exception_chain(sys.exc_info())

            # Anyway, try to submit this exception to Sentry, but be prepared for failure in case the original
            # exception was raised right in Sentry-related code.
            sentry.submit_exception(Failure(None, exc_info))

        # pylint: disable=broad-except
        except Exception:
            # tripple error \o/

            print >> sys.stderr
            print >> sys.stderr, '!!! While submitting an exception to the Sentry, another exception appeared !!!'
            print >> sys.stderr, '    Giving up on everything...'
            print >> sys.stderr

            traceback.print_exc()

        # Don't use _quit() here - it might try to use complicated logger, and we don't trust
        # anythign at this point. Just die already.
        sys.exit(-1)


def main():
    # pylint: disable=too-many-branches,too-many-statements

    sentry = libci.sentry.Sentry()

    # pylint: disable=invalid-name
    CI = None

    # Python installs SIGINT handler that translates signal to
    # a KeyboardInterrupt exception. It's so good we want to use
    # it for SIGTERM as well, just wrap the handler with some logging.
    orig_sigint_handler = signal.getsignal(signal.SIGINT)
    sigmap = {getattr(signal, name): name for name in [name for name in dir(signal) if name.startswith('SIG')]}

    def _signal_handler(signum, frame, handler=None, msg=None):
        msg = msg or 'Signal {} received'.format(sigmap[signum])

        CI.warn(msg)

        if handler is not None:
            return handler(signum, frame)

    def _sigusr1_handler(signum, frame):
        # pylint: disable=unused-argument

        raise TimeoutExpiredError()

    sigint_handler = functools.partial(_signal_handler,
                                       handler=orig_sigint_handler, msg='Interrupted by SIGINT (Ctrl+C?)')
    sigterm_handler = functools.partial(_signal_handler,
                                        handler=orig_sigint_handler, msg='Interrupted by SIGTERM')
    sigusr1_handler = functools.partial(_signal_handler, handler=_sigusr1_handler)

    # pylint: disable=too-many-nested-blocks,broad-except
    try:
        # pylint: disable=invalid-name
        CI = libci.CI(sentry=sentry)

        # CI is initialized, we can install our logging handlers
        signal.signal(signal.SIGINT, sigint_handler)
        signal.signal(signal.SIGTERM, sigterm_handler)
        signal.signal(signal.SIGUSR1, sigusr1_handler)

        for signum in DEFAULT_HANDLED_SIGNALS:
            signal.signal(signum, _signal_handler)

        # process configuration
        argv = sys.argv[1:]

        CI.parse_config(DEFAULT_CITOOL_CONFIG_PATHS)
        CI.parse_args(argv)

        if CI.option('pid'):
            CI.info('PID: {} PGID: {}'.format(os.getpid(), os.getpgrp()))

        # version
        if CI.option('version'):
            sys.stdout.write('citool %s\n' % libci.__version__.strip())
            sys.exit(0)

        CI.load_modules()

        pipeline_description = split_module_argv(CI.option('pipeline'), CI.module_list())
        log_dict(CI.debug, 'pipeline description', pipeline_description)

        # list modules
        groups = CI.option('list-modules')
        if groups == [True]:
            sys.stdout.write('%s\n' % CI.module_list_usage([]))
            sys.exit(0)

        elif groups:
            sys.stdout.write('%s\n' % CI.module_list_usage(groups))
            sys.exit(0)

        if CI.option('list-shared'):
            import tabulate

            functions = []

            for mod_name in CI.module_list():
                functions += [[func_name, mod_name] for func_name in CI.modules[mod_name]['class'].shared_functions]

            functions = sorted(functions, key=lambda row: row[0])

            sys.stdout.write("""Available shared functions

{}
""".format(tabulate.tabulate(functions, ['Shared function', 'Module name'], tablefmt='simple')))
            sys.exit(0)

        # no modules
        if not pipeline_description:
            raise CIError('No module specified, use -l to list available')

        # command-line info
        if CI.option('info'):
            log_cmdline(CI, argv, pipeline_description)

        # actually the execution loop is retries+1
        # there is always one execution
        retries = CI.option('retries')
        for loop_number in range(retries + 1):
            try:
                # Reset pipeline - destroy all modules that exist so far
                CI.destroy_modules()

                # Print retry info
                if loop_number:
                    CI.warn('retrying execution (attempt #{} out of {})'.format(loop_number, retries))

                # Run the pipeline
                CI.run_modules(pipeline_description, register_with_ci=True)

            except CIRetryError as e:
                CI.error(e)
                continue

            break

    except (SystemExit, KeyboardInterrupt, Exception):
        _handle_failure(Failure(CI.current_module if CI else None, sys.exc_info()), CI, sentry)

    else:
        exit_status = _cleanup(CI)

        _quit(exit_status)
