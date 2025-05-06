#!/usr/bin/env bash

set -Eeuo pipefail
rm -rf /var/lib/postgresql/archive/*
rm -rf /var/lib/postgresql/data/pgdata/*
rm -rf /var/lib/postgresql/logs/*
rm -rf /var/lib/postgresql/temp/*
