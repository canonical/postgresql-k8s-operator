# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest
from unittest.mock import call, patch

import psycopg2
from charms.postgresql_k8s.v0.postgresql import PostgreSQLCreateDatabaseError
from ops.testing import Harness
from psycopg2.sql import SQL, Composed, Identifier

from charm import PostgresqlOperatorCharm
from constants import PEER


class TestPostgreSQL(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def setUp(self):
        self.harness = Harness(PostgresqlOperatorCharm)
        self.addCleanup(self.harness.cleanup)

        # Set up the initial relation and hooks.
        self.peer_rel_id = self.harness.add_relation(PEER, "postgresql-k8s")
        self.harness.add_relation_unit(self.peer_rel_id, "postgresql-k8s/0")
        self.harness.begin()
        self.charm = self.harness.charm

    @patch("charms.postgresql_k8s.v0.postgresql.PostgreSQL.enable_disable_extensions")
    @patch(
        "charms.postgresql_k8s.v0.postgresql.PostgreSQL._generate_database_privileges_statements"
    )
    @patch("charms.postgresql_k8s.v0.postgresql.PostgreSQL._connect_to_database")
    def test_create_database(
        self,
        _connect_to_database,
        _generate_database_privileges_statements,
        _enable_disable_extensions,
    ):
        # Test a successful database creation.
        database = "test_database"
        user = "test_user"
        plugins = ["test_plugin_1", "test_plugin_2"]
        with self.harness.hooks_disabled():
            rel_id = self.harness.add_relation("database", "application")
            self.harness.add_relation_unit(rel_id, "application/0")
            self.harness.update_relation_data(rel_id, "application", {"database": database})
        database_relation = self.harness.model.get_relation("database")
        client_relations = [database_relation]
        schemas = [("test_schema_1",), ("test_schema_2",)]
        _connect_to_database.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = (
            schemas
        )
        self.charm.postgresql.create_database(database, user, plugins, client_relations)
        execute = _connect_to_database.return_value.cursor.return_value.execute
        execute.assert_has_calls(
            [
                call(
                    Composed(
                        [
                            SQL("REVOKE ALL PRIVILEGES ON DATABASE "),
                            Identifier(database),
                            SQL(" FROM PUBLIC;"),
                        ]
                    )
                ),
                call(
                    Composed(
                        [
                            SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                            Identifier(database),
                            SQL(" TO "),
                            Identifier(user),
                            SQL(";"),
                        ]
                    )
                ),
                call(
                    Composed(
                        [
                            SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                            Identifier(database),
                            SQL(" TO "),
                            Identifier("admin"),
                            SQL(";"),
                        ]
                    )
                ),
                call(
                    Composed(
                        [
                            SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                            Identifier(database),
                            SQL(" TO "),
                            Identifier("backup"),
                            SQL(";"),
                        ]
                    )
                ),
                call(
                    Composed(
                        [
                            SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                            Identifier(database),
                            SQL(" TO "),
                            Identifier("replication"),
                            SQL(";"),
                        ]
                    )
                ),
                call(
                    Composed(
                        [
                            SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                            Identifier(database),
                            SQL(" TO "),
                            Identifier("rewind"),
                            SQL(";"),
                        ]
                    )
                ),
                call(
                    Composed(
                        [
                            SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                            Identifier(database),
                            SQL(" TO "),
                            Identifier("operator"),
                            SQL(";"),
                        ]
                    )
                ),
                call(
                    Composed(
                        [
                            SQL("GRANT ALL PRIVILEGES ON DATABASE "),
                            Identifier(database),
                            SQL(" TO "),
                            Identifier("monitoring"),
                            SQL(";"),
                        ]
                    )
                ),
            ]
        )
        _generate_database_privileges_statements.assert_called_once_with(
            1, [schemas[0][0], schemas[1][0]], user
        )
        _enable_disable_extensions.assert_called_once_with(
            {plugins[0]: True, plugins[1]: True}, database
        )

        # Test when two relations request the same database.
        _connect_to_database.reset_mock()
        _generate_database_privileges_statements.reset_mock()
        with self.harness.hooks_disabled():
            other_rel_id = self.harness.add_relation("database", "other-application")
            self.harness.add_relation_unit(other_rel_id, "other-application/0")
            self.harness.update_relation_data(
                other_rel_id, "other-application", {"database": database}
            )
        other_database_relation = self.harness.model.get_relation("database", other_rel_id)
        client_relations = [database_relation, other_database_relation]
        self.charm.postgresql.create_database(database, user, plugins, client_relations)
        _generate_database_privileges_statements.assert_called_once_with(
            2, [schemas[0][0], schemas[1][0]], user
        )

        # Test a failed database creation.
        _enable_disable_extensions.reset_mock()
        execute.side_effect = psycopg2.Error
        with self.assertRaises(PostgreSQLCreateDatabaseError):
            self.charm.postgresql.create_database(database, user, plugins, client_relations)
        _enable_disable_extensions.assert_not_called()

    def test_generate_database_privileges_statements(self):
        # Test with only one established relation.
        self.assertEqual(
            self.charm.postgresql._generate_database_privileges_statements(
                1, ["test_schema_1", "test_schema_2"], "test_user"
            ),
            [
                Composed(
                    [
                        SQL(
                            "SELECT 'ALTER TABLE '|| schemaname || '.\"' || tablename ||'\" OWNER TO "
                        ),
                        Identifier("test_user"),
                        SQL(
                            ";' AS statement\nINTO TEMP TABLE temp_table\nFROM pg_tables WHERE NOT schemaname IN ('pg_catalog', 'information_schema')\nUNION SELECT 'ALTER SEQUENCE '|| sequence_schema || '.\"' || sequence_name ||'\" OWNER TO "
                        ),
                        Identifier("test_user"),
                        SQL(
                            ";' AS statement\nFROM information_schema.sequences WHERE NOT sequence_schema IN ('pg_catalog', 'information_schema');\nDO\n$$\nDECLARE r RECORD;\nBEGIN\n  FOR r IN (select * from temp_table) LOOP\n      EXECUTE format(r.statement);\n  END LOOP;\nEND; $$;"
                        ),
                    ]
                )
            ],
        )
        # Test with multiple established relations.
        self.assertEqual(
            self.charm.postgresql._generate_database_privileges_statements(
                2, ["test_schema_1", "test_schema_2"], "test_user"
            ),
            [
                Composed(
                    [
                        SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA "),
                        Identifier("test_schema_1"),
                        SQL(" TO "),
                        Identifier("test_user"),
                        SQL(";"),
                    ]
                ),
                Composed(
                    [
                        SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "),
                        Identifier("test_schema_1"),
                        SQL(" TO "),
                        Identifier("test_user"),
                        SQL(";"),
                    ]
                ),
                Composed(
                    [
                        SQL("GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "),
                        Identifier("test_schema_1"),
                        SQL(" TO "),
                        Identifier("test_user"),
                        SQL(";"),
                    ]
                ),
                Composed(
                    [
                        SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA "),
                        Identifier("test_schema_2"),
                        SQL(" TO "),
                        Identifier("test_user"),
                        SQL(";"),
                    ]
                ),
                Composed(
                    [
                        SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "),
                        Identifier("test_schema_2"),
                        SQL(" TO "),
                        Identifier("test_user"),
                        SQL(";"),
                    ]
                ),
                Composed(
                    [
                        SQL("GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "),
                        Identifier("test_schema_2"),
                        SQL(" TO "),
                        Identifier("test_user"),
                        SQL(";"),
                    ]
                ),
            ],
        )
