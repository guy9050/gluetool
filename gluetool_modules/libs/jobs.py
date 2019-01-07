"""
Simplyfies access to concurrently running jobs. Based on ``concurrent.futures``, letting user
to use callbacks to step into the whole process, it should take care of the heavy lifting.
"""

import concurrent.futures

import gluetool
import gluetool.log

from six import reraise

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import cast, Any, Callable, Dict, List, NamedTuple, Optional, Tuple  # noqa


#: A job to run.
#:
#: :param gluetool.log.ContextAdapter logger: logger to use when logging events related to the job.
#: :param callable target: function to call to perform the job.
#: :param tuple args: positional arguments of ``target``.
#: :param dict kwargs: keyword arguments of ``target``.
Job = NamedTuple('Job', (
    ('logger', gluetool.log.ContextAdapter),
    ('target', Callable[..., Any]),
    ('args', Tuple[Any, ...]),
    ('kwargs', Dict[str, Any])
))


# pylint: disable=invalid-name
JobErrorType = Tuple[Job, gluetool.log.ExceptionInfoType]


def handle_job_errors(errors, exception_message, logger=None):
    # type: (List[JobErrorType], str, Optional[gluetool.log.ContextAdapter]) -> None
    """
    Take care of reporting exceptions gathered from futures, and re-raise one of them - or a new,
    generic one - to report a process, performed by jobs, failed.

    :param list(tuple(Job, exception info)) errors: jobs and the errors they raised.
    :param str exception_message: a message used when raising generic exception.
    :param ContextAdapter logger: top-level logger to use when logging things related to all errors.
    """

    logger = logger or gluetool.log.Logging.get_logger()

    logger.debug('at least one job failed')

    # filter exceptions using given ``check`` callback, and raise the first suitable one - or return back
    def _raise_first(check):
        # type: (Callable[[gluetool.log.ExceptionInfoType], bool]) -> None

        for _, exc_info in errors:
            if not check(exc_info):
                continue

            reraise(*exc_info)

    # Soft errors have precedence - the let user know something bad happened, which is better
    # than just "infrastructure error".
    _raise_first(lambda exc: isinstance(exc[1], gluetool.SoftGlueError))

    # Then common CI errors
    _raise_first(lambda exc: isinstance(exc[1], gluetool.GlueError))

    # Ok, no custom exception, maybe just some Python ones - kill the pipeline.
    raise gluetool.GlueError(exception_message)


# pylint: disable=too-many-arguments
def run_jobs(jobs,  # type: List[Job]
             logger=None,  # type: Optional[gluetool.log.ContextAdapter]
             max_workers=None,  # type: Optional[int]
             worker_name_prefix='worker',  # type: str
             on_job_start=None,  # type: Optional[Callable[..., None]]
             on_job_complete=None,  # type: Optional[Callable[..., None]]
             on_job_error=None,  # type: Optional[Callable[..., None]]
             on_job_done=None  # type: Optional[Callable[..., None]]
            ):  # noqa
    # type: (...) -> List[JobErrorType]
    """
    Run jobs in parallel.

    :param list(Job) jobs: list of jobs to run.
    :param ContextAdapter logger: logger to use global events.
    :param int max_workers: maximal number of workers running at the same time. If not set, length of the
        job list is used.
    :param str worker_name_prefix: if set, it is used as a prefix of workers' names.
    :param callable on_job_start: function to call when job is started. Called with job's arguments.
    :param callable on_job_complete: function to call when job successfully finishes. Called with the
        job's return value, followed by job's arguments.
    :param callable on_job_error: function to call when job finishes with an error. Called with the
        exception info tuple, followed by job's arguments.
    :param callable on_job_done: function to call when job finishes - called always, preceded by a call
        to ``on_job_complete`` or ``on_job_error``. Called with the number of remaining jobs, followed
        by job's arguments.
    :rtype: list(tuple(exc_info, job definition))
    :returns: errors produced by jobs. Represented as a list of tuples of two items: the exception info
        and job definition as given in ``jobs`` list.
    """

    logger = logger or gluetool.log.Logging.get_logger()
    max_workers = max_workers or len(jobs)

    gluetool.log.log_dict(logger.debug,  # type: ignore  # logger.debug signature is compatible
                          'running {} jobs with {} workers'.format(len(jobs), max_workers),
                          jobs)

    futures = {}
    errors = []  # type: List[JobErrorType]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers,
                                               thread_name_prefix=worker_name_prefix) as executor:

        for job in jobs:
            if on_job_start:
                on_job_start(*job.args, **job.kwargs)

            future = executor.submit(job.target, *job.args, **job.kwargs)
            futures[future] = job

            job.logger.debug('started job in {}'.format(future))

        # If we leave context here, the rest of our code would run after all futures finished - context would
        # block in its __exit__ on executor's state. That'd be generaly fine but we'd like to inform user about
        # our progress, and that we can do be checking futures as they complete, one by one, not waiting for the
        # last one before we start checking them. This thread *will* sleep from time to time, when there's no
        # complete future available, but that's fine. We'll get our hands on each complete one as soon as
        # possible, letting user know about the progress.

        for i, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            remaining_count = len(jobs) - i

            job = futures[future]

            job.logger.debug('finished job in {}'.format(future))

            if future.exception() is None:
                if on_job_complete:
                    on_job_complete(future.result(), *job.args, **job.kwargs)

            else:
                exc_info = future.exception_info()

                # Exception info returned by future does not contain exception class while the info returned
                # by sys.exc_info() does and all users of it expect the first item to be exception class.
                full_exc_info = (exc_info[0].__class__, exc_info[0], exc_info[1])

                errors.append((job, full_exc_info))

                if on_job_error:
                    on_job_error(full_exc_info, *job.args, **job.kwargs)

            if on_job_done:
                on_job_done(remaining_count, *job.args, **job.kwargs)

    gluetool.log.log_dict(logger.debug,  # type: ignore  # logger.debug signature is compatible
                          'jobs produced errors',
                          errors)

    return errors
