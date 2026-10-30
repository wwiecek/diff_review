"""Run with python3 -m unittest test_diff_review -v."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import diff_review as dr


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = Path.cwd()
        os.chdir(self.root)
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.org')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'core.autocrlf', 'false')

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def git(self, *args):
        return dr.git(self.root, *args).stdout.decode()

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'test')
        return self.git('rev-parse', 'HEAD').strip()

    def collect(self, **kwargs):
        options = dict(patch=None, commit=None, between=None, base=None,
                       context=3, no_untracked=True, include_non_text=False)
        options.update(kwargs)
        return dr.collect(argparse.Namespace(**options))

    def test_default_combines_index_worktree_without_untracked(self):
        self.write('a.R', 'one\ntwo\nthree\n')
        self.commit()
        self.write('a.R', 'ONE\ntwo\nthree\n')
        self.git('add', 'a.R')
        self.write('a.R', 'ONE\ntwo\nTHREE\n')
        self.write('note.md', 'untracked\n')
        self.write('.gitignore', '*.ignored\n')
        self.write('x.ignored', 'ignore\n')
        before = self.git('status', '--porcelain')
        files = self.collect()['files']
        self.assertEqual([f['path'] for f in files].count('a.R'), 1)
        self.assertEqual(next(f for f in files if f['path'] == 'a.R')['adds'], 2)
        self.assertNotIn('note.md', [f['path'] for f in files])
        self.assertIn('note.md', [f['path'] for f in self.collect(no_untracked=False)['files']])
        self.assertNotIn('x.ignored', [f['path'] for f in files])
        self.assertEqual(len(self.collect(no_untracked=True)['files']), 1)
        self.assertEqual(before, self.git('status', '--porcelain'))

    def test_eol_noise_is_ignored_in_all_git_modes(self):
        self.write('code.R', 'one\ntwo\nthree\n')
        self.write('noise.R', 'unchanged\n')
        first = self.commit()
        Path('code.R').write_bytes(b'one\r\ntwo\r\nthree\r\n')
        Path('noise.R').write_bytes(b'unchanged\r\n')
        self.assertEqual(self.collect()['files'], [])
        second = self.commit()
        self.assertEqual(self.collect(commit=second)['files'], [])
        self.assertEqual(self.collect(between=[first, second])['files'], [])
        self.write('code.R', 'one\ntwo\nthree\n')
        self.assertEqual(self.collect()['files'], [])
        Path('code.R').write_bytes(b'ONE\r\ntwo\r\nthree\r\n')
        self.git('add', 'code.R')
        Path('code.R').write_bytes(b'ONE\ntwo\nTHREE\n')
        for mode in ({}, dict(base=first)):
            data = self.collect(**mode)
            self.assertEqual(len(data['files']), 1)
            f = data['files'][0]
            self.assertEqual((f['adds'], f['deletes']), (2, 2))
            self.assertEqual(f['whole_file'], ['ONE', 'two', 'THREE'])
        # Whitespace within lines is still a real change.
        self.write('code.R', 'one \ntwo\nthree\n')
        self.assertEqual(self.collect()['files'][0]['adds'], 1)
        branch = self.git('symbolic-ref', '--short', 'HEAD').strip()
        self.assertIn(branch, self.collect()['comparison'])

    def test_generated_review_is_not_collected_as_untracked(self):
        self.write('source.R', 'code\n')
        self.commit()
        self.write('diff-review.html', dr.render(dict(comparison='Previous review',
                   files=[], comments=[], context=3)))
        self.write('page.html', '<html>A real new source file</html>')
        self.assertEqual([f['path'] for f in self.collect(no_untracked=False)['files']], ['page.html'])

    def test_browser_open_is_opt_in(self):
        with patch.object(dr.webbrowser, 'open') as opening:
            dr.main(['--output', str(self.root / 'review.html')])
            opening.assert_not_called()
            dr.main(['--open', '--output', str(self.root / 'review.html')])
            opening.assert_called_once_with((self.root / 'review.html').as_uri())

    def test_non_text_formats_are_excluded_even_when_ascii(self):
        names = ['report.pdf', 'data.RDS', 'slides.pptx', 'image.png', 'plot.svg']
        for name in names + ['source.R', 'README.md', 'mystery.bin']:
            self.write(name, 'old\n')
        self.commit()
        for name in names + ['source.R', 'README.md']:
            self.write(name, 'new\n')
        Path('mystery.bin').write_bytes(b'\x00binary')
        self.write('new.R', 'untracked code\n')
        self.write('new.pdf', 'untracked PDF\n')
        self.assertEqual({f['path'] for f in self.collect()['files']},
                         {'source.R', 'README.md'})
        self.assertEqual({f['path'] for f in self.collect(no_untracked=False)['files']},
                         {'source.R', 'README.md', 'new.R'})
        self.assertEqual(len(self.collect(include_non_text=True)['files']), 8)
        patch_text = self.git('diff', 'HEAD')
        self.write('input.diff', patch_text)
        self.assertEqual({f['path'] for f in self.collect(patch='input.diff')['files']},
                         {'source.R', 'README.md'})
        revision = self.commit()
        self.assertFalse(any(f['path'] in names for f in self.collect(commit=revision)['files']))

    def test_highlights_align_similar_lines_around_insertions(self):
        old = ['total <- sum(x)', 'average <- total / n']
        new = ['message("starting")', 'total <- sum(x, na.rm = TRUE)',
               'average <- total / (n + 1)']
        rows = [dict(kind='delete', source=line) for line in old]
        rows += [dict(kind='add', source=line) for line in new]
        dr.highlight(dict(rows=rows))
        self.assertNotIn('highlights', rows[2])
        highlighted = lambda row: ''.join(row['source'][a:b]
                                         for a, b in row.get('highlights', []))
        self.assertEqual(highlighted(rows[3]), ', na.rm = TRUE')
        self.assertEqual(highlighted(rows[4]), '( + 1)')
        unrelated = [dict(kind='delete', source='return(alpha)'),
                     dict(kind='add', source='saveRDS(data, "out.rds")')]
        dr.highlight(dict(rows=unrelated))
        self.assertTrue(all('highlights' not in r for r in unrelated))

    def test_highlights_preserve_unicode_and_repeated_characters(self):
        old = '😀 value <- "' + 'a' * 210 + 'x' + 'b' * 210 + '"'
        new = old.replace('x', 'y')
        rows = [dict(kind='delete', source=old), dict(kind='add', source=new)]
        dr.highlight(dict(rows=rows))
        self.assertEqual([old[a:b] for a, b in rows[0]['highlights']], ['x'])
        self.assertEqual([new[a:b] for a, b in rows[1]['highlights']], ['y'])
        self.assertEqual(rows[0]['highlights'][0][0], old.index('x'))

    def test_revision_modes_use_correct_blobs(self):
        self.write('file with spaces.txt', 'root\n')
        first = self.commit()
        self.write('file with spaces.txt', 'second\n')
        second = self.commit()
        self.write('file with spaces.txt', 'worktree\n')
        self.assertEqual(self.collect(commit=first)['files'][0]['kind'], 'added')
        for mode in (dict(commit=second), dict(between=[first, second])):
            f = self.collect(**mode)['files'][0]
            self.assertEqual(f['whole_file'], ['second'])
        self.assertEqual(self.collect(base=first)['files'][0]['whole_file'], ['worktree'])
        self.assertRaises(ValueError, self.collect, commit='--bad-rev')

    def test_unborn_and_empty(self):
        self.write('staged.txt', 'staged\n')
        self.git('add', '.')
        self.write('new.txt', 'new')
        files = self.collect()['files']
        self.assertEqual(len(files), 1)
        self.assertEqual(len(self.collect(no_untracked=False)['files']), 2)
        self.assertTrue(all(f['adds'] == 1 for f in files))
        self.commit()
        self.assertEqual(self.collect()['files'], [])

    def test_multihunk_patch_unicode_spaces_and_no_newline(self):
        path = 'folder/żółć with spaces.txt'
        original = ''.join(f'line {i}\n' for i in range(30))
        self.write(path, original)
        self.commit()
        self.write(path, original.replace('line 2\n', 'changed\n').replace('line 27\n', 'other\n').rstrip('\n'))
        patch = self.git('diff', '--unified=1')
        self.write('input.diff', patch)
        f = self.collect(patch='input.diff')['files'][0]
        self.assertEqual(f['path'], path)
        self.assertGreaterEqual(len(f['hunks']), 2)
        adds = [r for h in f['hunks'] for r in h['rows'] if r['kind'] == 'add']
        self.assertEqual([r['new_line'] for r in adds], [3, 28, 30])
        self.assertTrue(adds[-1]['no_newline'])
        self.assertFalse(f['whole_file_available'])
        self.assertRaises(ValueError, dr.parse_patch, patch.rsplit('\n', 4)[0])
        self.assertRaises(ValueError, dr.parse_patch, 'not a patch')

    def test_renames_deletions_binary_modes_and_thresholds(self):
        self.write('old name.R', 'same\n')
        self.write('delete.md', 'remove me\n')
        self.write('binary.bin', '\x00old')
        self.write('mode.sh', '#!/bin/sh\n')
        self.write('lines.csv', 'x\n' * 1001)
        self.write('bytes.txt', 'x' * (dr.MAX_WHOLE_BYTES + 1))
        self.commit()
        self.git('mv', 'old name.R', 'new name.R')
        Path('delete.md').unlink()
        self.write('binary.bin', '\x00new')
        Path('mode.sh').chmod(0o755)
        self.write('lines.csv', 'y\n' + 'x\n' * 1000)
        self.write('bytes.txt', 'y' * (dr.MAX_WHOLE_BYTES + 1))
        files = {f['path']: f for f in self.collect()['files']}
        self.assertEqual(files['new name.R']['kind'], 'renamed')
        self.assertEqual(files['delete.md']['whole_side'], 'old')
        self.assertNotIn('binary.bin', files)
        all_files = self.collect(include_non_text=True)['files']
        self.assertTrue(next(f for f in all_files if f['path'] == 'binary.bin')['binary'])
        self.assertFalse(files['lines.csv']['whole_file_available'])
        self.assertFalse(files['bytes.txt']['whole_file_available'])
        self.assertEqual(files['mode.sh']['hunks'], [])

    def test_comments_and_script_escaping(self):
        self.write('x.R', '\n'.join(['unchanged'] * 20) + '\n')
        self.commit()
        self.write('x.R', 'changed\n' + '\n'.join(['unchanged'] * 19) + '\n')
        files = self.collect()['files']
        payload = '</script><script>globalThis.pwned=true</script>'
        self.write('comments.json', json.dumps([
            dict(path='x.R', side='new', line=18, body=payload),
            dict(path='x.R', side='old', line=1, author='You', body='old note'),
            dict(path='missing', side=payload, line=payload, body='unmatched')]))
        comments = dr.import_comments('comments.json', files)
        self.assertEqual([c['matched'] for c in comments], [True, True, False])
        html = dr.render(dict(comparison=payload, files=files, comments=comments, context=3))
        self.assertNotIn(payload, html)
        self.assertIn('\\u003c/script', html)

    def test_submodules_and_unusual_paths(self):
        patch = ('diff --git a/sub b/sub\nindex 1111111..2222222 160000\n'
                 '--- a/sub\n+++ b/sub\n@@ -1 +1 @@\n'
                 '-Subproject commit 1111111\n+Subproject commit 2222222\n')
        f = dr.parse_patch(patch)[0]
        self.assertEqual(dr.header_paths('a/dir b/file b/dir b/file'),
                         ('dir b/file', 'dir b/file'))
        self.assertTrue(f['submodule'])
        self.assertEqual(f['hunks'], [])
        for path in ['a b.txt', 'a b/c d.txt', 'tab\tfile.txt', 'quote"file.txt']:
            self.write(path, 'old\n')
        self.commit()
        for path in ['a b.txt', 'a b/c d.txt', 'tab\tfile.txt', 'quote"file.txt']:
            self.write(path, 'new\n')
        files = self.collect()['files']
        self.assertEqual(len(files), 4)
        self.assertTrue(all(f['whole_file'] == ['new'] for f in files))

    def test_cli_and_stdin(self):
        script = str(Path(dr.__file__).resolve())
        patch = 'diff --git a/a.txt b/a.txt\nnew file mode 100644\n--- /dev/null\n+++ b/a.txt\n@@ -0,0 +1 @@\n+hello\n'
        result = subprocess.run([sys.executable, script, '--patch', '-', '--no-open',
                                 '--output', 'nested/review.html'], input=patch,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path('nested/review.html').exists())
        # A hunk with a zero-length old range is numbered from its new range.
        row = dr.parse_patch(patch)[0]['hunks'][0]['rows'][0]
        self.assertEqual((row['old_line'], row['new_line']), (None, 1))


if __name__ == '__main__':
    import sys
    unittest.main()
