"""Cross-process LLM slots, request spacing and shared HTTP-429 backoff."""
import json
import os
from pathlib import Path
import time
from contextlib import contextmanager

stop_requested = lambda: False


def request_headers(url, headers, gate):
    """Rotate only explicitly configured Blablador endpoints and primary headers."""
    primary = os.getenv('BLABLADOR_KEY', '')
    secondary = os.getenv('GRAPHRAG_API_KEY2', '')
    allowed = json.loads(os.getenv('SOLVE_BLABLADOR_CHAT_URLS', '[]'))
    if (not primary or not secondary or primary == secondary or url not in allowed
            or (headers or {}).get('Authorization') != 'Bearer ' + primary):
        return headers
    with gate.state() as state:
        index = state.get('key_rotation', 0) % 2
        state['key_rotation'] = 1 - index
    # Persist only the rotating index, never keys, headers or fingerprints.
    return dict(headers, Authorization='Bearer ' + (primary, secondary)[index])


class Gate:
    def __init__(self, root, core, slots=2, interval=2, cooldown=60):
        if slots < 1 or interval < 0 or cooldown < 1:
            raise ValueError('Ungültige LLM-Begrenzung')
        self.root, self.core = Path(root), core
        self.slots, self.interval, self.cooldown = slots, interval, cooldown
        self.root.mkdir(parents=True, exist_ok=True)

    def check_stop(self):
        if stop_requested():
            raise self.core.FetchProblem('llm_wait_cancelled', retry=True)

    @contextmanager
    def state(self):
        lock = self.core.WorkspaceLock(self.root / 'state-lock')
        while True:
            try:
                lock.__enter__()
                break
            except RuntimeError as error:
                if 'bereits bearbeitet' not in str(error):
                    raise
                self.check_stop()
                time.sleep(.1)
        try:
            path = self.root / 'state.json'
            state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            before = dict(state)
            yield state
            if before != state:
                temporary = self.root / 'state.tmp'
                temporary.write_text(json.dumps(state), encoding='utf-8')
                os.replace(temporary, path)
        finally:
            lock.__exit__(None, None, None)

    @contextmanager
    def slot(self):
        chosen = None
        began = time.monotonic()
        reported = False
        try:
            while chosen is None:
                self.check_stop()
                with self.state() as state:
                    wait = max(state.get('cooldown_until', 0), state.get('next_start', 0)) - time.time()
                    if wait <= 0:
                        for number in range(self.slots):
                            lock = self.core.WorkspaceLock(self.root / f'slot-{number}')
                            try:
                                lock.__enter__()
                            except RuntimeError as error:
                                if 'bereits bearbeitet' not in str(error):
                                    raise
                            else:
                                chosen = lock
                                state['next_start'] = time.time() + self.interval
                                break
                if chosen is None:
                    if not reported and time.monotonic() - began >= 2:
                        print('Blablador: warte auf gemeinsamen API-Slot oder gemeinsame Wartezeit.', flush=True)
                        reported = True
                    time.sleep(.5)
            yield
        finally:
            if chosen is not None:
                chosen.__exit__(None, None, None)

    def call(self, request):
        with self.slot():
            try:
                result = request()
            except self.core.FetchProblem as error:
                if str(error) == 'api_http_429':
                    with self.state() as state:
                        strikes = min(state.get('strikes', 0) + 1, 5)
                        delay = max(error.delay, min(900, self.cooldown * 2 ** (strikes - 1)))
                        state['strikes'] = strikes
                        state['cooldown_until'] = max(state.get('cooldown_until', 0), time.time() + delay)
                    print(f'Blablador HTTP 429: gemeinsame Pause mindestens {delay:.0f} Sekunden.', flush=True)
                raise
            else:
                with self.state() as state:
                    # A concurrent successful call must not clear a newer 429 pause.
                    if state.get('cooldown_until', 0) <= time.time():
                        state['strikes'] = 0
                return result
