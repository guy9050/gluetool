import psycopg2
from psycopg2.extras import NamedTupleCursor
import libci


class CIPostgreSQL(libci.Module):
    """
    This module provides connection to a PostgreSQL database via psycopg2 module:

    http://initd.org/psycopg/

    Connection is compliant with Python Database API Specification v2.0
    Documentation of connection class can be found on:

    http://initd.org/psycopg/docs/connection.html
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
    shared_functions = ['postgresql', 'postgresql_cursor']

    def postgresql(self, reconnect=False):
        """
        Return psycopg2.connection class instance

        :param bool reconnect: Recreate connection if True (default: False)
        :return: posgtgresql connection
        :rtype: ``psycopg2.connection`` instance
        """
        if reconnect:
            self.connect()

        return self._connection

    def postgresql_cursor(self, reconnect=False, cursor_factory=NamedTupleCursor):
        """
        Return psycopg2.connection.cursor class instance with cursor_factory
        by default `psycopg2.extras.NamedTupleCursor` is used.

        :param bool reconnect: Recreate connection if True (default: False)
        :param cursor_factory: A cursor factory class from psycopg2.extras, by default `NamedTupleCursor``
        :return: posgtgresql cursor
        :rtype: psycopg2.connection.cursor
        :raises libci.CIError: if connection object does not exist
        """
        if reconnect:
            self.connect()

        if not self._connection:
            raise libci.CIError("connection object not initialized, did you forget connect to database?")

        return self._connection.cursor(cursor_factory=cursor_factory)

    def connect(self):
        user = self.option('user')
        password = self.option('password')
        host = self.option('host')
        port = self.option('port')
        dbname = self.option('dbname')

        # connect to the instance
        self.info("connecting to database {}:{} may take some time".format(host, port))
        try:
            self._connection = psycopg2.connect(host=host, port=port, dbname=dbname,
                                                user=user, password=password)
        except Exception as exc:
            raise libci.CIError("Could not connect to PostgreSQL server '{}': {}".format(host, exc.message))

    def server_version(self):
        cursor = self._connection.cursor()
        cursor.execute("SELECT version()")
        row = cursor.fetchone()
        if row is None:
            raise libci.CIError("could not fetch server version")
        return row[0]

    def execute(self):
        # connecto to database
        self.connect()
        host = self.option('host')
        version = self.server_version()

        # be informative about the database connection
        self.info('connected to postgresql \'{}\' version \'{}\''.format(host, version))
