#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import psycopg2 as psycopg2
import pytest as pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
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
REFINT_EXTENSION_STATEMENT = "CREATE TABLE A (ID int4 not null); CREATE UNIQUE INDEX AI ON A (ID);CREATE TABLE B (REFB int4);CREATE INDEX BI ON B (REFB);CREATE TRIGGER BT BEFORE INSERT OR UPDATE ON B FOR EACH ROW EXECUTE PROCEDURE check_primary_key ('REFB', 'A', 'ID');"
AUTOINC_EXTENSION_STATEMENT = "CREATE TABLE ids (id int4, idesc text);CREATE TRIGGER ids_nextid BEFORE INSERT OR UPDATE ON ids FOR EACH ROW EXECUTE PROCEDURE autoinc (id, next_id);"
INSERT_USERNAME_EXTENSION_STATEMENT = "CREATE TABLE username_test (name text, username text not null);CREATE TRIGGER insert_usernames BEFORE INSERT OR UPDATE ON username_test FOR EACH ROW EXECUTE PROCEDURE insert_username (username);"
MODDATETIME_EXTENSION_STATEMENT = "CREATE TABLE mdt (moddate timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL);CREATE TRIGGER mdt_moddatetime BEFORE UPDATE ON mdt FOR EACH ROW EXECUTE PROCEDURE moddatetime (moddate);"
BOOL_PLPERL_EXTENSION_STATEMENT = "CREATE FUNCTION hello_bool(bool) RETURNS TEXT TRANSFORM FOR TYPE bool LANGUAGE plperl AS $$ my $with_world = shift; return sprintf('hello%s', $with_world ? ' world' : ''); $$;"
HLL_EXTENSION_STATEMENT = "CREATE TABLE hll_test (users hll);"
HYPOPG_EXTENSION_STATEMENT = "CREATE TABLE hypopg_test (id integer, val text); SELECT hypopg_create_index('CREATE INDEX ON hypopg_test (id)');"
IP4R_EXTENSION_STATEMENT = "CREATE TABLE ip4r_test (ip ip4);"
JSONB_PLPERL_EXTENSION_STATEMENT = "CREATE OR REPLACE FUNCTION jsonb_plperl_test(val jsonb) RETURNS jsonb TRANSFORM FOR TYPE jsonb LANGUAGE plperl as $$ return $_[0]; $$;"
ORAFCE_EXTENSION_STATEMENT = "SELECT oracle.add_months(date '2005-05-31',1);"
PG_SIMILARITY_EXTENSION_STATEMENT = (
    "SET pg_similarity.levenshtein_threshold = 0.7; SELECT 'aaa', 'aab', lev('aaa','aab');"
)
PLPERL_EXTENSION_STATEMENT = "CREATE OR REPLACE FUNCTION plperl_test(name text) RETURNS text AS $$ return $_SHARED{$_[0]}; $$ LANGUAGE plperl;"
PREFIX_EXTENSION_STATEMENT = "SELECT '123'::prefix_range @> '123456';"
RDKIT_EXTENSION_STATEMENT = "SELECT is_valid_smiles('CCC');"
TDS_FDW_EXTENSION_STATEMENT = "CREATE SERVER mssql_svr FOREIGN DATA WRAPPER tds_fdw OPTIONS (servername 'tds_fdw_test', port '3306', database 'tds_fdw_test', tds_version '7.1');"
ICU_EXT_EXTENSION_STATEMENT = (
    'CREATE COLLATION "vat-lat" (provider = icu, locale = "la-VA-u-kn-true")'
)
PLTCL_EXTENSION_STATEMENT = (
    "CREATE FUNCTION pltcl_test(integer) RETURNS integer AS $$ return $1 $$ LANGUAGE pltcl STRICT;"
)
POSTGIS_EXTENSION_STATEMENT = "SELECT PostGIS_Full_Version();"
ADDRESS_STANDARDIZER_EXTENSION_STATEMENT = "SELECT num, street, city, zip, zipplus FROM parse_address('1 Devonshire Place, Boston, MA 02109-1234');"
ADDRESS_STANDARDIZER_DATA_US_EXTENSION_STATEMENT = "SELECT house_num, name, suftype, city, country, state, unit  FROM standardize_address('us_lex', 'us_gaz', 'us_rules', 'One Devonshire Place, PH 301, Boston, MA 02109');"
POSTGIS_TIGER_GEOCODER_EXTENSION_STATEMENT = "SELECT *  FROM standardize_address('tiger.pagc_lex', 'tiger.pagc_gaz', 'tiger.pagc_rules', 'One Devonshire Place, PH 301, Boston, MA 02109-1234');"
POSTGIS_TOPOLOGY_EXTENSION_STATEMENT = "SELECT topology.CreateTopology('nyc_topo', 26918, 0.5);"
POSTGIS_RASTER_EXTENSION_STATEMENT = (
    "CREATE TABLE test_postgis_raster (name varchar, rast raster);"
)
VECTOR_EXTENSION_STATEMENT = (
    "CREATE TABLE vector_test (id bigserial PRIMARY KEY, embedding vector(3));"
)
TIMESCALEDB_EXTENSION_STATEMENT = "CREATE TABLE test_timescaledb (time TIMESTAMPTZ NOT NULL); SELECT create_hypertable('test_timescaledb', 'time');"


