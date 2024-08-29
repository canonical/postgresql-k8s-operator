# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utilities for catching and raising architecture errors."""

import logging
import os
import sys

from ops.charm import CharmBase
from ops.model import BlockedStatus

logger = logging.getLogger(__name__)


class WrongArchitectureWarningCharm(CharmBase):
    """A fake charm class that only signals a wrong architecture deploy."""

    def __init__(self, *args):
        super().__init__(*args)
        self.unit.status = BlockedStatus(
            f"Error: Charm version incompatible with {os.uname().machine} architecture"
        )
        sys.exit(0)


def is_wrong_architecture() -> bool:
    """Checks if charm was deployed on wrong architecture."""
    juju_charm_file = f"{os.environ.get('CHARM_DIR')}/manifest.yaml"
    if not os.path.exists(juju_charm_file):
        logger.error("Cannot check architecture: manifest file not found in %s", juju_charm_file)
        return False

    with open(juju_charm_file, "r") as file:
        ch_platform = file.read()
    hw_arch = os.uname().machine
    if ("amd64" in ch_platform and hw_arch == "x86_64") or (
        "arm64" in ch_platform and hw_arch == "aarch64"
    ):
        logger.info("Architecture matches: %s charm for %s machine", ch_platform, hw_arch)
        return False

    logger.error("Architecture does not match: %s charm for %s machine", ch_platform, hw_arch)
    return True
