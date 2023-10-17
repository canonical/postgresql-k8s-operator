#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the PostgreSQL charm."""
import logging
from typing import Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    profile: str
    profile_limit_memory: Optional[int]
    plugin_citext_enable: bool
    plugin_debversion_enable: bool
    plugin_hstore_enable: bool
    plugin_pg_trgm_enable: bool
    plugin_plpython3u_enable: bool
    plugin_unaccent_enable: bool
    plugin_bloom_enable: bool
    plugin_btree_gin_enable: bool
    plugin_btree_gist_enable: bool
    plugin_cube_enable: bool
    plugin_dict_int_enable: bool
    plugin_dict_xsyn_enable: bool
    plugin_earthdistance_enable: bool
    plugin_fuzzystrmatch_enable: bool
    plugin_intarray_enable: bool
    plugin_isn_enable: bool
    plugin_lo_enable: bool
    plugin_ltree_enable: bool
    plugin_old_snapshot_enable: bool
    plugin_pg_freespacemap_enable: bool
    plugin_pgrowlocks_enable: bool
    plugin_pgstattuple_enable: bool
    plugin_pg_visibility_enable: bool
    plugin_seg_enable: bool
    plugin_tablefunc_enable: bool
    plugin_tcn_enable: bool
    plugin_tsm_system_rows_enable: bool
    plugin_tsm_system_time_enable: bool
    plugin_uuid_ossp_enable: bool

    @classmethod
    def keys(cls) -> list[str]:
        """Return config as list items."""
        return list(cls.__fields__.keys())

    @classmethod
    def plugin_keys(cls) -> filter:
        """Return plugin config names in a iterable."""
        return filter(lambda x: x.startswith("plugin_"), cls.keys())

    @validator("profile")
    @classmethod
    def profile_values(cls, value: str) -> Optional[str]:
        """Check profile config option is one of `testing` or `production`."""
        if value not in ["testing", "production"]:
            raise ValueError("Value not one of 'testing' or 'production'")

        return value

    @validator("profile_limit_memory")
    @classmethod
    def profile_limit_memory_validator(cls, value: int) -> Optional[int]:
        """Check profile limit memory."""
        if value < 128:
            raise ValueError("PostgreSQL Charm requires at least 128MB")
        if value > 9999999:
            raise ValueError("`profile-limit-memory` limited to 7 digits (9999999MB)")

        return value
