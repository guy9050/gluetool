import copy
import os
import sys

from collections import defaultdict
import enum
import bs4

import gluetool
from gluetool import GlueError
from gluetool.log import log_blob, log_dict
from gluetool.utils import new_xml_element, IncompatibleOptionsError, normalize_bool_option, render_template
from libci.results import TestResult, publish_result


# The exit status values come from restraint sources: https://github.com/p3ck/restraint/blob/master/src/errors.h
# I failed to find any documentation on this...
class RestraintExitCodes(enum.IntEnum):
    # pylint: disable=invalid-name
    RESTRAINT_TASK_RUNNER_RESULT_ERROR = 10
    RESTRAINT_SSH_ERROR = 14


class RestraintTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, glue, overall_result, **kwargs):
        super(RestraintTestResult, self).__init__(glue, 'restraint', overall_result, **kwargs)

    def _serialize_to_xunit(self):
        test_suite = super(RestraintTestResult, self)._serialize_to_xunit()

        if self.glue.has_shared('beah_xunit_serialize'):
            self.glue.shared('beah_xunit_serialize', test_suite, self)

        else:
            self.glue.warn("To serialize result to xUnit format, 'beah_xunit_serialize' shared function is required",
                           sentry=True)

        return test_suite

    @classmethod
    def _unserialize_from_json(cls, glue, input_data):
        return RestraintTestResult(glue, input_data['overall_result'], ids=input_data['ids'], urls=input_data['urls'],
                                   payload=input_data['payload'])


