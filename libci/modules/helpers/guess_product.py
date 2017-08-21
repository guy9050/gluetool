from libci import CIError, SoftCIError, Module
from libci.utils import cached_property, PatternMap


class IncompatibleOptionsError(SoftCIError):
    SUBJECT = 'Incompatible options detected'
    BODY = """
Configuration of your component uses incompatible options for `guess-product` module:

    {message}

Please, review the configuration of your component - the default settings are usually sane
and should not lead to this error. For valid options, their values and possible combinations
see documentation for `guess-product` ([1]).

[1]
    """


class GuessProduct(Module):
    """
    "Guess" product. User can choose from different possible methods of "guessing":

    * ``target-autodetection``: module will try to transform build target of brew task to a product
      identification using provided regex patterns in ``--pattern-map`` file;

    * ``force``: use specified product no matter what. Use ``--product`` option to set *what*
      product you wish to use.
    """

    name = 'guess-product'
    description = 'Guess product identification from a build target of a brew build.'

    options = {
        'method': {
            'help': 'What method to use for product "guessing".',
            'choices': ('target-autodetection', 'force'),
            'default': 'target-autodetection'
        },
        'product': {
            'help': 'Product identification, to help your method with guessing.'
        },
        'pattern-map': {
            'help': 'Path to a file with ``target`` => ``product`` patterns.'
        }
    }

    shared_functions = ['product']

    def __init__(self, *args, **kwargs):
        super(GuessProduct, self).__init__(*args, **kwargs)

        self._product = None

    def product(self):
        """
        Return guessed product.

        :rtype: str
        """

        return self._product

    @cached_property
    def pattern_map(self):
        return PatternMap(self.option('pattern-map'), logger=self.logger)

    def _guess_force(self):
        self._product = self.option('product')

        self.debug("forcing '{}' as the product".format(self._product))

    def _guess_target_autodetection(self):
        task = self.shared('task')
        if task is None:
            raise CIError("Using 'target-autodetect' method without a brew task does not work")

        target = task.target

        self._product = self.pattern_map.match(target)
        self.debug("transformed target '{}' to the product '{}'".format(target, self._product))

    _methods = {
        'force': _guess_force,
        'target-autodetection': _guess_target_autodetection
    }

    def sanity(self):
        product_required = ('force',)
        product_ignored = ('target-autodetection',)

        method = self.option('method')
        product = self.option('product')

        if method == 'target-autodetection' and not self.option('pattern-map'):
            raise CIError("--pattern-map option is required with method '{}'".format(method))

        if method in product_required and not product:
            raise IncompatibleOptionsError("--product option is required with method '{}'".format(method))

        if method in product_ignored and product:
            raise IncompatibleOptionsError("--product option is ignored with method '{}'".format(method))

    def execute(self):
        method = self._methods.get(self.option('method'), None)
        if method is None:
            raise IncompatibleOptionsError("Unknown 'guessing' method '{}'".format(self.option('method')))

        method(self)
        self.info("Using product '{}'".format(self._product))
