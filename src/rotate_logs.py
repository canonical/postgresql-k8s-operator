# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Custom event for rotating logs."""

import logging
import subprocess
from time import sleep

logger = logging.getLogger(__name__)


def main():
    """Main loop that calls logrotate."""
    while True:
        subprocess.run(["logrotate", "-f", "/etc/logrotate.d/pgbackrest.logrotate"])

        # Wait 60 seconds before executing logrotate again.
        sleep(60)


if __name__ == "__main__":
    main()
