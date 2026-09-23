"""Regression tests: temporary files/processes only, never the caller's projects."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import server as m

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable
CODEX = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/OpenAI/Codex/bin/codex.exe'


def ps_python(code):
    return "& '" + PYTHON.replace("'", "''") + "' -u -c '" + code.replace("'", "''") + "'"


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='coding-agent-test-')
        self.root = Path(self.tmp.name).resolve()
        self.previous = m._workspace
        m._workspace = self.root
        self.settings_patch = patch.object(m, '_instruction_settings', return_value=(self.root/'global', ['AGENTS.override.md', 'AGENTS.md'], 32768))
        self.settings_patch.start()

    def tearDown(self):
        m._shutdown_sessions()
        m._workspace = self.previous
        self.settings_patch.stop()
        self.tmp.cleanup()

    def file(self, path, text, encoding='utf-8'):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode(encoding))
        return p

    def apply(self, body, **kwargs):
        return m.apply_patch('*** Begin Patch\n' + body + '\n*** End Patch', **kwargs)

    def run_command(self, command, **kwargs):
        return m.exec_command(command, cwd=str(self.root), **kwargs)

    def finish(self, result):
        stdout, stderr = result.get('stdout', ''), result.get('stderr', '')
        deadline = time.monotonic() + 10
        while result['running'] or result['has_more_output'] or not result['output_complete']:
            self.assertLess(time.monotonic(), deadline, result)
            result = m.read_process(result['session_id'], wait_ms=1000)
            stdout += result['stdout']; stderr += result['stderr']
        return result, stdout, stderr

    def test_patch_anchor_repeated_body(self):
        p = self.file('a.py', 'def first():\n    return 1\n\ndef second():\n    return 1\n')
        r = self.apply('*** Update File: a.py\n@@ def second():\n-    return 1\n+    return 2')
        self.assertTrue(r['ok'], r)
        self.assertEqual(p.read_text(), 'def first():\n    return 1\n\ndef second():\n    return 2\n')

    def test_patch_eof_repeated_context(self):
        p = self.file('a', 'value = 1\nmiddle\nvalue = 1\n')
        r = self.apply('*** Update File: a\n@@\n-value = 1\n+value = 2\n*** End of File')
        self.assertTrue(r['ok'], r)
        self.assertEqual(p.read_text(), 'value = 1\nmiddle\nvalue = 2\n')

    def test_patch_nested_anchors(self):
        p = self.file('a.py', 'class A:\n    def method(self):\n        return 1\nclass B:\n    def method(self):\n        return 1\n')
        r = self.apply('*** Update File: a.py\n@@ class B:\n@@     def method(self):\n-        return 1\n+        return 2')
        self.assertTrue(r['ok'], r)
        self.assertEqual(p.read_text().count('return 2'), 1)
        self.assertIn('class A:\n    def method(self):\n        return 1', p.read_text())

    def test_patch_multiple_hunks_original_offsets(self):
        p = self.file('a', 'first\nx\nmiddle\ny\nlast\n')
        r = self.apply('*** Update File: a\n@@\n first\n-x\n+x1\n+x2\n@@\n middle\n-y\n+y2')
        self.assertTrue(r['ok'], r)
        self.assertEqual(p.read_text(), 'first\nx1\nx2\nmiddle\ny2\nlast\n')

    def test_patch_add_move_delete(self):
        self.assertTrue(self.apply('*** Add File: folder/a.txt\n+中文🙂')['ok'])
        r = self.apply('*** Update File: folder/a.txt\n*** Move to: folder/b.txt\n@@\n-中文🙂\n+结果🙂')
        self.assertTrue(r['ok'], r)
        self.assertFalse((self.root / 'folder/a.txt').exists())
        self.assertEqual((self.root / 'folder/b.txt').read_text(encoding='utf-8'), '结果🙂\n')
        self.assertTrue(self.apply('*** Delete File: folder/b.txt')['ok'])
        self.assertFalse((self.root / 'folder/b.txt').exists())

    def test_patch_destination_collision(self):
        a = self.file('a', 'a\n'); b = self.file('b', 'b\n')
        r = self.apply('*** Update File: a\n*** Move to: b\n@@\n-a\n+c')
        self.assertFalse(r['ok'])
        self.assertEqual(a.read_text(), 'a\n'); self.assertEqual(b.read_text(), 'b\n')

    def test_patch_invalid_second_file_keeps_first(self):
        a = self.file('a', 'original\n'); b = self.file('b', 'original\n')
        r = self.apply('*** Update File: a\n@@\n-original\n+changed\n*** Update File: b\n@@\n-missing\n+changed')
        self.assertFalse(r['ok'])
        self.assertEqual(a.read_text(), 'original\n'); self.assertEqual(b.read_text(), 'original\n')

    def test_patch_invalid_unprefixed_context(self):
        a = self.file('a', 'a\n')
        self.assertFalse(self.apply('*** Update File: a\n@@\na\n+b')['ok'])
        self.assertEqual(a.read_text(), 'a\n')

    def test_patch_dry_run_has_no_writes(self):
        r = self.apply('*** Add File: new/child\n+preview', dry_run=True)
        self.assertTrue(r['ok'], r); self.assertIn('+preview', r['diff'])
        self.assertFalse((self.root/'new').exists())

    def test_patch_crlf_and_bom(self):
        a = self.file('a', 'old\r\n', 'utf-8-sig')
        self.assertTrue(self.apply('*** Update File: a\n@@\n-old\n+new')['ok'])
        self.assertEqual(a.read_bytes(), b'\xef\xbb\xbfnew\r\n')

    def test_patch_utf16_big_endian(self):
        a = self.root/'a'
        a.write_bytes(b'\xfe\xff'+'old\r\n'.encode('utf-16-be'))
        r = self.apply('*** Update File: a\n@@\n-old\n+new')
        self.assertTrue(r['ok'], r)
        self.assertEqual(a.read_bytes(), b'\xfe\xff'+'new\r\n'.encode('utf-16-be'))

    def test_output_cache_reclaims_consumed_bytes(self):
        s = m.ProcessSession('fake', '', '.', None, 'pipe', '')
        s.append('stdout', 'x'*1000)
        self.assertEqual(s.drain(1000)[0], 'x'*1000)
        self.assertEqual(s.spools['stdout'].seek(0, os.SEEK_END), 0)
        s.append('stdout', 'new')
        self.assertEqual(s.drain(1000)[0], 'new')
        for spool in s.spools.values(): spool.close()

    def test_patch_conflicting_external_edit_is_not_overwritten(self):
        a = self.file('a', 'original\n')
        real = m._agents_for_paths
        def concurrent_edit(paths, root):
            a.write_text('external\n', encoding='utf-8')
            return real(paths, root)
        with patch.object(m, '_agents_for_paths', side_effect=concurrent_edit):
            r = self.apply('*** Update File: a\n@@\n-original\n+mine')
        self.assertFalse(r['ok']); self.assertIn('changed during', r['error'])
        self.assertEqual(a.read_text(), 'external\n')

    def test_patch_io_failure_rolls_back(self):
        a = self.file('a', 'a\n'); b = self.file('b', 'b\n')
        real = m._atomic_write
        def fail_second(path, data):
            if path == b and data == b'new\n':
                raise PermissionError('simulated write failure')
            real(path, data)
        with patch.object(m, '_atomic_write', side_effect=fail_second):
            r = self.apply('*** Update File: a\n@@\n-a\n+new\n*** Update File: b\n@@\n-b\n+new')
        self.assertFalse(r['ok']); self.assertIn('rolled back', r['error'])
        self.assertEqual(a.read_text(), 'a\n'); self.assertEqual(b.read_text(), 'b\n')

    def test_patch_explicit_cwd(self):
        p = self.file('other/a', 'a\n')
        r = self.apply('*** Update File: a\n@@\n-a\n+b', cwd=str(p.parent))
        self.assertTrue(r['ok'], r); self.assertEqual(p.read_text(), 'b\n')

    def test_workspace_inspects_nested_extensionless_file_before_edit(self):
        self.file('AGENTS.md', 'root rules')
        self.file('child/AGENTS.override.md', 'child rules')
        self.file('child/AGENTS.md', 'ignored')
        r = m.workspace(inspect_paths=['child/Makefile'], set_default=False)
        self.assertEqual([a['content'] for a in r['agents']], ['root rules', 'child rules'])
        self.assertFalse((self.root/'child/Makefile').exists())

    def test_workspace_inspection_does_not_change_default(self):
        d = self.root/'other'; d.mkdir()
        self.assertTrue(m.workspace(str(d), set_default=False)['ok'])
        self.assertEqual(m._workspace, self.root)

    def test_process_context_snapshot_survives_workspace_change(self):
        self.file('AGENTS.md', 'original rules')
        r = self.run_command('Start-Sleep -Milliseconds 200', yield_time_ms=0)
        other = self.root/'other'; other.mkdir()
        m.workspace(str(other))
        final, _, _ = self.finish(r)
        self.assertEqual(final['applicable_agents'][0]['content'], 'original rules')

    def test_utf8_split_boundaries(self):
        data = '中文🙂'.encode('utf-8')
        for boundary in range(1, len(data)):
            class Pipe:
                def __init__(self): self.chunks = iter([data[:boundary], data[boundary:], b''])
                def read(self, n): return next(self.chunks)
                read1 = read
                def close(self): pass
            s = m.ProcessSession('fake', '', '.', None, 'pipe', '')
            m._pipe_reader(s, 'stdout', Pipe())
            self.assertEqual(s.drain(1000)[0], '中文🙂')
            for spool in s.spools.values(): spool.close()

    def test_output_paging_is_lossless(self):
        s = m.ProcessSession('fake', '', '.', None, 'pipe', '')
        text = ('中文🙂abcdef' * 1000) + 'TAIL_MARKER'
        s.append('stdout', text)
        chunks = []
        while s.has_output(): chunks.append(s.drain(1000)[0])
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        for spool in s.spools.values(): spool.close()

    def test_finished_command_keeps_session_for_remaining_output(self):
        r = self.run_command("Write-Output ('x' * 3000 + 'TAIL_MARKER')", max_output_chars=1000)
        self.assertFalse(r['running']); self.assertTrue(r['has_more_output']); self.assertIsNotNone(r['session_id'])
        final, out, err = self.finish(r)
        self.assertTrue(final['ok'], final); self.assertEqual(out.strip(), 'x'*3000+'TAIL_MARKER')

    def test_native_failure_exit_code(self):
        r = self.run_command('& $env:ComSpec /d /c exit 7')
        self.assertFalse(r['ok']); self.assertEqual(r['exit_code'], 7)

    def test_explicit_handled_native_failure(self):
        r = self.run_command('& $env:ComSpec /d /c exit 7\nexit 0')
        self.assertTrue(r['ok'], r); self.assertEqual(r['exit_code'], 0)

    def test_powershell_error_fails(self):
        r = self.run_command("Write-Error 'test failure'\nWrite-Output 'must not run'")
        self.assertFalse(r['ok']); self.assertNotIn('must not run', r['stdout'])

    def test_literal_multiline_unicode(self):
        command = "$value = @'\n中文🙂 $() ` quoted \" ' text\n'@\nWrite-Output $value"
        r = self.run_command(command)
        self.assertTrue(r['ok'], r); self.assertIn('中文🙂 $() ` quoted', r['stdout'])

    def test_python_unicode(self):
        r = self.run_command(ps_python('print("中文🙂")'))
        self.assertTrue(r['ok'], r); self.assertIn('中文🙂', r['stdout'])

    def test_pipe_interactive_input(self):
        r = self.run_command(ps_python('print("READY", flush=True); print("VALUE="+input())'), yield_time_ms=100)
        self.assertTrue(r['running'], r)
        result = m.write_stdin(r['session_id'], '中文🙂', append_newline=True, wait_ms=1000)
        final, out, err = self.finish(result)
        self.assertTrue(final['ok'], (final,out,err)); self.assertIn('VALUE=中文🙂', out)

    def test_empty_stdin_can_poll_finished_process(self):
        r = self.run_command("Write-Output ('x'*3000)", max_output_chars=1000)
        r = m.write_stdin(r['session_id'], max_output_chars=1000)
        self.assertTrue(r['ok'], r); self.assertGreater(len(r['stdout']), 0)

    def test_conpty_interaction(self):
        r = self.run_command(ps_python('print("READY", flush=True); print("VALUE="+input())'), tty=True, yield_time_ms=300)
        self.assertTrue(r['running'], r); self.assertEqual(r['backend'], 'ConPTY')
        r = m.write_stdin(r['session_id'], 'conpty-ok', append_newline=True, wait_ms=1000)
        final, out, err = self.finish(r)
        self.assertTrue(final['ok'], (final,out,err)); self.assertIn('VALUE=conpty-ok', out)

    def test_conpty_noninteractive_completion(self):
        r = self.run_command("Write-Output '中文🙂'", tty=True)
        final, out, err = self.finish(r)
        self.assertTrue(final['ok'], (final,out,err)); self.assertIn('中文🙂', out)

    def test_conpty_native_failure(self):
        final, out, err = self.finish(self.run_command('& $env:ComSpec /d /c exit 7', tty=True))
        self.assertFalse(final['ok']); self.assertEqual(final['exit_code'], 7)

    def test_global_and_repository_instruction_chain(self):
        self.file('global/AGENTS.md', 'global rules')
        self.file('repo/.git', 'gitdir: stub')
        self.file('repo/AGENTS.md', 'repo rules')
        self.file('repo/child/AGENTS.md', 'child rules')
        r = m.workspace(str(self.root/'repo/child'), set_default=False)
        self.assertEqual([a['content'] for a in r['agents']], ['global rules', 'repo rules', 'child rules'])

    def test_empty_override_uses_normal(self):
        self.file('AGENTS.override.md', '')
        self.file('AGENTS.md', 'normal')
        self.assertEqual(m.workspace()['agents'][0]['content'], 'normal')

    def test_instruction_budget_utf8(self):
        self.file('AGENTS.md', '中' * 20)
        with patch.object(m, '_instruction_settings', return_value=(self.root/'global', ['AGENTS.md'], 10)):
            r = m.workspace()
        self.assertEqual(r['agents'][0]['content'], '中' * 3)

    def test_kill_process_tree(self):
        code = 'import subprocess,sys,time; child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(90)"]); print(child.pid,flush=True); time.sleep(90)'
        r = self.run_command(ps_python(code), yield_time_ms=300)
        text = r['stdout']
        while not text.strip():
            text += m.read_process(r['session_id'], wait_ms=1000)['stdout']
        pid = int(text.strip())
        killed = m.kill_process(r['session_id'])
        self.assertTrue(killed['killed'], killed)
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                self.assertNotEqual(code.value, 259)
            finally: ctypes.windll.kernel32.CloseHandle(handle)

    def test_compatibility_timeout(self):
        r = m.powershell('Write-Output "before"; Start-Sleep -Seconds 90', cwd=str(self.root), timeout_seconds=1)
        self.assertFalse(r['ok']); self.assertTrue(r['timed_out']); self.assertFalse(r['running'])
        self.assertIn('before', r['stdout'])

    def test_unknown_session(self):
        self.assertFalse(m.read_process('missing')['ok'])

    @unittest.skipUnless(CODEX.is_file(), 'Local Codex CLI unavailable')
    def test_official_codex_patch_differential(self):
        cases = [
            ('def first():\n    return 1\n\ndef second():\n    return 1\n', '@@ def second():\n-    return 1\n+    return 2'),
            ('x\nmiddle\nx\n', '@@\n-x\n+y\n*** End of File'),
            ('one\nx\nmiddle\ny\n', '@@\n one\n-x\n+x1\n+x2\n@@\n middle\n-y\n+y2'),
            ('a\nb\n', '@@\n-a\n+A'),
        ]
        for original, body in cases:
            with self.subTest(body=body):
                a = self.file('official.txt', original)
                b = self.file('local.txt', original)
                patch_text = '*** Begin Patch\n*** Update File: official.txt\n'+body+'\n*** End Patch'
                p = subprocess.run([str(CODEX), '--codex-run-as-apply-patch', patch_text], cwd=self.root, capture_output=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stderr)
                r = self.apply('*** Update File: local.txt\n'+body)
                self.assertTrue(r['ok'], r); self.assertEqual(a.read_bytes(), b.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
