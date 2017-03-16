import copy
import tempfile
import bs4

import libci


DEFAULT_RESTRAINT_PORT = 8081


class RestraintRunner(libci.Module):
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
        'parallelize-test-sets': {
            'help': 'Enable or disable parallelization of test sets (default: no)',
            'default': 'no',
            'metavar': 'yes|no'
        }
    }

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

    def _guest_restraint_address(self, guest):
        # pylint: disable=no-self-use
        return '{}:{}/{}'.format(guest.hostname, DEFAULT_RESTRAINT_PORT, guest.port)

    def _run_task_set(self, guest, task_set, recipe_attrs, recipe_set_attrs):
        """
        Run a set of tasks on the guest.

        :param Guest guest: guest to use for running tests.
        :param task_set: list of <task/> elements, representing separate tasks.
        :param dict recipe_attrs: additional attributes to set on <recipe/> element.
        :param dict recipe_set_attrs: additional attributes to set on <recipe_set/> element.
        """

        soup = bs4.BeautifulSoup('', 'xml')

        # Log our task set
        guest.debug('Task set:\n{}'.format('\n'.join([task.prettify() for task in task_set])))

        # Wrap task set in <job><recipeSet><recipe>... envelope
        job = soup.new_tag('job')
        job.append(soup.new_tag('recipeSet', **recipe_set_attrs))
        job.recipeSet.append(soup.new_tag('recipe', **recipe_attrs))

        for task in task_set:
            job.recipeSet.recipe.append(copy.copy(task))

        # We'll need this for restraint
        job_desc = job.prettify()

        self.debug('Job:\n{}'.format(job_desc))

        # Write out our job description, and tell restraint to run it
        with tempfile.NamedTemporaryFile() as f:
            f.write(job_desc)
            f.flush()

            try:
                output = libci.utils.run_command([
                    '/usr/bin/restraint', '-v',
                    '--host', '1={}@{}'.format(guest.username, self._guest_restraint_address(guest)),
                    '--job', f.name
                ], logger=guest.logger)

            except libci.CICommandError as e:
                if e.output.stderr.strip().startswith('One or more tasks failed'):
                    output = 'One or more tasks failed'

                else:
                    raise

            else:
                output = output.stdout

        self.info('Task set output:\n{}'.format(output))

        return ('task set result', )  # results

    def _run_recipe_set_isolated(self, guest, recipe_set):
        """
        Run tasks from a recipe set one by one, getting fresh snapshot for each task.

        :param element recipe_set: <recipeSet/> element, gathering some tasks.
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

            for i, (guest, task) in enumerate(zip(guests, tasks)):
                thread = libci.utils.WorkerThread(guest.logger, self._run_task_set,
                                                  fn_args=(guest, [task], recipe_attrs, recipe_set_attrs),
                                                  name='task-runner-{}'.format(i))
                threads.append(thread)

                thread.start()

            self.debug('wait for all worker threads to finish')
            for thread in threads:
                thread.join()

            results = [thread.result for thread in threads]

#            if any((isinstance(thread.result, Exception) for thread in setup_threads)):
#                self.error('At least one guest setup failed')
#                self.error('Note: see detailed exception in debug log for more information')
#                raise CIError('At least one guest setup failed')

        else:
            # run all tasks one by one, on the same guest, restoring the snapshot between tasks
            self.info('Running {} tasks one by one'.format(len(tasks)))

            results = []

            for task in recipe_set.find_all('task'):
                guest.debug("restoring snapshot '{}' before running next task".format(base_snapshot))
                actual_guest = guest.restore_snapshot(base_snapshot)

                results.append(self._run_task_set(actual_guest, [task], recipe_attrs, recipe_set_attrs))

        return results

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

        guest.info('Running recipe set:\n{}'.format(recipe_set.prettify()))

        if guest.supports_snapshots() is True and self.use_snapshots:
            results = self._run_recipe_set_isolated(guest, recipe_set)

        else:
            results = self._run_recipe_set_whole(guest, recipe_set)

        guest.info('Recipe set finished!')
        return results

    def execute(self):
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

            results = [thread.result for thread in threads]

        else:
            self.info('Scheduled {} items, running them one by one'.format(len(schedule)))

            results = [self._run_recipe_set(guest, recipe_set) for guest, recipe_set in schedule]

        self.info('Recipe sets results:\n{}'.format(results))

    def destroy(self, failure=None):
        pass