@pytest.mark.abort_on_fail
async def test_plugins(ops_test: OpsTest, charm) -> None:
    """Build and deploy one unit of PostgreSQL and then test the available plugins."""
    # Build and deploy the PostgreSQL charm.
    async with ops_test.fast_forward():
        # TODO Figure out how to deal with pgaudit
        await build_and_deploy(ops_test, charm, 2)

    sql_tests = {
        "plugin-citext-enable": CITEXT_EXTENSION_STATEMENT,
        "plugin-debversion-enable": DEBVERSION_EXTENSION_STATEMENT,
        "plugin-hstore-enable": HSTORE_EXTENSION_STATEMENT,
        "plugin-pg-trgm-enable": PG_TRGM_EXTENSION_STATEMENT,
        "plugin-plpython3u-enable": PLPYTHON3U_EXTENSION_STATEMENT,
        "plugin-unaccent-enable": UNACCENT_EXTENSION_STATEMENT,
        "plugin-bloom-enable": BLOOM_EXTENSION_STATEMENT,
        "plugin-btree-gin-enable": BTREEGIN_EXTENSION_STATEMENT,
        "plugin-btree-gist-enable": BTREEGIST_EXTENSION_STATEMENT,
        "plugin-cube-enable": CUBE_EXTENSION_STATEMENT,
        "plugin-dict-int-enable": DICTINT_EXTENSION_STATEMENT,
        "plugin-dict-xsyn-enable": DICTXSYN_EXTENSION_STATEMENT,
        "plugin-earthdistance-enable": EARTHDISTANCE_EXTENSION_STATEMENT,
        "plugin-fuzzystrmatch-enable": FUZZYSTRMATCH_EXTENSION_STATEMENT,
        "plugin-intarray-enable": INTARRAY_EXTENSION_STATEMENT,
        "plugin-isn-enable": ISN_EXTENSION_STATEMENT,
        "plugin-lo-enable": LO_EXTENSION_STATEMENT,
        "plugin-ltree-enable": LTREE_EXTENSION_STATEMENT,
        "plugin-old-snapshot-enable": OLD_SNAPSHOT_EXTENSION_STATEMENT,
        "plugin-pg-freespacemap-enable": PG_FREESPACEMAP_EXTENSION_STATEMENT,
        "plugin-pgrowlocks-enable": PGROWLOCKS_EXTENSION_STATEMENT,
        "plugin-pgstattuple-enable": PGSTATTUPLE_EXTENSION_STATEMENT,
        "plugin-pg-visibility-enable": PG_VISIBILITY_EXTENSION_STATEMENT,
        "plugin-seg-enable": SEG_EXTENSION_STATEMENT,
        "plugin-tablefunc-enable": TABLEFUNC_EXTENSION_STATEMENT,
        "plugin-tcn-enable": TCN_EXTENSION_STATEMENT,
        "plugin-tsm-system-rows-enable": TSM_SYSTEM_ROWS_EXTENSION_STATEMENT,
        "plugin-tsm-system-time-enable": TSM_SYSTEM_TIME_EXTENSION_STATEMENT,
        "plugin-uuid-ossp-enable": UUID_OSSP_EXTENSION_STATEMENT,
        "plugin-spi-enable": [
            REFINT_EXTENSION_STATEMENT,
            AUTOINC_EXTENSION_STATEMENT,
            INSERT_USERNAME_EXTENSION_STATEMENT,
            MODDATETIME_EXTENSION_STATEMENT,
        ],
        "plugin-bool-plperl-enable": BOOL_PLPERL_EXTENSION_STATEMENT,
        "plugin-hll-enable": HLL_EXTENSION_STATEMENT,
        "plugin-postgis-enable": POSTGIS_EXTENSION_STATEMENT,
        "plugin-hypopg-enable": HYPOPG_EXTENSION_STATEMENT,
        "plugin-ip4r-enable": IP4R_EXTENSION_STATEMENT,
        "plugin-plperl-enable": PLPERL_EXTENSION_STATEMENT,
        "plugin-jsonb-plperl-enable": JSONB_PLPERL_EXTENSION_STATEMENT,
        "plugin-orafce-enable": ORAFCE_EXTENSION_STATEMENT,
        "plugin-pg-similarity-enable": ORAFCE_EXTENSION_STATEMENT,
        "plugin-prefix-enable": PREFIX_EXTENSION_STATEMENT,
        "plugin-rdkit-enable": RDKIT_EXTENSION_STATEMENT,
        "plugin-tds-fdw-enable": TDS_FDW_EXTENSION_STATEMENT,
        "plugin-icu-ext-enable": ICU_EXT_EXTENSION_STATEMENT,
        "plugin-pltcl-enable": PLTCL_EXTENSION_STATEMENT,
        "plugin-address-standardizer-enable": ADDRESS_STANDARDIZER_EXTENSION_STATEMENT,
        "plugin-address-standardizer-data-us-enable": ADDRESS_STANDARDIZER_DATA_US_EXTENSION_STATEMENT,
        "plugin-postgis-tiger-geocoder-enable": POSTGIS_TIGER_GEOCODER_EXTENSION_STATEMENT,
        "plugin-postgis-raster-enable": POSTGIS_RASTER_EXTENSION_STATEMENT,
        "plugin-postgis-topology-enable": POSTGIS_TOPOLOGY_EXTENSION_STATEMENT,
        "plugin-vector-enable": VECTOR_EXTENSION_STATEMENT,
        "plugin-timescaledb-enable": TIMESCALEDB_EXTENSION_STATEMENT,
    }

    def enable_disable_config(enabled: False):
        config = {}
        for plugin in sql_tests:
            config[plugin] = f"{enabled}"
        return config

    # Check that the available plugins are disabled.
    primary = await get_primary(ops_test)
    password = await get_password(ops_test)
    address = await get_unit_address(ops_test, primary)

    config = enable_disable_config(False)
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    logger.info("checking that the plugins are disabled")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        for query in sql_tests.values():
            if isinstance(query, list):
                for test in query:
                    with pytest.raises(psycopg2.Error):
                        connection.cursor().execute(test)
            else:
                with pytest.raises(psycopg2.Error):
                    connection.cursor().execute(query)
    connection.close()

    # Enable the plugins.
    logger.info("enabling the plugins")

    config = enable_disable_config(True)
    await ops_test.model.applications[DATABASE_APP_NAME].set_config(config)
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    # Check that the available plugins are enabled.
    logger.info("checking that the plugins are enabled")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        for query in sql_tests.values():
            if isinstance(query, list):
                for test in query:
                    connection.cursor().execute(test)
            else:
                connection.cursor().execute(query)
    connection.close()


