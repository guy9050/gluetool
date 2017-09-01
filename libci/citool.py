"""
Heart of the "citool" script. Referred to by setuptools' entry point.
"""

import functools
import os
import signal
import sys

import libci
from libci import CIError, CIRetryError, Failure
from libci.log import log_dict
from libci.utils import format_command_line


DEFAULT_CITOOL_CONFIG_PATHS = [
    '/etc/citool.d/citool',
    os.path.expanduser('~/.citool.d/citool')
]

DEFAULT_HANDLED_SIGNALS = (signal.SIGUSR1, signal.SIGUSR2)


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


def main():
    # pylint: disable=too-many-branches,too-many-statements

    sentry = None

    if 'SENTRY_DSN' in os.environ:
        import raven

        sentry = raven.Client(os.getenv('SENTRY_DSN'), install_logging_hook=True)

        # Enrich Sentry context with information that are important for us
        context = {}

        # env variables
        for name, value in os.environ.iteritems():
            context['env.{}'.format(name)] = value

        sentry.extra_context(context)

    # pylint: disable=invalid-name
    CI = None
    module = None

    # If not None, exception happened and we want to let modules know
    # during their "destroy" time.
    failure = None

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

    sigint_handler = functools.partial(_signal_handler,
                                       handler=orig_sigint_handler, msg='Interrupted by SIGINT (Ctrl+C?)')
    sigterm_handler = functools.partial(_signal_handler,
                                        handler=orig_sigint_handler, msg='Interrupted by SIGTERM')

    # pylint: disable=too-many-nested-blocks,broad-except
    try:
        # pylint: disable=invalid-name
        CI = libci.CI(sentry=sentry)

        # CI is initialized, we can install our logging handlers
        signal.signal(signal.SIGINT, sigint_handler)
        signal.signal(signal.SIGTERM, sigterm_handler)

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
                module.error('error: {}'.format(e))
                continue
            break

    except (SystemExit, KeyboardInterrupt) as e:
        failure = Failure(module=module, exc_info=sys.exc_info())
        raise e

    except Exception as e:
        exit_status = -1

        failure = Failure(module=module, exc_info=sys.exc_info())

        if module:
            msg = "Exception raised in module '{}': {}".format(module.unique_name, e.message)
        else:
            msg = "Exception raised: {}".format(e.message)

        libci.Logging.get_logger().exception(msg, exc_info=failure.exc_info)

        if failure.soft is True:
            # soft errors are up to users to fix, no reason to kill pipeline
            exit_status = 0

        elif sentry is not None:
            # we could use CI.sentry_submit_exception but ci may not exist yet,
            # use sentry client directly
            sentry.captureException(exc_info=failure.exc_info)

        sys.exit(exit_status)

    finally:
        if CI:
            CI.destroy_modules(failure=failure)
