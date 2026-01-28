#!/usr/bin/env bash

set -Eeuo pipefail
find /var/lib/pg/archive -mindepth 1 -delete
find /var/lib/pg/data/16/main -mindepth 1 -delete
find /var/lib/pg/logs/16/main -mindepth 1 -delete
find /var/lib/pg/temp/16/main -mindepth 1 -delete
