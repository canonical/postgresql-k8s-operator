#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


from ..helpers import METADATA
from ..new_relations.test_new_relations import (
    APPLICATION_APP_NAME,
)

APP_NAME = METADATA["name"]
DB_RELATION = "db"
DATABASE_RELATION = "database"
FIRST_DATABASE_RELATION = "first-database"
APP_NAMES = [APP_NAME, APPLICATION_APP_NAME]
