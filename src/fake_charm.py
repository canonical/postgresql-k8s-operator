#!/usr/bin/env -S LD_LIBRARY_PATH=lib python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fake charm used for catching and raising architecture errors."""

import os
import sys

import yaml
from ops.charm import CharmBase
from ops.main import main
from ops.model import BlockedStatus


class WrongArchitectureWarningCharm(CharmBase):
    """A fake charm class that only signals a wrong architecture deploy."""

    def __init__(self, *args):
        super().__init__(*args)
        self.unit.status = BlockedStatus(
            f"Error: Charm version incompatible with {os.uname().machine} architecture"
        )
        sys.exit(0)


def block_on_wrong_architecture() -> None:
    """Checks if charm architecture is compatible with underlying hardware.

    If not, the fake charm will get deployed instead, warning the user.
    """
    manifest_path = f"{os.environ.get('CHARM_DIR')}/manifest.yaml"
    if not os.path.exists(manifest_path):
        return
    with open(manifest_path, "r") as manifest:
        charm_arch = yaml.safe_load(manifest)["bases"][0]["architectures"][0]
    hw_arch = os.uname().machine

    if (charm_arch == "amd64" and hw_arch != "x86_64") or (
        charm_arch == "arm64" and hw_arch != "aarch64"
    ):
        main(WrongArchitectureWarningCharm, use_juju_for_storage=True)
