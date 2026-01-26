#!/usr/bin/env bash

set -Eeuo pipefail
rm -rf /var/lib/pg/archive/*
rm -rf /var/lib/postgresql/16/main/*
rm -rf /var/lib/pg/logs/*
rm -rf /var/lib/pg/temp/*
