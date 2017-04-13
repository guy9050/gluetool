"""
Heart of the "citool" script. Referred to by setuptools' entry point.
"""

import os
import signal
import sys

import libci
from libci import CIError, CIRetryError, Failure


def split_argv(argv_all, modules):
    args = []
    argv_modules = []
    argv_ci = []
    module = None

    for argv in argv_all:
        if argv in modules:
            if module:
                argv_modules.append({module: args})
            else:
                argv_ci = args
            args = []
            module = argv
        else:
            args.append(argv)

    # add last one
    if module:
        argv_modules.append({module: args})
    else:
        argv_ci = args

    return (argv_ci, argv_modules)


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

    # init used vars
    ci = None
    module = None

    # If not None, exception happened and we want to let modules know
    # during their "destroy" time.
    failure = None

    # Python installs SIGINT handler that translates signal to
    # a KeyboardInterrupt exception. It's so good we want to use
    # it for SIGTERM as well, just wrap the handler with some logging.
    orig_sigint_handler = signal.getsignal(signal.SIGINT)

    def sigint_handler(signum, frame):
        ci.warn('Interrupted by SIGINT (Ctrl+C?)')
        return orig_sigint_handler(signum, frame)

    def sigterm_handler(signum, frame):
        ci.warn('Interrupted by SIGTERM')
        return orig_sigint_handler(signum, frame)

    # pylint: disable=too-many-nested-blocks,broad-except
    try:
        # initialize ci, load the module list
        ci = libci.CI(sentry=sentry)

        # CI is initialized, we can install our logging handlers
        signal.signal(signal.SIGINT, sigint_handler)
        signal.signal(signal.SIGTERM, sigterm_handler)

        # split the args and parse citool args
        (ci_args, modules_args) = split_argv(sys.argv[1:], ci.module_list())
        ci.parse_args(ci_args)

        ci.debug('parsed ci args: %s' % ci_args)
        ci.debug('parsed module args: %s' % modules_args)

        # version
        if ci.get_config('version'):
            sys.stdout.write('citool %s\n' % libci.__version__.strip())
            sys.exit(0)

        # list modules
        groups = ci.get_config('list')
        if groups is not None:
            sys.stdout.write('%s\n' % ci.module_list_usage(groups))
            sys.exit(0)

        # no modules
        if not modules_args:
            msg = 'No module specified, use -l to list available'
            raise CIError(msg)

        # command-line info
        if ci.get_config('info'):
            ci.print_cmdline(ci_args, modules_args)

        # actually the execution loop is retries+1
        # there is always one execution
        retries = ci.get_config('retries')
        for i in range(retries + 1):
            try:
                # destroy all modules if they exist
                # this will call their destructor, where modules should keep
                # their cleanup procedures
                ci.destroy_modules()

                # print retry info
                if i:
                    sys.stderr.write('\n')
                    ci.info('retrying execution (attempt %s out of %s)' %
                            (i, retries))

                # create a separate parser for each module, including ci itself
                for module_args in modules_args:
                    for module_name, args in module_args.iteritems():
                        module = ci.init_module(module_name)
                        module = ci.add_module_instance(module)
                        module.init_options_config()
                        module.parse_args(args)
                        module.sanity()
                        module.check_required_options()

                # execute all modules
                for module in ci.module_instances:
                    # make sure we have a clean state in case of retries
                    module.execute()
                    module.add_shared()
            except CIRetryError as e:
                sys.stderr.write('error in %s: %s\n' % (module.name, e))
                continue
            break

    except (SystemExit, KeyboardInterrupt) as e:
        failure = Failure(module=module, exc_info=sys.exc_info())
        raise e

    except Exception as e:
        failure = Failure(module=module, exc_info=sys.exc_info())

        if module:
            msg = "Exception raised in module '{}': {}".format(module.name, e.message)
        else:
            msg = "Exception raised: {}".format(e.message)

        libci.Logging.get_logger().exception(msg, exc_info=failure.exc_info)

        # we could use ci.sentry_submit_exception but ci may not exist yet,
        # use sentry client directly
        if not failure.soft and sentry is not None:
            sentry.captureException(exc_info=failure.exc_info)

        sys.exit(-1)

    finally:
        if ci:
            ci.destroy_modules(failure=failure)