class RestraintRunner(gluetool.Module):
    """
    This module runs recipe sets, prepared by other modules, using restraint harness.
    It can make use of snapshots (if supported by guests) to isolate each test, and
    it's able to parallelize tests.

    The results are provided in the form similar to what beaker module does - short summary
    in console log, artifact file, and shared function to publish results for later
    modules as well.
    """

    name = 'restraint-runner'
    description = 'This module runs recipe sets, prepared by other modules, using restraint harness.'

    options = {
        'use-snapshots': {
            'help': 'Enable or disable use of snapshots (if supported by guests) (default: %(default)s)',
            'default': 'no',
            'metavar': 'yes|no'
        },
        'parallelize-recipe-sets': {
            'help': 'Enable or disable parallelization of recipe sets (default: %(default)s)',
            'default': 'no',
            'metavar': 'yes|no'
        },
        'parallelize-task-sets': {
            'help': 'Enable or disable parallelization of test sets (default: %(default)s)',
            'default': 'no',
            'metavar': 'yes|no'
        },
        'on-error-snapshot': {
            # pylint: disable=line-too-long
            'help': 'If set, on crash of restraint take a snapshot of the guest and download it (default: %(default)s).',
            'default': 'no',
            'metavar': 'yes|no'
        },
        'on-error-continue': {
            'help': """
                    If set, on crash of restraint continue with another test case (default: %(default)s).

                    Be aware that you probably wish to use ``--use-snapshots`` as well, as crashed restraint might
                    have left the SUT in a very bad state.
                    """,
            'default': 'no',
            'metavar': 'yes|no'
        },
        'results-directory-template': {
            'help': """
                    If set, directories created by ``restraint`` are renamed using this template. Common eval
                    context is available, with addition of ``GUEST`` (default: %(default)s).

                    Not compatible with ``--parallelize-task-sets`` and ``--use-snapshots``.
                    """,
            'default': None
        }
    }

    _result_class = None

    @gluetool.utils.cached_property
    def use_snapshots(self):
        return normalize_bool_option(self.option('use-snapshots'))

    @gluetool.utils.cached_property
    def parallelize_recipe_sets(self):
        return normalize_bool_option(self.option('parallelize-recipe-sets'))

    @gluetool.utils.cached_property
    def parallelize_task_sets(self):
        return normalize_bool_option(self.option('parallelize-task-sets'))

    @gluetool.utils.cached_property
    def on_error_snapshot(self):
        return normalize_bool_option(self.option('on-error-snapshot'))

    @gluetool.utils.cached_property
    def on_error_continue(self):
        return normalize_bool_option(self.option('on-error-continue'))

    def _merge_task_results(self, tasks_results):
        # pylint: disable=no-self-use
        """
        ``task_results`` is quite common structure - ``list`` of dictionaries, with ``task`` name
        being the key, with ``task runs`` - list of dictionaries, one for each task run - as values.
        We need to merge all task runs for a single task into a single list.

        .. code-block:: python

           [
             {
               'foo task': [
                 {
                   'result': 'PASS'
                 },
                 {
                   'result': 'PASS'
                 }
               ],
               'bar task': [
                 {
                   'result': 'FAIL'
                 }
               ]
             },
             {
               'bar task': [
                 {
                   'result': 'PASS'
                 }
               ]
           ]

        becomes

        .. code-block:: python

            {
              'foo task': [
               {
                 'result': 'PASS'
               },
               {
                 'result': 'PASS'
               }
             ],
             'bar task': [
               {
                 'result': 'FAIL'
               },
               {
                 'result': 'PASS'
               }
             ]
           }
        """

        merged = defaultdict(list)

        for result_set in tasks_results:
            for name, runs in result_set.iteritems():
                merged[name] += runs

        # and return a dictionary, not a default dict
        return dict(merged)

    def _gather_task_set_results(self, guest, output):
        """
        restraint produces `job.xml` which carries pile of logs, results and so on.
        We open `job.xml`, and find all necessary data to construct dictionaries,
        one for each task, carrying "results" of the task, in a structure very
        similar to the one produced by `beaker` module.

        :returns: { <task name>: <task runs> }
        """

        # pylint: disable=no-self-use

        # XML produced by restraint
        with open(os.path.join(output.directory, 'job.xml'), 'r') as f:
            self.debug('XML produced by restraint lies in {}'.format(f.name))

            job_results = bs4.BeautifulSoup(f.read(), 'xml')

        # results accumulates results (<task name>: <task runs>) we want to return upwards
        results = defaultdict(list)

        if 'BUILD_URL' in os.environ:
            def artifact_path(s):
                return '{}/artifact/{}/{}'.format(os.getenv('BUILD_URL'), output.directory, s)

        else:
            def artifact_path(s):
                path = gluetool.utils.normalize_path('{}/{}'.format(output.directory, s))

                return 'file://localhost/{}'.format(path)

        for task_results in job_results.recipeSet.recipe.find_all('task'):
            # find journal if there's one available for the task
            journal = None

            journal_log = task_results.logs.find_all('log', filename='journal.xml')
            if journal_log:
                with open(os.path.join(output.directory, journal_log[0]['path']), 'r') as f:
                    self.debug('Journal lies in {}'.format(f.name))

                    journal = bs4.BeautifulSoup(f.read(), 'xml').BEAKER_TEST

            # If guest provides hostname, export it to result as well - user may need to connect
            # to it, and its hostname (as set during its setup) may not be resolvable - but
            # we're guaranteed to connect to the guest using guest.hostname value.
            if hasattr(guest, 'hostname'):
                connectable_hostname = guest.hostname

            results[task_results['name']].append(
                self.shared('parse_beah_result', task_results, journal=journal, recipe=job_results.job.recipeSet,
                            artifact_path=artifact_path, connectable_hostname=connectable_hostname)
            )

        return dict(results)

    def _run_task_set(self, guest, task_set, recipe_attrs, recipe_set_attrs):
        """
        Run a set of tasks on the guest.

        :param Guest guest: guest to use for running tests.
        :param task_set: list of <task/> elements, representing separate tasks.
        :param dict recipe_attrs: additional attributes to set on <recipe/> element.
        :param dict recipe_set_attrs: additional attributes to set on <recipe_set/> element.
        """

        # Log our task set
        guest.debug('Task set:\n{}'.format('\n'.join([task.prettify(encoding='utf-8') for task in task_set])))

        # Wrap task set in <job><recipeSet><recipe>... envelope
        job = new_xml_element('job')
        new_xml_element('recipeSet', _parent=job, **recipe_set_attrs)
        new_xml_element('recipe', _parent=job.recipeSet, **recipe_attrs)

        for task in task_set:
            job.recipeSet.recipe.append(copy.copy(task))

        # We'll need this for restraint
        job_desc = job.prettify(encoding='utf-8')

        self.debug('Job:\n{}'.format(job_desc))

        def download_snapshot():
            # If snapshot downloads are not enabled, just do nothing and return.
            if not self.on_error_snapshot:
                return

            try:
                filename = guest.create_snapshot(start_again=False).download()

                self.warn("Snapshot saved as '{}'".format(filename))

            # pylint: disable=broad-except
            except Exception as exc:
                self.exception('Exception raised when downloading a snapshot: {}'.format(exc),
                               exc_info=sys.exc_info())

        # Run restraint with our job. So far, any exception is a serious concern as it signals something
        # bad happened - 'restraint' shared function returns restraint's output even if its exit status
        # was non-zero. Take a snapshot, if asked to do so, and re-raise the exception.
        try:
            # We should add rename_dir_to and label but it's not clear what should be the name. Probably something
            # with index of the job. Future patch :)

            # For now, use simple template, which is disable when parallelization is enabled.
            rename_dir_to = None
            if self.option('results-directory-template'):
                context = gluetool.utils.dict_update(
                    self.shared('eval_context'),
                    {
                        'GUEST': guest
                    }
                )

                rename_dir_to = render_template(self.option('results-directory-template'),
                                                logger=self.logger,
                                                **context)

            output = self.shared('restraint', guest, job,
                                 rename_dir_to=rename_dir_to)

        except gluetool.GlueError as exc:
            download_snapshot()

            raise exc

        log_blob(self.info, 'Task set output', output.execution_output.stdout)

        # Find out what is the result - restraint returned back to us, and even with a non-zero
        # exit status, there should be a result to pick up.
        result = self._gather_task_set_results(guest, output)
        log_dict(self.debug, 'task set result', result)

        exit_code = output.execution_output.exit_code

        # A zero exit status? Fine!
        if exit_code == 0:
            return result

        self.debug('restraint exited with invalid exit code {}'.format(exit_code))

        if exit_code == RestraintExitCodes.RESTRAINT_TASK_RUNNER_RESULT_ERROR:
            # "One or more tasks failed" error - this is a good, well behaving error.
            # We can safely move on and return results we got from restraint.
            self.info('restraint reports: One or more tasks failed')

            return result

        # Now we're dealing with an error we don't know how to handle better, so...

        # Dowonload a snapshot.
        download_snapshot()

        # Return a result and let the caller to decide what to do next.
        if self.on_error_continue:
            return result

        # Restraint failed, and no better option was enabled => raise an exception.
        raise gluetool.GlueError('restraint command exited with return code {}: {}'.format(
            exit_code, output.execution_output.stderr))

    def _run_recipe_set_isolated(self, guest, recipe_set):
        """
        Run tasks from a recipe set one by one, getting fresh snapshot for each task.

        :param element recipe_set: <recipeSet/> element, gathering some tasks.
        :returns: { <task name>: <task runs> }
        """

        guest.info('Running recipe set tasks in isolation')

        # _run_task_set will need these, to make tasks feel like home
        recipe_set_attrs = recipe_set.attrs
        recipe_attrs = recipe_set.find_all('recipe')[0].attrs

        tasks = recipe_set.find_all('task')

        # if it's just a single task, it's quite simple
        if len(tasks) == 1:
            self.debug('only a single task in the task set, use guest directly')

            return self._run_task_set(guest, tasks, recipe_attrs, recipe_set_attrs)

        # save current state of guest
        base_snapshot = guest.create_snapshot()

        if self.parallelize_task_sets:
            # run all task in parallel, each on its own guest, using the snapshot as their image
            self.info('Running {} tasks in parallel'.format(len(tasks)))
            self.debug('parallelize {} tasks requires {} additional guests'.format(len(tasks), len(tasks) - 1))

            guests = [guest] + self.shared('openstack_provision', len(tasks) - 1, image=base_snapshot)
            threads = []

            for i, (actual_guest, task) in enumerate(zip(guests, tasks)):
                thread = gluetool.utils.WorkerThread(actual_guest.logger, self._run_task_set,
                                                     fn_args=(actual_guest, [task], recipe_attrs, recipe_set_attrs),
                                                     name='task-runner-{}'.format(i))
                threads.append(thread)

                thread.start()

            self.debug('wait for all worker threads to finish')
            for thread in threads:
                thread.join()

            recipe_set_results = [thread.result for thread in threads]

            if any((isinstance(result, Exception) for result in recipe_set_results)):
                self.error('At least one task set raised an exception')
                self.error('Note: see detailed exception in debug log for more information')

                raise gluetool.GlueError('At least one task set raised an exception')

        else:
            # run all tasks one by one, on the same guest, restoring the snapshot between tasks
            self.info('Running {} tasks one by one'.format(len(tasks)))

            recipe_set_results = []

            for i, task in enumerate(recipe_set.find_all('task'), 1):
                self.info('running task #{} of {}'.format(i, len(tasks)))

                guest.debug("restoring snapshot '{}' before running next task".format(base_snapshot.name))
                actual_guest = guest.restore_snapshot(base_snapshot)

                recipe_set_results.append(self._run_task_set(actual_guest, [task], recipe_attrs, recipe_set_attrs))

        return self._merge_task_results(recipe_set_results)

    def _run_recipe_set_whole(self, guest, recipe_set):
        """
        Run tasks from a recipe set in a "classic" manner, runnign one by one
        on the same box.

        :param element recipe_set: <recipeSet/> element, gathering some tasks.
        """

        guest.info('Running recipe set tasks in the same environment, one by one')

        return self._run_task_set(guest, recipe_set.find_all('task'),
                                  recipe_set.find_all('recipe')[0].attrs, recipe_set.attrs)

    def _run_recipe_set(self, guest, recipe_set):
        """
        Run recipe set on a given guest.

        :param Guest guest: guest we use for our tests.
        :param element recipe_set: <recipeSet/> element, grouping tasks.
        """

        # this makes situation easier - I decided to limit number of <recipe/>
        # elements inside <recipeSet/> to exactly one. I don't know what options
        # would make wow to create more recipes inside recipeSet, and I want
        # to find out, but for the proof of concept, this makes my living easy
        # to bear.
        assert len(recipe_set.find_all('recipe')) == 1

        guest.debug('Running recipe set:\n{}'.format(recipe_set.prettify(encoding='utf-8')))

        if guest.supports_snapshots() is True and self.use_snapshots:
            results = self._run_recipe_set_isolated(guest, recipe_set)

        else:
            results = self._run_recipe_set_whole(guest, recipe_set)

        guest.debug('Recipe set finished')
        return results

    def _process_results(self, results):
        """
        Try to find at least one task that didn't complete or didn't pass.
        """

        self.debug('Try to find any non-PASS task')

        for task, runs in results.iteritems():
            self.debug("  task '{}'".format(task))

            for run in runs:
                self.debug("    Status='{}', Result='{}'".format(run['bkr_status'], run['bkr_result']))

                if run['bkr_status'].lower() == 'completed' and run['bkr_result'].lower() == 'pass':
                    continue

                self.debug('      We have our traitor!')
                return 'FAIL'

        return 'PASS'

    def sanity(self):
        if self.option('results-directory-template') and (self.parallelize_task_sets or self.use_snapshots):
            raise GlueError('Cannot use --results-directory-template with --parallelize-task-sets or --use-snapshots.')

        if self.parallelize_recipe_sets:
            self.info('Will run recipe sets in parallel')

        else:
            self.info('Will run recipe sets serially')

        if self.use_snapshots:
            if self.parallelize_task_sets:
                self.info('Will run recipe set tasks in parallel, using snapshots')
            else:
                self.info('Will run recipe set tasks serially, using snapshots')
        else:
            if self.parallelize_task_sets:
                raise IncompatibleOptionsError('--parallelize-task-sets is not supported when snapshots are disabled')

            self.info('Will run recipe set tasks serially, without snapshots')

    def execute(self):
        self.require_shared('restraint')

        schedule = self.shared('schedule') or []

        if self.parallelize_recipe_sets:
            self.info('Scheduled {} items, running them in parallel'.format(len(schedule)))

            threads = []

            for i, (guest, recipe_set) in enumerate(schedule):
                thread = gluetool.utils.WorkerThread(self.logger, self._run_recipe_set, fn_args=(guest, recipe_set),
                                                     name='recipe-set-runner-{}'.format(i))
                threads.append(thread)

                thread.start()

            self.debug('wait for all recipe set threads to finish')
            for thread in threads:
                thread.join()

            recipe_sets_results = [thread.result for thread in threads]

            if any((isinstance(result, Exception) for result in recipe_sets_results)):
                self.error('At least one recipe set raised an exception')
                self.error('Note: see detailed exception in debug log for more information')

                raise gluetool.GlueError('At least one recipe set raised an exception')

        else:
            self.info('Scheduled {} items, running them one by one'.format(len(schedule)))

            recipe_sets_results = [self._run_recipe_set(guest, recipe_set) for guest, recipe_set in schedule]

        results = self._merge_task_results(recipe_sets_results)

        log_dict(self.debug, 'Recipe sets results', results)

        overall_result = self._process_results(results)

        self.info('Result of testing: {}'.format(overall_result))

        publish_result(self, RestraintTestResult, overall_result, payload=results)