# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""This file is meant to run in the background continuously writing entries to PostgreSQL."""
import sys

import psycopg2 as psycopg2


def continuous_writes(connection_string: str, starting_number: int):
    write_value = starting_number

    try:
        with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
            connection.autocommit = True
            cursor.execute("CREATE TABLE IF NOT EXISTS continuous_writes(number INTEGER);")
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS number ON continuous_writes(number);"
            )
    finally:
        connection.close()

    while True:
        try:
            with psycopg2.connect(connection_string) as connection, connection.cursor() as cursor:
                connection.autocommit = True
                cursor.execute(f"INSERT INTO continuous_writes(number) VALUES({write_value});")
        except (psycopg2.OperationalError, psycopg2.errors.ReadOnlySqlTransaction) as e:
            # We should not raise this exception but instead increment the write value and move
            # on, indicating that there was a failure writing to the database.
            # psycopg2.InterfaceError
            # psycopg2.OperationalError
            f = open("/tmp/demofile3.txt", "a")
            f.write(str(type(e)))
            f.write("\n")
            f.write(str(e.pgcode))
            f.write("\n")
            f.write(str(e.pgerror))
            f.write("\n")
            f.write(str(e))
            f.write("\n")
            f.write(f"{write_value}")
            f.write("\n")
            f.close()
            continue
        except psycopg2.errors.UniqueViolation as e:
            # We should not raise this exception but instead increment the write value and move
            # on, indicating that there was a failure writing to the database.
            # psycopg2.InterfaceError
            # psycopg2.OperationalError
            f = open("/tmp/demofile8.txt", "a")
            f.write(str(type(e)))
            f.write("\n")
            f.write(str(e.pgcode))
            f.write("\n")
            f.write(str(e.pgerror))
            f.write("\n")
            f.write(str(e))
            f.write("\n")
            f.write(f"{write_value}")
            f.write("\n")
            f.close()
        except psycopg2.Error as e:
            # We should not raise this exception but instead increment the write value and move
            # on, indicating that there was a failure writing to the database.
            # psycopg2.InterfaceError
            # psycopg2.OperationalError
            f = open("/tmp/demofile2.txt", "a")
            f.write(str(type(e)))
            f.write("\n")
            f.write(str(e.pgcode))
            f.write("\n")
            f.write(str(e.pgerror))
            f.write("\n")
            f.write(str(e))
            f.write("\n")
            f.write(f"{write_value}")
            f.write("\n")
            f.close()
        finally:
            connection.close()

        write_value += 1


def main():
    connection_string = sys.argv[1]
    starting_number = int(sys.argv[2])
    continuous_writes(connection_string, starting_number)


if __name__ == "__main__":
    main()
