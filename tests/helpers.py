#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import yaml

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
STORAGE_PATH = METADATA["storage"]["data"]["location"]

# PGDATA_PATH points to the Debian compatibility symlink path kept for tools that expect the
# traditional PostgreSQL directory layout.
PGDATA_PATH = "/var/lib/postgresql/16/main"

# ACTUAL_PGDATA_PATH points to the real storage location used by Patroni.
ACTUAL_PGDATA_PATH = f"{STORAGE_PATH}/16/main"
