# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
from lightkube.core.exceptions import ApiError


class _FakeResponse:
    """Used to fake an httpx response during testing only."""

    def __init__(self, status_code: int):
        self.status_code = status_code

    def json(self):
        return {
            "apiVersion": 1,
            "code": self.status_code,
            "message": "broken",
            "reason": "",
        }


class _FakeApiError(ApiError):
    """Used to simulate an ApiError during testing."""

    def __init__(self, status_code: int = 400):
        super().__init__(response=_FakeResponse(status_code))
