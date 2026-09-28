import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import llm_gate
import run_web_enrichment as core


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_429_shared_backoff_preserves_retry_after_and_releases_slot(self):
        gate = llm_gate.Gate(self.root, core)
        def fail():
            raise core.FetchProblem('api_http_429', retry=True, delay=180)
        with self.assertRaises(core.FetchProblem):
            gate.call(fail)
        state = json.loads((self.root/'state.json').read_text())
        self.assertGreater(state['cooldown_until'], time.time()+175)
        with core.WorkspaceLock(self.root/'slot-0'):
            pass
        # A separate gate instance observes the pause and cannot issue a request.
        other = llm_gate.Gate(self.root, core)
        with patch.object(llm_gate, 'stop_requested', side_effect=[False, True]), \
             patch.object(llm_gate.time, 'sleep'), \
             self.assertRaisesRegex(core.FetchProblem, 'llm_wait_cancelled'):
            other.call(lambda: self.fail('Request issued during cooldown'))

    def test_slots_released_on_invalid_response(self):
        gate = llm_gate.Gate(self.root, core, interval=0)
        with self.assertRaises(ValueError):
            gate.call(lambda: (_ for _ in ()).throw(ValueError('bad JSON')))
        with core.WorkspaceLock(self.root/'slot-0'):
            pass

    def test_api_wrapper_applies_only_to_chat_calls(self):
        with patch.dict(core.os.environ, {'SOLVE_LLM_GATE_DIR': str(self.root)}), \
             patch.object(core, '_api_request_unlimited', return_value={'ok':True}) as request, \
             patch.object(llm_gate.Gate, 'call', side_effect=lambda fn: fn()) as limited:
            core.api_request('https://example.org/chat/completions', {'model':'fixture'})
            core.api_request('https://example.org/models')
            self.assertEqual(limited.call_count, 1)
            self.assertEqual(request.call_count, 2)

    def test_four_processes_obey_two_slots_and_start_spacing(self):
        code = '''
import sys,time,json
from pathlib import Path
import llm_gate,run_web_enrichment as core
root=Path(sys.argv[1]); number=sys.argv[2]
(root/('ready-'+number)).touch()
while not (root/'go').exists():time.sleep(.02)
gate=llm_gate.Gate(root/'gate',core,slots=2,interval=.15)
def request():
    start=time.time();time.sleep(.8)
    (root/(number+'.json')).write_text(json.dumps([start,time.time()]))
gate.call(request)
'''
        processes = []
        try:
            for i in range(4):
                processes.append(subprocess.Popen([sys.executable,'-c',code,str(self.root),str(i)],
                    cwd=Path(core.__file__).parent,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE))
            deadline=time.monotonic()+20
            while len(list(self.root.glob('ready-*')))<4 and time.monotonic()<deadline:
                time.sleep(.05)
            self.assertEqual(len(list(self.root.glob('ready-*'))),4)
            (self.root/'go').touch()
            for process in processes:
                _, error=process.communicate(timeout=20)
                self.assertEqual(process.returncode,0,error.decode(errors='replace'))
            intervals=[json.loads((self.root/f'{i}.json').read_text()) for i in range(4)]
            events=sorted([(start,1) for start,end in intervals]+[(end,-1) for start,end in intervals])
            active=peak=0
            for _,change in events:
                active+=change;peak=max(peak,active)
            self.assertEqual(peak,2)
            starts=sorted(x[0] for x in intervals)
            self.assertTrue(all(b-a>=.14 for a,b in zip(starts,starts[1:])))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill();process.communicate()
