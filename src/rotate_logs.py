# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Service for rotating logs."""

import subprocess
import time


def main():
    """Main loop that calls logrotate."""
    while True:
        # Command is hardcoded
        subprocess.run(["/usr/sbin/logrotate", "-f", "/etc/logrotate.d/pgbackrest.logrotate"])  # noqa: S603

        # Wait 60 seconds before executing logrotate again.
        time.sleep(60)


if __name__ == "__main__":
    main()
