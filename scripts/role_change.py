#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess
import sys

from typing import Optional

logger = logging.getLogger(__name__)


class RoleChange(object):

    def __init__(self, cluster_name: Optional[str]) -> None:
        self._cluster_name = cluster_name if cluster_name is not None else 'unknown'

    def on_role_change(self, new_role: str) -> None:
        try:
            self._update_role(new_role)
            logger.info(f"Updated role to {new_role} for cluster {self._cluster_name}")
        except Exception as e:
            logger.warning("Unable to update role for the cluster {0} to {1}: {2}".format(
                self._cluster_name, new_role, str(e)))

    def _update_role(self, role: str) -> None:
        """Update the role."""
        subprocess.run(["/usr/bin/pebble", "notify", "canonical.com/postgresql", "operation=update_role", "data={}".format(role)])


def main():
    logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s', level=logging.INFO)
    logger.warning("Arguments: %s", sys.argv)
    if len(sys.argv) == 4:
        if sys.argv[1] == 'on_role_change':
            logger.warning("Reached 1")
            RoleChange(cluster_name=sys.argv[3]).on_role_change(sys.argv[2])
        else:
            logger.warning("Reached 1.5")
            RoleChange(cluster_name=sys.argv[3]).on_role_change(sys.argv[2])
    else:
        logger.warning("Reached 2")
        sys.exit("Usage: {0} action role name".format(sys.argv[0]))


if __name__ == '__main__':
    main()
