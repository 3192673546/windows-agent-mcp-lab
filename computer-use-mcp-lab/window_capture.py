"""Bounded, persistent capture worker with explicit compatibility fallback."""
import atexit
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import desktop_input as d

class CaptureWorker:
    def __init__(self):
        self.proc = None
        self.responses = None
        self.lock = threading.RLock()
        self.timer = None
        self.cooldowns = {}

    def close(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            proc, self.proc = self.proc, None
            if proc:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1)
                proc.stdin.close()
                proc.stdout.close()

    def _start(self):
        if self.proc and self.proc.poll() is None:
            return
        self.close()
        worker = Path(__file__).with_name('capture_worker.py')
        deps = worker.with_name('capture_deps')
        if not (deps/'windows_capture').is_dir():
            raise RuntimeError('WGC dependencies missing; install capture-requirements.txt into capture_deps.')
        proc = subprocess.Popen([sys.executable, '-u', str(worker)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        responses = queue.Queue()
        def read():
            try:
                for line in proc.stdout:
                    if len(line) > 8_000_000:
                        responses.put(RuntimeError('Capture response exceeds limit.'))
                        return
                    responses.put(line)
            except (OSError, ValueError):
                pass
            finally:
                responses.put(RuntimeError('Capture worker exited.'))
        threading.Thread(target=read, daemon=True).start()
        self.proc, self.responses = proc, responses

    def capture(self, hwnd, process_id):
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self._start()
            try:
                self.proc.stdin.write(json.dumps({'window_id': hwnd, 'process_id': process_id})+'\n')
                self.proc.stdin.flush()
                value = self.responses.get(timeout=3)
                if isinstance(value, Exception):
                    raise value
                result = json.loads(value)
                if not result.get('ok'):
                    raise RuntimeError(result.get('error', 'WGC capture failed.'))
                return result
            except Exception:
                self.close()
                raise
            finally:
                if self.proc:
                    self.timer = threading.Timer(300, self.close)
                    self.timer.daemon = True
                    self.timer.start()

    def screenshot(self, hwnd, process_id, fallback, backend='auto'):
        if backend not in {'auto', 'wgc', 'legacy'}:
            raise ValueError('capture_backend must be auto, wgc or legacy.')
        if backend == 'legacy':
            return fallback()
        r = d.rect(hwnd)
        key = hwnd, process_id
        left, top, width, height = (d.u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        error = None
        if r['right'] <= left or r['bottom'] <= top or r['left'] >= left+width or r['top'] >= top+height:
            error = 'Target is entirely off-screen; WGC may not produce a frame.'
        elif backend == 'auto' and key in self.cooldowns:
            until, message = self.cooldowns[key]
            if time.monotonic() < until:
                error = message
        if error is None:
            try:
                result = self.capture(hwnd, process_id)
                self.cooldowns.pop(key, None)
                return result
            except Exception as exc:
                error = str(exc)
                self.cooldowns[key] = time.monotonic()+30, error
                if len(self.cooldowns) > 16:
                    self.cooldowns.pop(next(iter(self.cooldowns)))
        if backend == 'wgc':
            raise RuntimeError(error)
        result = fallback()
        result['captureFallbackReason'] = error
        return result

CAPTURE_WORKER = CaptureWorker()
atexit.register(CAPTURE_WORKER.close)
