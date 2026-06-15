# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path
from typing import get_args

from single_kernel_postgresql.config.locales import K8S_LOCALES


def test_locales_fixture_matches_library() -> None:
    """The integration locale fixture must mirror the library's K8S_LOCALES.

    The K8s rock provides the shared locales plus C.utf8 and POSIX, so this
    charm pins K8S_LOCALES. The integration tests cannot import the library (it
    is absent from their dependency group), so they assert against this
    committed snapshot instead; this guard fails CI if it drifts. Regenerate it
    from the repo root after a library locale change:
        python -c "from typing import get_args; \
from single_kernel_postgresql.config.locales import K8S_LOCALES; \
print(chr(10).join(sorted(get_args(K8S_LOCALES))))" > tests/integration/locales.txt
    """
    fixture = (Path(__file__).parents[1] / "integration" / "locales.txt").read_text().splitlines()
    assert fixture == sorted(get_args(K8S_LOCALES))