@pytest.mark.abort_on_fail
async def test_plugin_objects(ops_test: OpsTest) -> None:
    """Checks if charm gets blocked when trying to disable a plugin in use."""
    primary = await get_primary(ops_test)
    password = await get_password(ops_test)
    address = await get_unit_address(ops_test, primary)

    logger.info("Creating an index object which depends on the pg_trgm config")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("CREATE TABLE example (value VARCHAR(10));")
        connection.cursor().execute(
            "CREATE INDEX example_idx ON example USING gin(value gin_trgm_ops);"
        )
    connection.close()

    logger.info("Disabling the plugin on charm config, waiting for blocked status")
    await ops_test.model.applications[DATABASE_APP_NAME].set_config({
        "plugin-pg-trgm-enable": "False"
    })
    await ops_test.model.block_until(
        lambda: ops_test.model.units[primary].workload_status == "blocked",
        timeout=100,
    )

    logger.info("Enabling the plugin back on charm config, status should resolve")
    await ops_test.model.applications[DATABASE_APP_NAME].set_config({
        "plugin-pg-trgm-enable": "True"
    })
    await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")

    logger.info("Re-disabling plugin, waiting for blocked status to come back")
    await ops_test.model.applications[DATABASE_APP_NAME].set_config({
        "plugin-pg-trgm-enable": "False"
    })
    await ops_test.model.block_until(
        lambda: ops_test.model.units[primary].workload_status == "blocked",
        timeout=100,
    )

    logger.info("Delete the object which depends on the plugin")
    with db_connect(host=address, password=password) as connection:
        connection.autocommit = True
        connection.cursor().execute("DROP INDEX example_idx;")
    connection.close()

    logger.info("Waiting for status to resolve again")
    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(apps=[DATABASE_APP_NAME], status="active")
