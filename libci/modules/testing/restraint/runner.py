import copy
import os
from collections import defaultdict
import enum
import bs4

import libci
from libci.log import log_blob, log_dict
from libci.results import TestResult, publish_result
from libci.utils import new_xml_element


# The exit status values come from restraint sources: https://github.com/p3ck/restraint/blob/master/src/errors.h
# I failed to find any documentation on this...
class RestraintExitCodes(enum.IntEnum):
    # pylint: disable=invalid-name
    RESTRAINT_TASK_RUNNER_RESULT_ERROR = 10
    RESTRAINT_SSH_ERROR = 14


class IncompatibleOptionsError(libci.SoftCIError):
    SUBJECT = 'Incompatible options detected'
    BODY = """
Configuration of your component uses incompatible options for `restraint-runner` module:

    {message}

Please, review the configuration of your component - the default settings are usually sane
and should not lead to this error. For valid options, their values and possible combinations
see documentation for `restraint-runner` ([1]).

[1] https://url.corp.redhat.com/e19c028
    """

    def __init__(self):
        msg = '--parallelize-task-sets is not supported when snapshots are disabled'
        super(IncompatibleOptionsError, self).__init__(msg)


class RestraintTestResult(TestResult):
    # pylint: disable=too-few-public-methods

    def __init__(self, ci, overall_result, **kwargs):
        super(RestraintTestResult, self).__init__(ci, 'restraint', overall_result, **kwargs)

    def _serialize_to_xunit(self):
        test_suite = super(RestraintTestResult, self)._serialize_to_xunit()

        if self.ci.has_shared('beah_xunit_serialize'):
            self.ci.shared('beah_xunit_serialize', test_suite, self)

        else:
            self.ci.warn("To serialize result to xUnit format, 'beah_xunit_serialize' shared function is required",
                         sentry=True)

        return test_suite

    @classmethod
    def _unserialize_from_json(cls, ci, input_data):
        return RestraintTestResult(ci, input_data['overall_result'], ids=input_data['ids'], urls=input_data['urls'],
                                   payload=input_data['payload'])


class RestraintRunner(libci.Module):
    """
    This module runs recipe sets, prepared by other modules, using restraint harness.
    It can make use of snapshots (if supported by guests) to isolate each test, and
    it's able to parallelize tests.

    The results are provided in the form similar to what beaker module does - short summary
    in console log, artifact file, and shared function to publish results for later
    modules as well.
    """

    name = 'restraint-runner'

    options = {
        'use-snapshots': {
            'help': 'Enable or disable use of snapshots (if supported by guests) (default: no)',
            'default': 'no',
            'metavar': 'yes|no'
        },
        'parallelize-recipe-sets': {
            'help': 'Enable or disable parallelization of recipe sets (default: no)',
            'default': 'no',
            'metavar': 'yes|no'
        },
        'parallelize-task-sets': {
            'help': 'Enable or disable parallelization of test sets (default: no)',
            'default': 'no',
            'metavar': 'yes|no'
        }
    }

    _result_class = None

    def _bool_option(self, name):
        value = self.option(name)
        if value is None:
            return False

        return True if value.strip().lower() == 'yes' else False

    @libci.utils.cached_property
    def use_snapshots(self):
        return self._bool_option('use-snapshots')

    @libci.utils.cached_property
    def parallelize_recipe_sets(self):
        return self._bool_option('parallelize-recipe-sets')

    @libci.utils.cached_property
    def parallelize_task_sets(self):
        return self._bool_option('parallelize-task-sets')

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

        # results are stored in a temporary directory which is logged on the first
        # line of restraint's output
        header_line = output.split('\n')[0].strip()
        if not header_line.startswith('Using ./tmp'):
            raise libci.CIError('Don\'t know where to find restraint results')

        job_dir = os.path.join('.', header_line.split(' ')[1].strip())

        # XML produced by restraint
        with open(os.path.join(job_dir, 'job.xml'), 'r') as f:
            self.debug('XML produced by restraint lies in {}'.format(f.name))

            job_results = bs4.BeautifulSoup(f.read(), 'xml')

        # results accumulates results (<task name>: <task runs>) we want to return upwards
        results = defaultdict(list)

        if 'BUILD_URL' in os.environ:
            def artifact_path(s):
                return '{}/artifact/{}/{}'.format(os.getenv('BUILD_URL'), job_dir, s)

        else:
            def artifact_path(s):
                path = os.path.abspath('{}/{}'.format(job_dir, s))

                return 'file://localhost/{}'.format(path)

        for task_results in job_results.recipeSet.recipe.find_all('task'):
            # find journal if there's one available for the task
            journal = None

            journal_log = task_results.logs.find_all('log', filename='journal.xml')
            if journal_log:
                with open(os.path.join(job_dir, journal_log[0]['path']), 'r') as f:
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

        output = self.shared('restraint', guest, job)

        if output.exit_code != 0:
            self.debug('restraint exited with invalid exit code {}'.format(output.exit_code))

            if output.exit_code == RestraintExitCodes.RESTRAINT_TASK_RUNNER_RESULT_ERROR:
                # "One or more tasks failed" error - this is a good, well behaving error.
                # We can safely move on and process results stored in restraint's directory.
                self.info('restraint reports: One or more tasks failed')

            else:
                raise libci.CIError('restraint command exited with return code {}: {}'.format(
                    output.exit_code, output.stderr))

        log_blob(self.info, 'Task set output', output.stdout)

        result = self._gather_task_set_results(guest, output.stdout)
        log_dict(self.debug, 'task set result', result)

        return result

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
                thread = libci.utils.WorkerThread(actual_guest.logger, self._run_task_set,
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

                raise libci.CIError('At least one task set raised an exception')

        else:
            # run all tasks one by one, on the same guest, restoring the snapshot between tasks
            self.info('Running {} tasks one by one'.format(len(tasks)))

            recipe_set_results = []

            for i, task in enumerate(recipe_set.find_all('task'), 1):
                self.info('running task #{} of {}'.format(i, len(tasks)))

                guest.debug("restoring snapshot '{}' before running next task".format(base_snapshot))
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
                raise IncompatibleOptionsError()

            self.info('Will run recipe set tasks serially, without snapshots')

    def execute(self):
        self.require_shared('restraint')

        schedule = self.shared('schedule') or []

        if self.parallelize_recipe_sets:
            self.info('Scheduled {} items, running them in parallel'.format(len(schedule)))

            threads = []

            for i, (guest, recipe_set) in enumerate(schedule):
                thread = libci.utils.WorkerThread(self.logger, self._run_recipe_set, fn_args=(guest, recipe_set),
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

                raise libci.CIError('At least one recipe set raised an exception')

        else:
            self.info('Scheduled {} items, running them one by one'.format(len(schedule)))

            recipe_sets_results = [self._run_recipe_set(guest, recipe_set) for guest, recipe_set in schedule]

        results = self._merge_task_results(recipe_sets_results)

        log_dict(self.debug, 'Recipe sets results', results)

        overall_result = self._process_results(results)

        self.info('Result of testing: {}'.format(overall_result))

        publish_result(self, RestraintTestResult, overall_result, payload=results)
