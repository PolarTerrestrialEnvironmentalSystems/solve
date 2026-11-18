"""Coordinator retry tests with simulated processes and clock; no API requests."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import parallel_crawler as parallel
from test_collector import config


class ApiResumeTests(unittest.TestCase):
    def simulate(self, reasons, *, auto=True, interrupt=False, stale=False, exit_code=0):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg = config(root)
            target = root / 'parallel'
            parallel.prepare(target, cfg, 1)
            path = target / 'group-01/last_run.json'
            if stale:
                parallel.write_json(path, {'stop_reason': 'review_api_errors'})
            calls, clock = [], [0.0]

            class Process:
                pid = 42

                def poll(self):
                    return exit_code

            def spawn(*args, **kwargs):
                calls.append(args)
                if not stale:
                    reason = reasons[len(calls) - 1]
                    parallel.write_json(path, {'stop_reason': reason, 'pending_tasks': 3})
                    os.utime(path, ns=(10**18 + len(calls), 10**18 + len(calls)))
                return Process()

            def sleep(seconds):
                if interrupt:
                    raise KeyboardInterrupt
                clock[0] += seconds

            with patch.object(parallel.subprocess, 'Popen', side_effect=spawn), \
                 patch.object(parallel.time, 'monotonic', side_effect=lambda: clock[0]), \
                 patch.object(parallel.time, 'sleep', side_effect=sleep), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = parallel.run(target, 1, auto_retry_api=auto)
            report = json.loads((target / 'parallel_last_run.json').read_text())
            state = json.loads((target / 'parallel_retry_state.json').read_text())
            return code, len(calls), clock[0], report, state

    def test_retry_both_api_stops_then_finish(self):
        code, calls, elapsed, report, state = self.simulate(
            ['review_api_errors', 'preflight_api_errors', 'no_due_tasks'])
        self.assertEqual((code, calls, elapsed), (0, 3, 180))
        self.assertEqual(report['api_restarts'], {'group-01': 2})
        self.assertEqual(state['group-01']['state'], 'finished')

    def test_interrupt_cancels_scheduled_restart(self):
        code, calls, _, report, state = self.simulate(['review_api_errors'], interrupt=True)
        self.assertEqual((code, calls), (130, 1))
        self.assertTrue(report['interrupted'])
        self.assertEqual(state['group-01']['state'], 'cancelled')

    def test_stale_summary_does_not_restart_crashed_or_unreported_worker(self):
        for code in (0, 1):
            with self.subTest(code=code):
                result, calls, _, report, _ = self.simulate([], stale=True, exit_code=code)
                self.assertEqual((result, calls), (code, 1))
                self.assertEqual(report['api_restarts'], {})

    def test_opt_out_and_other_stop_reasons(self):
        for reason, auto in [('review_api_errors', False), ('run_time_budget', True),
                             ('no_due_tasks', True), ('interrupted', True)]:
            with self.subTest(reason=reason):
                _, calls, _, report, _ = self.simulate([reason], auto=auto)
                self.assertEqual(calls, 1)
                self.assertEqual(report['api_restarts'], {})

    def test_backoff_is_capped(self):
        self.assertEqual([parallel.api_retry_delay(i) for i in range(1, 8)],
                         [60, 120, 240, 480, 900, 900, 900])


if __name__ == '__main__':
    unittest.main()
