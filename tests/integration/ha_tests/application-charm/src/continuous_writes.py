# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""This file is meant to run in the background continuously writing entries to PostgreSQL."""
import signal
import sys

import psycopg2 as psycopg2

interrupt_loop = False


def continuous_writes(connection_string: str, starting_number: int):
    # Define a handler for signals.
    def interrupt_handler(_, __):
        global interrupt_loop
        interrupt_loop = True

    signal.signal(signal.SIGINT, interrupt_handler)  # Handle ctrl+c.

    write_value = starting_number

    global interrupt_loop
    connection = None
    reconnect = False
    while not interrupt_loop:
        try:
            # Connect to the database and create the table that will store the inserted rows.
            if reconnect:
                connection.close()
            if connection is None or reconnect:
                connection = psycopg2.connect(connection_string)
                connection.autocommit = True
                reconnect = False
                with connection, connection.cursor() as cursor:
                    cursor.execute("CREATE TABLE IF NOT EXISTS continuous_writes(number INTEGER);")
            with connection, connection.cursor() as cursor:
                cursor.execute(f"INSERT INTO continuous_writes(number) VALUES({write_value});")
        except psycopg2.errors.ConnectionException as e:
            # this means that the primary was not able to be found. An application should try to
            # reconnect and re-write the previous value. Hence, we `continue` here, without
            # incrementing `write_value` as to try to insert this value again.
            reconnect = True
            f = open("/tmp/demofile1.txt", "a")
            f.write(str(type(e)))
            f.close()
            continue
        except psycopg2.InterfaceError as e:
            # We should not raise this exception but instead increment the write value and move
            # on, indicating that there was a failure writing to the database.
            # psycopg2.InterfaceError
            # psycopg2.OperationalError
            f = open("/tmp/demofile7.txt", "a")
            f.write(str(type(e)))
            f.write("\n")
            # f.write(e.pgcode)
            # f.write("\n")
            f.write(str(e))
            f.write("\n")
            f.write(f"{write_value}")
            f.write("\n")
            f.close()
            reconnect = True
            continue
        except psycopg2.OperationalError as e:
            # We should not raise this exception but instead increment the write value and move
            # on, indicating that there was a failure writing to the database.
            # psycopg2.InterfaceError
            # psycopg2.OperationalError
            f = open("/tmp/demofile4.txt", "a")
            f.write(str(type(e)))
            f.write("\n")
            # f.write(e.pgcode)
            f.write(str(dir(e)))
            f.write("\n")
            f.write(str(e))
            f.write("\n")
            f.write(f"{write_value}")
            f.write("\n")
            f.close()
            reconnect = True
            continue
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
            reconnect = True
            continue
        except Exception as e:
            f = open("/tmp/demofile3.txt", "a")
            f.write(str(type(e)))
            f.close()
            pass

        write_value += 1

    connection.close()
    print("connection closed")


def main():
    connection_string = sys.argv[1]
    starting_number = int(sys.argv[2])
    continuous_writes(connection_string, starting_number)


if __name__ == "__main__":
    main()
