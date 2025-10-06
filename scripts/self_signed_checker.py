# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Self signed cert call checker."""

import os
from ssl import create_default_context
from urllib.request import urlopen

context = create_default_context()
context.load_verify_locations(cafile="/var/lib/postgresql/data/peer_ca.pem")
# Endpoint is set by the charm
with urlopen(os.environ["ENDPOINT"], context=context) as response:  # noqa: S310
    # We want assert to exit the interpreter with an error
    assert 200 >= response.status < 300  # noqa: S101
