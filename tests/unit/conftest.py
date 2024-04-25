# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import os
from unittest.mock import PropertyMock

import pytest
from ops import JujuVersion
from pytest_mock import MockerFixture


@pytest.fixture(autouse=True)
def juju_has_secrets(mocker: MockerFixture):
    """This fixture will force the usage of secrets whenever run on Juju 3.x.

    NOTE: This is needed, as normally JujuVersion is set to 0.0.0 in tests
    (i.e. not the real juju version)
    """
    if juju_version := os.environ.get("LIBJUJU"):
        juju_version = juju_version[1:].split(".")[0]
    else:
        juju_version = "3"

    if juju_version < "3":
        mocker.patch.object(
            JujuVersion, "has_secrets", new_callable=PropertyMock
        ).return_value = False
        return False
    else:
        mocker.patch.object(
            JujuVersion, "has_secrets", new_callable=PropertyMock
        ).return_value = True
        return True
