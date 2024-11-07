# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import call, patch

from rotate_logs import main


def test_main():
    with patch("subprocess.run") as _run, patch(
        "time.sleep", side_effect=[None, InterruptedError]
    ) as _sleep:
        try:
            main()
        except InterruptedError:
            pass
        assert _run.call_count == 2
        run_call = call(["logrotate", "-f", "/etc/logrotate.d/pgbackrest.logrotate"])
        _run.assert_has_calls([run_call, run_call])
        assert _sleep.call_count == 2
        sleep_call = call(60)
        _sleep.assert_has_calls([sleep_call, sleep_call])
