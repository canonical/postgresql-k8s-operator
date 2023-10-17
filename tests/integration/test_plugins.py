#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    DATABASE_APP_NAME,
    build_and_deploy,
    db_connect,
    get_password,
    get_primary,
    get_unit_address,
)

logger = logging.getLogger(__name__)

CITEXT_EXTENSION_STATEMENT = "CREATE TABLE citext_test (value CITEXT);"
DEBVERSION_EXTENSION_STATEMENT = "CREATE TABLE debversion_test (value DEBVERSION);"
HSTORE_EXTENSION_STATEMENT = "CREATE TABLE hstore_test (value hstore);"
PG_TRGM_EXTENSION_STATEMENT = "SELECT word_similarity('word', 'two words');"
PLPYTHON3U_EXTENSION_STATEMENT = 'CREATE FUNCTION plpython_test() RETURNS varchar[] AS $$ return "hello" $$ LANGUAGE plpython3u;'
UNACCENT_EXTENSION_STATEMENT = "SELECT ts_lexize('unaccent','Hôtel');"
BLOOM_EXTENSION_STATEMENT = (
    "CREATE TABLE tbloom_test (i int);CREATE INDEX btreeidx ON tbloom_test USING bloom (i);"
)
BTREEGIN_EXTENSION_STATEMENT = "CREATE TABLE btree_gin_test (a int4);CREATE INDEX btreeginidx ON btree_gin_test USING GIN (a);"
BTREEGIST_EXTENSION_STATEMENT = "CREATE TABLE btree_gist_test (a int4);CREATE INDEX btreegistidx ON btree_gist_test USING GIST (a);"
CUBE_EXTENSION_STATEMENT = "SELECT cube_inter('(0,-1),(1,1)', '(-2),(2)');"
DICTINT_EXTENSION_STATEMENT = "SELECT ts_lexize('intdict', '12345678');"
DICTXSYN_EXTENSION_STATEMENT = "SELECT ts_lexize('xsyn', 'word');"
EARTHDISTANCE_EXTENSION_STATEMENT = "SELECT earth_distance(ll_to_earth(-81.3927381, 30.2918842),ll_to_earth(-87.6473133, 41.8853881));"
FUZZYSTRMATCH_EXTENSION_STATEMENT = "SELECT soundex('hello world!');"
INTARRAY_EXTENSION_STATEMENT = "CREATE TABLE intarray_test (mid INT PRIMARY KEY, sections INT[]);SELECT intarray_test.mid FROM intarray_test WHERE intarray_test.sections @> '{1,2}';"
ISN_EXTENSION_STATEMENT = "SELECT isbn('978-0-393-04002-9');"
LO_EXTENSION_STATEMENT = "CREATE TABLE lo_test (value lo);"
LTREE_EXTENSION_STATEMENT = "CREATE TABLE ltree_test (path ltree);"
OLD_SNAPSHOT_EXTENSION_STATEMENT = "SELECT * from pg_old_snapshot_time_mapping();"
PG_FREESPACEMAP_EXTENSION_STATEMENT = (
    "CREATE TABLE pg_freespacemap_test (i int);SELECT * FROM pg_freespace('pg_freespacemap_test');"
)
PGROWLOCKS_EXTENSION_STATEMENT = (
    "CREATE TABLE pgrowlocks_test (i int);SELECT * FROM pgrowlocks('pgrowlocks_test');"
)
PGSTATTUPLE_EXTENSION_STATEMENT = "SELECT * FROM pgstattuple('pg_catalog.pg_proc');"
PG_VISIBILITY_EXTENSION_STATEMENT = "CREATE TABLE pg_visibility_test (i int);SELECT * FROM pg_visibility('pg_visibility_test'::regclass);"
SEG_EXTENSION_STATEMENT = "SELECT '10(+-)1'::seg as seg;"
TABLEFUNC_EXTENSION_STATEMENT = "SELECT * FROM normal_rand(1000, 5, 3);"
TCN_EXTENSION_STATEMENT = "CREATE TABLE tcn_test (i int);CREATE TRIGGER tcn_test_idx AFTER INSERT OR UPDATE OR DELETE ON tcn_test FOR EACH ROW EXECUTE FUNCTION TRIGGERED_CHANGE_NOTIFICATION();"
TSM_SYSTEM_ROWS_EXTENSION_STATEMENT = "CREATE TABLE tsm_system_rows_test (i int);SELECT * FROM tsm_system_rows_test TABLESAMPLE SYSTEM_ROWS(100);"
TSM_SYSTEM_TIME_EXTENSION_STATEMENT = "CREATE TABLE tsm_system_time_test (i int);SELECT * FROM tsm_system_time_test TABLESAMPLE SYSTEM_TIME(1000);"
UUID_OSSP_EXTENSION_STATEMENT = "SELECT uuid_nil();"


