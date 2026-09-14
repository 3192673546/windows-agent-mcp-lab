"""Regression checks for fast observation and safe post-action state delivery."""
import io
import json
import subprocess
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import server as s

checks = []
def passed(name):
    checks.append(name)
    print('PASS ' + name, flush=True)

target = subprocess.Popen(['pwsh.exe', '-NoLogo', '-NoProfile', '-File',
    str(Path(__file__).with_name('test_target.ps1'))], stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL, creationflags=s.CREATE_NO_WINDOW)
s.LAB_PROCESS_IDS.add(target.pid)
window = None
try:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        s._invalidate_window_list_cache()
        window = next((w for w in s.list_windows() if w['title'] == 'Computer Use MCP Lab Target'), None)
        if window:
            break
        time.sleep(.1)
    assert window, 'No disposable test window'

    with patch.object(s, 'run_uia', wraps=s.run_uia) as uia:
        shot = s.call_tool('get_window_state', {'window': window, 'include_text': False, 'include_screenshot': True})
        operations = [c.args[0] for c in uia.call_args_list]
        assert 'tree' not in operations, operations
        assert set(operations) == {'window', 'focused', 'screenshot'}, operations
        assert shot['screenshots'] and shot['accessibility'] is None
        assert shot['observation']['treeTruncated'] is None
    passed('screenshot-only skips tree but keeps window/focus validation')

    state = s.call_tool('get_window_state', {'window': window, 'max_elements': 1})
    assert state['observation']['treeTruncated'] is True, state['observation']
    assert any('truncated' in x for x in state['warnings'])
    state = s.get_window_state(window, max_elements=500, include_screenshot=True)
    assert state['observation']['treeTruncated'] is False
    assert state['observation']['treeIncomplete'] is False
    passed('real UIA exposes truncated and complete observations')

    def element(state, aid):
        return next(e for e in state['accessibility']['elements'] if e['automationId'] == aid)
    def rpc(name, args):
        output = io.StringIO()
        with redirect_stdout(output):
            s.handle({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                'params': {'name': name, 'arguments': args}})
        result = json.loads(output.getvalue())['result']
        assert not result.get('isError'), result
        return result

    result = rpc('set_value', {'window': window, 'element_index': element(state, 'entry')['index'], 'value': '自动验证🙂'})
    state = result['structuredContent']['state']
    assert element(state, 'entry')['value'] == '自动验证🙂'
    assert result['structuredContent']['requiresReobserve'] is False
    assert state['stateId'] != shot['stateId']
    passed('legacy action arguments return actual read-back and fresh state through MCP')
    assert len([c for c in result['content'] if c['type'] == 'image']) == 1
    assert 'data' not in state['screenshots'][0]
    passed('nested screenshot sent once as MCP image without base64 duplication')

    old_token = state['stateId']
    idx = element(state, 'applyButton')['index']
    with patch.object(s, '_dispatch_tool', wraps=s._dispatch_tool) as dispatch:
        for token in [None, 'stale-token']:
            try:
                s.call_tool('click', {'window': window, 'element_index': idx, **({'state_id': token} if token else {})})
            except RuntimeError:
                pass
            else:
                raise AssertionError('Old index or wrong token accepted')
        assert dispatch.call_count == 0
    passed('automatic state rejects reused indices and stale tokens before dispatch')
    result = rpc('click', {'window': window, 'element_index': idx, 'state_id': old_token})
    state = result['structuredContent']['state']
    assert 'Hello, 自动验证🙂' in state['window']['title'], state['window']
    assert state['stateId'] != old_token
    assert result['structuredContent']['verification']['outcomeVerified'] is False
    passed('token-authorized click returns actual application result without overstating success')

    # An unavailable post-action capture must not hide the already executed write.
    with patch.object(s, 'get_window_state', side_effect=RuntimeError('synthetic capture failure')), \
            patch.object(s, '_dispatch_tool', wraps=s._dispatch_tool) as dispatch:
        result = s.call_tool('set_value', {'window': window, 'element_index': element(state, 'entry')['index'],
            'state_id': state['stateId'], 'value': '一次写入'})
        assert result['ok'] and result['requiresReobserve'] and 'synthetic' in result['observationError']
        assert dispatch.call_count == 1
        assert window['id'] not in s.STATE_CACHE
    state = s.get_window_state(window)
    assert element(state, 'entry')['value'] == '一次写入'
    passed('capture failure preserves completed write and never retries')

    result = s.call_tool('set_value', {'window': window, 'element_index': element(state, 'entry')['index'],
        'value': '可关闭回读', 'include_state': False})
    assert result['requiresReobserve'] and 'state' not in result
    assert window['id'] not in s.STATE_CACHE
    passed('opt-out retains explicit observe-act workflow and state invalidation')

    state = s.get_window_state(window)
    with patch.object(s, '_dispatch_tool', wraps=s._dispatch_tool) as dispatch:
        try:
            s.call_tool('set_value', {'window': window, 'element_index': element(state, 'entry')['index'],
                'value': 'must not execute', 'settle_ms': 'invalid'})
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid option accepted')
        assert dispatch.call_count == 0
    passed('invalid observation option rejected before side effects')
    print(json.dumps({'ok': True, 'checks': len(checks), 'passed': checks}, ensure_ascii=False, indent=2))
finally:
    target.terminate()
    try:
        target.wait(timeout=3)
    except subprocess.TimeoutExpired:
        target.kill()
        target.wait(timeout=3)
    s.UIA_WORKER.close()
