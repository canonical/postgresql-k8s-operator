#!/usr/bin/env bash

set -Eeuo pipefail
find /var/lib/pg/archive -mindepth 1 -delete
find /var/lib/pg/data/16/main -mindepth 1 -delete
find /var/lib/pg/logs -mindepth 1 -delete
find /var/lib/pg/temp -mindepth 1 -delete
