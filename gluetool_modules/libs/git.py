import os
import os.path
import stat
import tempfile

import gluetool.log
import gluetool.utils

# Type annotations
# pylint: disable=unused-import,wrong-import-order
from typing import cast, Optional  # cast, Any, Callable, Dict, List, NamedTuple, Optional, Tuple  # noqa


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

    # pylint: disable=too-many-arguments
    def clone(self,
              logger=None,  # type: Optional[gluetool.log.ContextAdapter]
              branch=None,  # type: Optional[str]
              ref=None,  # type: Optional[str]
              path=None,  # type: Optional[str]
              prefix=None  # type: Optional[str]
             ):  # noqa
        # type: (...) -> str
        """
        Clone remote repository.

        :param gluetool.log.ContextAdapter logger: logger to use.
        :param str ref: checkout specified git ref. If not set, the one specified during ``RemoteGitRepository``
            initialization is used. If none of these was specified, top of the branch is checked out.
        :param str branch: checkout specified branch. If not set, the one specified during ``RemoteGitRepository``
            initialization is used. If none of these was specified, ``master`` is used by default.
        :param str path: if specified, clone into this path. Othwerwise, a temporary directory is created.
        :param str prefix: if specified and `path` wasn't set, it is used as a prefix of directory created
            to hold the clone.
        :returns: path to a clone. If `path` was given explicitly, it is returned as-is. Otherwise,
            function created a temporary directory and its path relative to CWD is returned.
        """

        logger = logger or gluetool.log.Logging.get_logger()

        branch = branch or self.branch or 'master'
        ref = ref or self.ref

        original_path = path  # save the original path for later

        if path:
            actual_path = path

        elif prefix:
            actual_path = tempfile.mkdtemp(dir=os.getcwd(), prefix=prefix)

        else:
            actual_path = tempfile.mkdtemp(dir=os.getcwd())

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
            actual_path
        ]

        try:
            cmd.run()

        except gluetool.GlueCommandError as exc:
            raise gluetool.GlueError('Failed to clone git repository: {}'.format(exc.output.stderr))

        # Make sure it's possible to enter this directory for other parties. We're not that concerned with privacy,
        # we'd rather let common users inside the repository when inspecting the pipeline artifacts. Therefore
        # setting clone directory permissions to ug=rwx,o=rx.

        # pylint: disable=line-too-long
        os.chmod(
            actual_path,
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
        )

        if ref:
            try:
                gluetool.utils.Command([
                    'git',
                    '-C', actual_path,
                    'checkout', ref
                ]).run()

            except gluetool.GlueCommandError as exc:
                raise gluetool.GlueError('Failed to checkout ref {}: {}'.format(ref, exc.output.stderr))

        # Since we used `dir` when creating repo directory, the path we have is absolute. That is not perfect,
        # we have an agreement with the rest of the world that we're living in current directory, which we consider
        # a workdir (yes, it would be better to have an option to specify it explicitly), we should get the relative
        # path instead.
        # This applies to path *we* generated only - if we were given a path, we won't touch it.
        return actual_path if original_path else os.path.relpath(actual_path, os.getcwd())
