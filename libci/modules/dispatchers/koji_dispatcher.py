import re

import libci
from libci.log import log_dict
from libci.utils import cached_property


class KojiTaskDispatcher(libci.Module):
    """
    A generic dispatcher for Brew/Koji builds. Given the build, it asks other modules
    - via ``plan_test_batch`` shared function - for modules and their arguments, and
    than runs them.
    """

    name = ['brew-dispatcher', 'koji-dispatcher']
    description = 'Configurable brew dispatcher'

    options = {
        'pipeline-categories': {
            # pylint: disable=line-too-long
            'help': 'Mapping between jobs and their default pipeline category, as reported later by ``pipeline-state-reporter`` module.',
            'type': str,
            'default': None
        }
    }

    def __init__(self, *args, **kwargs):
        super(KojiTaskDispatcher, self).__init__(*args, **kwargs)

        self.build = {}

        self._thread_id = None
        self._subthread_counter = 0
        self._child_thread_id = None

    @cached_property
    def pipeline_categories(self):
        if not self.option('pipeline-categories'):
            return None

        return libci.utils.SimplePatternMap(self.option('pipeline-categories'), logger=self.logger)

    def execute(self):
        """
        Dispatch tests for a component. Ask for what modules should be called, and their options,
        and run them.
        """

        self.require_shared('plan_test_batch')

        batch = self.shared('plan_test_batch')
        log_dict(self.debug, 'prepared test batch', batch)

        if self.has_shared('thread_id'):
            self._thread_id = self.shared('thread_id')

        for module, args in batch:
            if self._thread_id is not None:
                self._subthread_counter += 1

                self._child_thread_id = '{}-{}'.format(self._thread_id, self._subthread_counter)
                args = ['--testing-thread-id', self._child_thread_id] + args

                log_dict(self.debug, 'augmented args with thread-id', args)

            if self.has_shared('report_pipeline_state'):
                # finding the correct category might be tricky
                category = 'other'

                # try to find out whether the command sets pipeline category, overriding any static setting
                joined_args = ' '.join(args)

                # pylint: disable=line-too-long
                match = re.search(r"""--pipeline-state-reporter-options\s*=?[\"']?\s*--category\s*=?(.*?)[ \"']""", joined_args)  # Ignore PEP8Bear
                if match is not None:
                    category = match.group(1)

                # if not, there might be defaults
                elif self.pipeline_categories is not None:
                    full_command = [module] + args

                    try:
                        # try to match our command with an entry from the category map, to get what
                        # confgurator thinks would be an appropriate default category for such command

                        category = self.pipeline_categories.match(' '.join(full_command))

                    except libci.CIError:
                        # pylint: disable=line-too-long

                        self.warn('Cannot find a pipeline category for job:\n{}'.format(libci.log.format_dict(full_command)), sentry=True)    # Ignore PEP8Bear

                self.shared('report_pipeline_state', 'scheduled', thread_id=self._child_thread_id, category=category)

            log_dict(self.debug, 'command to dispatch', [module, args])
            self.info('    {} {}'.format(module, ' '.join(args)))

            self.run_module(module, args)
