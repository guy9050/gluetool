from .ci import CI
from .ci import CIError, SoftCIError, CIRetryError, CICommandError, Failure
from .ci import Module
from .ci import retry
from .log import Logging
from . import utils

try:
    from .version import __version__
except ImportError:
    __version__ = '0.1-dev'

__all__ = ['__version__',
           'CI',
           'CIError', 'SoftCIError', 'CIRetryError', 'CICommandError', 'Failure',
           'Module',
           'Logging',
           'retry',
           'utils']
