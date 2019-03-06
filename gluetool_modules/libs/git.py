import os
import tempfile

import gluetool.log
import gluetool.utils

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import Optional  # cast, Any, Callable, Dict, List, NamedTuple, Optional, Tuple  # noqa


class RemoteGitRepository(object):
    # pylint: disable=too-few-public-methods

    """
    A remote Git repository representation.

    :param str clone_url: remote URL to use for cloning the repository.
    :param str branch: if set, it is the default branch to use in actions on the repository.
    :param str ref: if set, it it is the default point in repo history to manipulate.
    :param str web_url: if set, it is the URL of web frontend of the repository.
    """

    def __init__(self, clone_url, branch=None, ref=None, web_url=None):
        # type: (str, Optional[str], Optional[str], Optional[str]) -> None

        self.clone_url = clone_url
        self.branch = branch
        self.ref = ref
        self.web_url = web_url

    def clone(self, logger=None, branch=None, ref=None, path=None):
        # type: (Optional[gluetool.log.ContextAdapter], Optional[str], Optional[str], Optional[str]) -> str
        """
        Clone remote repository.

        :param gluetool.log.ContextAdapter logger: logger to use.
        :param str ref: checkout specified git ref. If not set, the one specified during ``RemoteGitRepository``
            initialization is used. If none of these was specified, top of the branch is checked out.
        :param str branch: checkout specified branch. If not set, the one specified during ``RemoteGitRepository``
            initialization is used. If none of these was specified, ``master`` is used by default.
        :param str path: if specified, clone into this path. Othwerwise, a temporary directory is created.
        :returns: path to a clone.
        """

        logger = logger or gluetool.log.Logging.get_logger()

        branch = branch or self.branch or 'master'
        ref = ref or self.ref
        path = path or tempfile.mkdtemp(dir=os.getcwd())

        logger.info('cloning repo {} (branch {}, ref {})'.format(
            self.clone_url,
            branch,
            ref if ref else 'not specified'
        ))

        cmd = gluetool.utils.Command(['git', 'clone'], logger=logger)

        if not ref:
            cmd.options += [
                '--depth', '1',
                '-b', branch
            ]

        cmd.options += [
            self.clone_url,
            path
        ]

        try:
            cmd.run()

        except gluetool.GlueCommandError as exc:
            raise gluetool.GlueError('Failed to clone git repository: {}'.format(exc.output.stderr))

        if ref:
            try:
                gluetool.utils.Command([
                    'git',
                    '-C', path,
                    'checkout', ref
                ]).run()

            except gluetool.GlueCommandError as exc:
                raise gluetool.GlueError('Failed to checkout ref {}: {}'.format(ref, exc.output.stderr))

        return path
