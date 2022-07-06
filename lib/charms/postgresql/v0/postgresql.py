"""TODO: Add a proper docstring here.

This is a placeholder docstring for this charm library. Docstrings are
presented on Charmhub and updated whenever you push a new version of the
library.

Complete documentation about creating and documenting libraries can be found
in the SDK docs at https://juju.is/docs/sdk/libraries.

See `charmcraft publish-lib` and `charmcraft fetch-lib` for details of how to
share and consume charm libraries. They serve to enhance collaboration
between charmers. Use a charmer's libraries for classes that handle
integration with their charm.

Bear in mind that new revisions of the different major API versions (v0, v1,
v2 etc) are maintained independently.  You can continue to update v0 and v1
after you have pushed v3.

Markdown is supported, following the CommonMark specification.
"""

import psycopg2
from psycopg2 import sql

# The unique Charmhub library identifier, never change it
LIBID = "b4ef28bf98604fb0b766c9e2b6ba26c7"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

class PostgreSQL:
    """Abstract class to encapsulate all operations related to the MySQL instance and cluster.

    This class handles the configuration of MySQL instances, and also the
    creation and configuration of MySQL InnoDB clusters via Group Replication.
    Some methods are platform specific and must be implemented in the related
    charm code.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        database: str,
    ):
        self.host = host
        self.user = user
        self.password = password
        self.database = database

    def _connect_to_database(self, database: str = None) -> psycopg2.extensions.connection:
        """Creates a connection to the database.

        Args:
            database: database to connect to (defaults to the database
                provided when the object for this class was created).

        Returns:
             psycopg2 connection object.
        """
        connection = psycopg2.connect(
            f"dbname='{database if database else self.database}' user='{self.user}' host='{self.host}' password='{self.password}' connect_timeout=1"
        )
        connection.autocommit = True
        return connection

    def create_database(self, database: str, user: str) -> None:
        """Creates a new database and grant privileges to a user on it.

        Args:
            database: database to be created.
            user: user that will have access to the database.
        """
        connection = self._connect_to_database()
        cursor = connection.cursor()
        cursor.execute(f"SELECT datname FROM pg_database WHERE datname='{database}';")
        if cursor.fetchone() is None:
            cursor.execute(sql.SQL("CREATE DATABASE {};").format(sql.Identifier(database)))
        cursor.execute(sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {};")
                       .format(sql.Identifier(database),
                               sql.Identifier(user)))

    def create_user(self, user: str, password: str, admin: bool = False) -> None:
        """Creates a database user.

        Args:
            user: user to be created.
            password: password to be assigned to the user.
            admin: whether the user should have additional admin privileges.
        """
        with self._connect_to_database() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT TRUE FROM pg_roles WHERE rolname='{user}';")
            user_definition = f"{user} WITH LOGIN{' SUPERUSER' if admin else ''} ENCRYPTED PASSWORD '{password}'"
            if cursor.fetchone() is not None:
                cursor.execute(f"ALTER ROLE {user_definition};")
            else:
                cursor.execute(f"CREATE ROLE {user_definition};")

    def delete_user(self, user: str) -> None:
        """Deletes a database user.

        Args:
            user: user to be deleted.
        """
        # List all databases.
        with self._connect_to_database() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT datname FROM pg_database WHERE datistemplate = false;")
            databases = [row[0] for row in cursor.fetchall()]

        # Existing objects need to be reassigned in each database before the user can be deleted.
        for database in databases:
            with self._connect_to_database(database) as connection, connection.cursor() as cursor:
                cursor.execute(f"REASSIGN OWNED BY {user} TO postgres;")
                cursor.execute(f"DROP OWNED BY {user};")

        # Delete the user.
        with self._connect_to_database() as connection, connection.cursor() as cursor:
            cursor.execute(f"DROP ROLE {user};")

    def get_postgresql_version(self) -> str:
        """Returns the PostgreSQL version.

        Returns:
            PostgreSQL version number.
        """
        with self._connect_to_database() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT version();")
            # Split to get only the version number.
            return cursor.fetchone()[0].split(" ")[1]

