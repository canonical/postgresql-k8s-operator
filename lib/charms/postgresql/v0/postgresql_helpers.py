# Copyright 2022 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PostgreSQL's helpers Library.

This library provides common PostgreSQL-specific features for the
PostgreSQL machine and Kubernetes charms.
"""
import logging
from typing import List

import psycopg2
from psycopg2._psycopg import AsIs

logger = logging.getLogger(__name__)


def build_username(app_name: str, admin: bool = False) -> str:
    """Creates a database user.

    Args:
        app_name: psycopg2 connection object.
        admin: whether the user should have a prefix in
            the name indicating it's an admin user.
    """
    # Default prefix to not conflict with users manually created.
    prefix = "juju_"
    if admin:
        prefix += "admin_"
    # Replace "-" invalid character (otherwise it'll generate an error later).
    return f"{prefix}{app_name.replace('-', '_')}"


def build_connection_string(database: str, user: str, host: str, password: str) -> str:
    """Builds a connection string based on authentication details.

    Args:
        database: name of the database to connect to.
        user: user used to connect to the database.
        host: address of the database server.
        password: password used to connect to the database.

    Returns:
         a connection string.
    """
    return f"dbname='{database}' user='{user}' host='{host}' password='{password}'"


def connect_to_database(
    database: str, user: str, host: str, password: str
) -> psycopg2.extensions.connection:
    """Creates an auth_query function for proxy applications.

    Args:
        database: name of the database to connect to.
        user: user used to connect to the database.
        host: address of the database server.
        password: password used to connect to the database.

    Returns:
         psycopg2 connection object.
    """
    connection = psycopg2.connect(
        f"dbname='{database}' user='{user}' host='{host}' password='{password}' connect_timeout=1"
    )
    connection.autocommit = True
    return connection


def create_auth_query_function(connection: psycopg2.extensions.connection) -> str:
    """Creates an auth_query function for proxy applications.

    Args:
        connection: psycopg2 connection object.

    Returns:
         name of the created auth_query function.
    """
    auth_query_function = "proxy_auth_query"
    execute(
        connection,
        [
            f"""
            CREATE OR REPLACE FUNCTION {auth_query_function}(uname TEXT)
            RETURNS TABLE (usename name, passwd text) as
            $$
              SELECT usename, passwd FROM pg_shadow WHERE usename=$1;
            $$
            LANGUAGE sql SECURITY DEFINER;
            """
        ],
    )
    return auth_query_function


def create_database(connection: psycopg2.extensions.connection, database: str, user: str) -> None:
    """Creates a new database.

    Args:
        connection: psycopg2 connection object.
        database: database to be created.
        user: user that will have access to the database.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {pgidentifier(database)};")
        cursor.execute(f"GRANT ALL PRIVILEGES ON DATABASE {pgidentifier(database)} TO {pgidentifier(user)};")

def create_user(
    connection: psycopg2.extensions.connection, user: str, password: str, admin: bool = False
) -> None:
    """Creates a database user.

    Args:
        connection: psycopg2 connection object.
        user: user to be created.
        password: password to be assigned to the user.
        admin: whether the user should have additional admin privileges.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE ROLE {user} WITH LOGIN{' SUPERUSER' if admin else ''} ENCRYPTED PASSWORD '{password}';")


def database_exists(connection: psycopg2.extensions.connection, database: str) -> bool:
    """Checks for database existence.

    Args:
        connection: psycopg2 connection object.
        database: name of the database to be checked.

    Returns:
        whether the database exists.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT 1 FROM pg_database WHERE datname={pgidentifier(database)};")
        result = cursor.fetchone()
        # If the data was returned, then the database exists.
        exists = result[0] == 1 if result else False
    return exists


def quote_identifier(identifier: str):
    r'''Quote an identifier, such as a table or role name.

    In SQL, identifiers are quoted using " rather than ' (which is reserved
    for strings).

    >>> print(quote_identifier('hello'))
    "hello"

    Quotes and Unicode are handled if you make use of them in your
    identifiers.

    >>> print(quote_identifier("'"))
    "'"
    >>> print(quote_identifier('"'))
    """"
    >>> print(quote_identifier("\\"))
    "\"
    >>> print(quote_identifier('\\"'))
    "\"""
    >>> print(quote_identifier('\\ aargh" \u0441\u043b\u043e\u043d'))
    U&"\\ aargh"" \0441\043b\043e\043d"
    '''
    try:
        identifier.encode("US-ASCII")
        return '"{}"'.format(identifier.replace('"', '""'))
    except UnicodeEncodeError:
        escaped = []
        for c in identifier:
            if c == "\\":
                escaped.append("\\\\")
            elif c == '"':
                escaped.append('""')
            else:
                c = c.encode("US-ASCII", "backslashreplace").decode("US-ASCII")
                # Note Python only supports 32 bit unicode, so we use
                # the 4 hexdigit PostgreSQL syntax (\1234) rather than
                # the 6 hexdigit format (\+123456).
                if c.startswith("\\u"):
                    c = "\\" + c[2:]
                escaped.append(c)
        return 'U&"%s"' % "".join(escaped)


def pgidentifier(token: str):
    """Wrap a string for interpolation by psycopg2 as an SQL identifier"""
    return AsIs(quote_identifier(token))

