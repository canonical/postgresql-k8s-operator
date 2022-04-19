#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest

from charms.postgresql.v0 import postgresql_helpers


class TestPostgreSQLHelpers(unittest.TestCase):
    def test_build_username(self):
        username = postgresql_helpers.build_username("test")
        self.assertEqual(username, "juju_test")

    def test_build_admin_username(self):
        username = postgresql_helpers.build_username("proxy", True)
        self.assertEqual(username, "juju_admin_proxy")

    def test_build_connection_string(self):
        connection_string = postgresql_helpers.build_connection_string(
            "test", "user", "db.example.com", "password"
        )
        self.assertEqual(
            connection_string,
            "dbname='test' user='user' host='db.example.com' password='password'",
        )
