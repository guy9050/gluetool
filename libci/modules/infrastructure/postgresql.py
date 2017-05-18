import pgdb
import libci


class CIPostgreSQL(libci.Module):
    """
    This module provides connection to a PostgreSQL database via PyGreSQL library:

    http://www.pygresql.org/

    Connection is compliant with Python Database API Specification v2.0
    Documentation of connection object can be found on:

    http://www.pygresql.org/contents/pgdb/index.html
    """

    name = 'postgresql'
    description = 'Connect to PostgreSQL database'

    # shared connection object
    _connection = None

    options = {
        'user': {
            'help': 'Username (default: None)',
        },
        'password': {
            'help': 'Password (default: None)',
        },
        'dbname': {
            'help': 'Database name to connect to',
        },
        'host': {
            'help': 'Database server host (default: localhost)',
            'default': 'localhost',
        },
        'port': {
            'help': 'Database server port number(default: 5432)',
            'default': 5432,
        }
    }
    required_options = ['dbname']
    shared_functions = ['postgresql']

    def postgresql(self, reconnect=False):
        """ return pgdb.Connection object instance """
        if reconnect:
            self.connect()

        return self._connection

    def connect(self):
        user = self.option('user')
        password = self.option('password')
        host = self.option('host')
        port = self.option('port')
        dbname = self.option('dbname')

        # connect to the instance
        self.info("connecting to database {}:{} may take some time".format(host, port))
        try:
            self._connection = pgdb.connect(host=host, port=port, dbname=dbname,
                                            user=user, password=password)
        except Exception as e:
            self.debug('connection error: {}'.format(e))
            raise libci.CIError("could not connect to PostgreSQL '{}': {}".format(host, str(e)))

    def server_version(self):
        cursor = self._connection.cursor()
        row = cursor.execute("SELECT VERSION()").fetchone()
        return row[0] if row else None

    def execute(self):
        # connecto to database
        self.connect()
        host = self.option('host')
        version = self.server_version()

        # be informative about the database connection
        self.info('connected to postgresql \'{}\' version \'{}\''.format(host, version))
