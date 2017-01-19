from .ci import CI
from .ci import CIError
from .ci import CIRetryError
from .ci import Module
from .ci import retry
from . import utils

try:
    from .version import __version__
except ImportError:
    __version__ = '0.1-dev'

__all__ = ['__version__',
           'CI',
           'CIError',
           'CIRetryError',
           'Module',
           'retry',
           'utils']
