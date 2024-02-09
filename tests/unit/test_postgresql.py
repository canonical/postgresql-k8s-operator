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
                            "DO $$\nDECLARE r RECORD;\nBEGIN\n  FOR r IN (SELECT statement FROM (SELECT 1 AS index,'ALTER TABLE '|| schemaname || '.\"' || tablename ||'\" OWNER TO "
                        ),
                        Identifier("test_user"),
                        SQL(
                            ";' AS statement\nFROM pg_tables WHERE NOT schemaname IN ('pg_catalog', 'information_schema')\nUNION SELECT 2 AS index,'ALTER SEQUENCE '|| sequence_schema || '.\"' || sequence_name ||'\" OWNER TO "
                        ),
                        Identifier("test_user"),
                        SQL(
                            ";' AS statement\nFROM information_schema.sequences WHERE NOT sequence_schema IN ('pg_catalog', 'information_schema')\nUNION SELECT 3 AS index,'ALTER FUNCTION '|| nsp.nspname || '.\"' || p.proname ||'\"('||pg_get_function_identity_arguments(p.oid)||') OWNER TO "
                        ),
                        Identifier("test_user"),
                        SQL(
                            ";' AS statement\nFROM pg_proc p JOIN pg_namespace nsp ON p.pronamespace = nsp.oid WHERE NOT nsp.nspname IN ('pg_catalog', 'information_schema')\nUNION SELECT 4 AS index,'ALTER VIEW '|| schemaname || '.\"' || viewname ||'\" OWNER TO "
                        ),
                        Identifier("test_user"),
                        SQL(
                            ";' AS statement\nFROM pg_catalog.pg_views WHERE NOT schemaname IN ('pg_catalog', 'information_schema')) AS statements ORDER BY index) LOOP\n      EXECUTE format(r.statement);\n  END LOOP;\nEND; $$;"
                        ),
                    ]
                ),
                "UPDATE pg_catalog.pg_largeobject_metadata\nSET lomowner = (SELECT oid FROM pg_roles WHERE rolname = 'test_user')\nWHERE lomowner = (SELECT oid FROM pg_roles WHERE rolname = 'operator');",
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

    def test_build_postgresql_parameters(self):
        # Test when not limit is imposed to the available memory.
        config_options = {
            "durability_test_config_option_1": True,
            "instance_test_config_option_2": False,
            "logging_test_config_option_3": "on",
            "memory_test_config_option_4": 1024,
            "optimizer_test_config_option_5": "scheduled",
            "other_test_config_option_6": "test-value",
            "profile": "production",
            "request_date_style": "ISO, DMY",
            "request_time_zone": "UTC",
            "request_test_config_option_7": "off",
            "response_test_config_option_8": "partial",
            "vacuum_test_config_option_9": 10.5,
        }
        self.assertEqual(
            self.charm.postgresql.build_postgresql_parameters(config_options, 1000000000),
            {
                "test_config_option_1": True,
                "test_config_option_2": False,
                "test_config_option_3": "on",
                "test_config_option_4": 1024,
                "test_config_option_5": "scheduled",
                "test_config_option_7": "off",
                "DateStyle": "ISO, DMY",
                "TimeZone": "UTC",
                "test_config_option_8": "partial",
                "test_config_option_9": 10.5,
                "shared_buffers": "250MB",
                "effective_cache_size": "750MB",
            },
        )

        # Test with a limited imposed to the available memory.
        parameters = self.charm.postgresql.build_postgresql_parameters(
            config_options, 1000000000, 600000000
        )
        self.assertEqual(parameters["shared_buffers"], "150MB")
        self.assertEqual(parameters["effective_cache_size"], "450MB")

        # Test when the profile is set to "testing".
        config_options["profile"] = "testing"
        parameters = self.charm.postgresql.build_postgresql_parameters(config_options, 1000000000)
        self.assertEqual(parameters["shared_buffers"], "128MB")
