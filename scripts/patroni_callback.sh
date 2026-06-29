#!/bin/bash
# Patroni callback script — sends Pebble notice on role changes.
# Invoked by Patroni with: action=$1 role=$2 cluster_name=$3
# E.g.: ./patroni-callback.sh on_role_change master my-cluster
#
# TODO: Move this script into the charmed-postgresql rock image so it's
# available at a known path without needing the charm to push it.
#
# Stderr from this script flows to the Patroni/Pebble service logs,
# which are forwarded to Loki and visible in `juju debug-log`.

set -euo pipefail

ACTION="${1:-}"
ROLE="${2:-}"
CLUSTER_NAME="${3:-}"

log() {
    echo "[patroni-callback] $(date -Iseconds) ${*}" >&2
}

# Only send notices for role-relevant actions.
case "${ACTION}" in
    on_role_change|on_start|on_stop)
        log "action=${ACTION} role=${ROLE} cluster=${CLUSTER_NAME}"
        if /charm/bin/pebble notify \
            "canonical.com/postgresql-k8s/role-change" \
            "role=${ROLE}" \
            --repeat-after=30s; then
            log "pebble notify succeeded"
        else
            log "ERROR: pebble notify failed (exit code $?)"
        fi
        ;;
    *)
        log "ignoring unsupported action=${ACTION}"
        ;;
esac

exit 0