@pytest.mark.abort_on_fail
async def test_plugins(ops_test: OpsTest) -> None:
    """Build and deploy one unit of PostgreSQL and then test the available plugins."""
    # Build and deploy the PostgreSQL charm.
    async with ops_test.fast_forward():
        await build_and_deploy(ops_test, 2)

    # Check that the available plugins are disabled.
    primary = await get_primary(ops_test)
    password = await get_password(ops_test)
    address = await get_unit_address(ops_test, primary)
    logger.info("checking that the plugins are disabled")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True

        # Test citext extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(CITEXT_EXTENSION_STATEMENT)

        # Test debversion extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(DEBVERSION_EXTENSION_STATEMENT)

        # Test hstore extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(HSTORE_EXTENSION_STATEMENT)

        # Test pg_trgm extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(PG_TRGM_EXTENSION_STATEMENT)

        # Test PL/Python extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(PLPYTHON3U_EXTENSION_STATEMENT)

        # Test unaccent extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(UNACCENT_EXTENSION_STATEMENT)

        # Test bloom extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(BLOOM_EXTENSION_STATEMENT)

        # Test btree_gin extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(BTREEGIN_EXTENSION_STATEMENT)

        # Test btree_gist extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(BTREEGIST_EXTENSION_STATEMENT)

        # Test cube extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(CUBE_EXTENSION_STATEMENT)

        # Test dict_int extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(DICTINT_EXTENSION_STATEMENT)

        # Test dict_xsyn extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(DICTXSYN_EXTENSION_STATEMENT)

        # Test earthdistance extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(EARTHDISTANCE_EXTENSION_STATEMENT)

        # Test fuzzystrmatch extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(FUZZYSTRMATCH_EXTENSION_STATEMENT)

        # Test intarray extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(INTARRAY_EXTENSION_STATEMENT)

        # Test isn extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(ISN_EXTENSION_STATEMENT)

        # Test lo extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(LO_EXTENSION_STATEMENT)

        # Test ltree extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(LTREE_EXTENSION_STATEMENT)

        # Test old_snapshot extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(OLD_SNAPSHOT_EXTENSION_STATEMENT)

        # Test pg_freespacemap extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(PG_FREESPACEMAP_EXTENSION_STATEMENT)

        # Test pgrowlocks extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(PGROWLOCKS_EXTENSION_STATEMENT)

        # Test pgstattuple extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(PGSTATTUPLE_EXTENSION_STATEMENT)

        # Test pg_visibility extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(PG_VISIBILITY_EXTENSION_STATEMENT)

        # Test seg extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(SEG_EXTENSION_STATEMENT)

        # Test tablefunc extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(TABLEFUNC_EXTENSION_STATEMENT)

        # Test tcn extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(TCN_EXTENSION_STATEMENT)

        # Test tsm_system_rows extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(TSM_SYSTEM_ROWS_EXTENSION_STATEMENT)

        # Test tsm_system_time extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(TSM_SYSTEM_TIME_EXTENSION_STATEMENT)

        # Test uuid_ossp extension disabled.
        with pytest.raises(psycopg2.Error):
            connection.cursor().execute(UUID_OSSP_EXTENSION_STATEMENT)
    connection.close()

    # Enable the plugins.
    logger.info("enabling the plugins")
    config = {
        "plugin_citext_enable": "True",
        "plugin_debversion_enable": "True",
        "plugin_hstore_enable": "True",
        "plugin_pg_trgm_enable": "True",
        "plugin_plpython3u_enable": "True",
        "plugin_unaccent_enable": "True",
        "plugin_bloom_enable": "True",
        "plugin_btree_gin_enable": "True",
        "plugin_btree_gist_enable": "True",
        "plugin_cube_enable": "True",
        "plugin_dict_int_enable": "True",
        "plugin_dict_xsyn_enable": "True",
        "plugin_earthdistance_enable": "True",
        "plugin_fuzzystrmatch_enable": "True",
        "plugin_intarray_enable": "True",
        "plugin_isn_enable": "True",
        "plugin_lo_enable": "True",
        "plugin_ltree_enable": "True",
        "plugin_old_snapshot_enable": "True",
        "plugin_pg_freespacemap_enable": "True",
        "plugin_pgrowlocks_enable": "True",
        "plugin_pgstattuple_enable": "True",
        "plugin_pg_visibility_enable": "True",
        "plugin_seg_enable": "True",
        "plugin_tablefunc_enable": "True",
        "plugin_tcn_enable": "True",
        "plugin_tsm_system_rows_enable": "True",
        "plugin_tsm_system_time_enable": "True",
        "plugin_uuid_ossp_enable": "True",
    }
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    # Check that the available plugins are enabled.
    logger.info("checking that the plugins are enabled")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True

        # Test citext extension enabled.
        connection.cursor().execute(CITEXT_EXTENSION_STATEMENT)

        # Test debversion extension enabled.
        connection.cursor().execute(DEBVERSION_EXTENSION_STATEMENT)

        # Test hstore extension enabled.
        connection.cursor().execute(HSTORE_EXTENSION_STATEMENT)

        # Test pg_trgm extension enabled.
        connection.cursor().execute(PG_TRGM_EXTENSION_STATEMENT)

        # Test PL/Python extension enabled.
        connection.cursor().execute(PLPYTHON3U_EXTENSION_STATEMENT)

        # Test unaccent extension enabled.
        connection.cursor().execute(UNACCENT_EXTENSION_STATEMENT)

        # Test bloom extension enabled.
        connection.cursor().execute(BLOOM_EXTENSION_STATEMENT)

        # Test btree_gin extension enabled.
        connection.cursor().execute(BTREEGIN_EXTENSION_STATEMENT)

        # Test btree_gist extension enabled.
        connection.cursor().execute(BTREEGIST_EXTENSION_STATEMENT)

        # Test cube extension enabled.
        connection.cursor().execute(CUBE_EXTENSION_STATEMENT)

        # Test dict_int extension enabled.
        connection.cursor().execute(DICTINT_EXTENSION_STATEMENT)

        # Test dict_xsyn extension enabled.
        connection.cursor().execute(DICTXSYN_EXTENSION_STATEMENT)

        # Test earthdistance extension enabled.
        connection.cursor().execute(EARTHDISTANCE_EXTENSION_STATEMENT)

        # Test fuzzystrmatch extension enabled.
        connection.cursor().execute(FUZZYSTRMATCH_EXTENSION_STATEMENT)

        # Test intarray extension enabled.
        connection.cursor().execute(INTARRAY_EXTENSION_STATEMENT)

        # Test isn extension enabled.
        connection.cursor().execute(ISN_EXTENSION_STATEMENT)

        # Test lo extension enabled.
        connection.cursor().execute(LO_EXTENSION_STATEMENT)

        # Test ltree extension enabled.
        connection.cursor().execute(LTREE_EXTENSION_STATEMENT)

        # Test old_snapshot extension enabled.
        connection.cursor().execute(OLD_SNAPSHOT_EXTENSION_STATEMENT)

        # Test pg_freespacemap extension enabled.
        connection.cursor().execute(PG_FREESPACEMAP_EXTENSION_STATEMENT)

        # Test pgrowlocks extension enabled.
        connection.cursor().execute(PGROWLOCKS_EXTENSION_STATEMENT)

        # Test pgstattuple extension enabled.
        connection.cursor().execute(PGSTATTUPLE_EXTENSION_STATEMENT)

        # Test pg_visibility extension enabled.
        connection.cursor().execute(PG_VISIBILITY_EXTENSION_STATEMENT)

        # Test seg extension enabled.
        connection.cursor().execute(SEG_EXTENSION_STATEMENT)

        # Test tablefunc extension enabled.
        connection.cursor().execute(TABLEFUNC_EXTENSION_STATEMENT)

        # Test tcn extension enabled.
        connection.cursor().execute(TCN_EXTENSION_STATEMENT)

        # Test tsm_system_rows extension enabled.
        connection.cursor().execute(TSM_SYSTEM_ROWS_EXTENSION_STATEMENT)

        # Test tsm_system_time extension enabled.
        connection.cursor().execute(TSM_SYSTEM_TIME_EXTENSION_STATEMENT)

        # Test uuid_ossp extension enabled.
        connection.cursor().execute(UUID_OSSP_EXTENSION_STATEMENT)
    connection.close()
