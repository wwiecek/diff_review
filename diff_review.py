#!/usr/bin/env python3
"""Generate a self-contained, offline HTML review of a Git comparison."""

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import webbrowser

MAX_WHOLE_LINES = 1000
MAX_WHOLE_BYTES = 200 * 1024
NON_TEXT_SUFFIXES = {
    '.pdf', '.rds', '.rda', '.rdata', '.rdb', '.rdx',
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.tif',
    '.tiff', '.svg', '.eps', '.ps', '.heic', '.avif', '.psd', '.ai',
    '.ppt', '.pptx', '.doc', '.docx', '.xls', '.xlsx', '.xlsb', '.odt',
    '.ods', '.odp', '.key', '.pages', '.numbers',
    '.zip', '.gz', '.bz2', '.xz', '.tar', '.7z', '.rar', '.zst',
    '.mp3', '.wav', '.flac', '.ogg', '.m4a', '.mp4', '.mov', '.avi', '.webm',
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.exe', '.dll', '.so', '.o', '.a', '.pyc', '.class', '.jar', '.wasm',
    '.db', '.sqlite', '.sqlite3', '.parquet', '.feather', '.arrow', '.h5', '.hdf5',
}


def filter_files(files, include_non_text=False):
    if include_non_text:
        return files
    return [f for f in files if not f.get('binary') and not f.get('submodule')
            and Path(f['path']).suffix.lower() not in NON_TEXT_SUFFIXES]


def git(root, *args, check=True):
    result = subprocess.run(['git', '-C', str(root), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            shell=False)
    if check and result.returncode:
        raise ValueError(result.stderr.decode('utf-8', 'replace').strip())
    return result


def decode_path(value):
    """Decode Git's C-quoted path, including octal UTF-8 byte escapes."""
    if not value.startswith('"'):
        return value
    if not value.endswith('"'):
        raise ValueError('Invalid quoted Git path')
    data = bytearray()
    i = 1
    escapes = {'a': 7, 'b': 8, 't': 9, 'n': 10, 'v': 11,
               'f': 12, 'r': 13, '"': 34, '\\': 92}
    while i < len(value) - 1:
        if value[i] != '\\':
            data.extend(value[i].encode('utf-8'))
            i += 1
        else:
            i += 1
            match = re.match(r'[0-7]{1,3}', value[i:])
            if match:
                data.append(int(match[0], 8))
                i += len(match[0])
            else:
                if value[i] not in escapes:
                    raise ValueError('Invalid Git path escape')
                data.append(escapes[value[i]])
                i += 1
    return data.decode('utf-8', 'replace')


def header_paths(header):
    # Git leaves spaces unquoted, so shlex alone cannot parse these headers.
    # Prefer identical paths, including names containing the separator " b/".
    if header.startswith('a/'):
        for separator in re.finditer(r' b/', header):
            old, new = header[:separator.start()], header[separator.start() + 1:]
            if old[2:] == new[2:]:
                return old[2:], new[2:]
    match = re.fullmatch(r'("(?:[^"\\]|\\.)*"|a/.*?) ("(?:[^"\\]|\\.)*"|b/.*)', header)
    if not match:
        raise ValueError('Invalid diff --git header: ' + header)
    return tuple(decode_path(p)[2:] for p in match.groups())


def new_file(old_path, path):
    return dict(path=path, old_path=old_path, kind='modified',
                file_type=Path(path).suffix[1:].upper() or 'OTHER',
                adds=0, deletes=0, hunks=[], metadata=[],
                whole_file_available=False, whole_reason='Complete file not known from patch')


def highlight_replacements(deleted, added):
    """Align similar lines in order; leave unrelated additions/deletions plain."""
    old_sources = [r['source'] for r in deleted]
    new_sources = [r['source'] for r in added]
    # This is optional decoration. Bound work on large replacement blocks.
    if (len(deleted) * len(added) > 4096 or
            sum(map(len, old_sources)) * sum(map(len, new_sources)) > 4_000_000 or
            any(len(s) > 4000 for s in old_sources + new_sources)):
        return
    scores = [[0.0] * (len(added) + 1) for _ in range(len(deleted) + 1)]
    choices = {}
    for i, old in enumerate(old_sources, 1):
        for j, new in enumerate(new_sources, 1):
            matcher = difflib.SequenceMatcher(None, old.strip(), new.strip(),
                                              autojunk=False)
            similarity = matcher.ratio() if matcher.quick_ratio() >= 0.65 else 0
            score, choice = max((scores[i - 1][j], 'old'),
                                (scores[i][j - 1], 'new'))
            if similarity >= 0.65 and scores[i - 1][j - 1] + similarity > score:
                score, choice = scores[i - 1][j - 1] + similarity, 'pair'
            scores[i][j], choices[i, j] = score, choice
    i, j = len(deleted), len(added)
    while i and j:
        choice = choices[i, j]
        if choice == 'old':
            i -= 1
        elif choice == 'new':
            j -= 1
        else:
            old, new = deleted[i - 1], added[j - 1]
            old['highlights'], new['highlights'] = [], []
            for tag, a, b, c, d in difflib.SequenceMatcher(
                    None, old['source'], new['source'], autojunk=False).get_opcodes():
                if tag != 'equal':
                    if a < b:
                        old['highlights'].append([a, b])
                    if c < d:
                        new['highlights'].append([c, d])
            old['highlights'].sort()
            new['highlights'].sort()
            i, j = i - 1, j - 1


def highlight(hunk):
    rows = hunk['rows']
    i = 0
    while i < len(rows):
        if rows[i]['kind'] == 'context':
            i += 1
            continue
        end = i
        while end < len(rows) and rows[end]['kind'] != 'context':
            end += 1
        highlight_replacements(
            [r for r in rows[i:end] if r['kind'] == 'delete'],
            [r for r in rows[i:end] if r['kind'] == 'add'])
        i = end


def parse_patch(patch):
    if not patch.strip():
        return []
    # Split only on LF: str.splitlines() would corrupt source containing CR/VT.
    lines = patch.split('\n')
    if lines[-1] == '':
        lines.pop()
    files = []
    f = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('diff --git '):
            old, new = header_paths(line[11:])
            f = new_file(old, new)
            files.append(f)
        elif f is None:
            raise ValueError('Expected a conventional Git unified patch (diff --git)')
        elif line.startswith('@@ '):
            match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)', line)
            if not match:
                raise ValueError('Invalid hunk header: ' + line)
            old, oc, new, nc, title = match.groups()
            old, new = int(old), int(new)
            oc, nc = int(oc or 1), int(nc or 1)
            hunk = dict(old_start=old, new_start=new, old_count=oc,
                        new_count=nc, header=line, rows=[])
            f['hunks'].append(hunk)
            i += 1
            used_old = used_new = 0
            while used_old < oc or used_new < nc:
                if i >= len(lines) or not lines[i] or lines[i][0] not in ' +-':
                    raise ValueError('Truncated or invalid patch hunk')
                source = lines[i]
                kind = {' ': 'context', '+': 'add', '-': 'delete'}[source[0]]
                row = dict(kind=kind, source=source[1:].removesuffix('\r'),
                           old_line=old if kind != 'add' else None,
                           new_line=new if kind != 'delete' else None)
                hunk['rows'].append(row)
                if kind != 'add':
                    old += 1
                    used_old += 1
                if kind != 'delete':
                    new += 1
                    used_new += 1
                f['adds'] += kind == 'add'
                f['deletes'] += kind == 'delete'
                i += 1
                if i < len(lines) and lines[i] == '\\ No newline at end of file':
                    row['no_newline'] = True
                    i += 1
                if used_old > oc or used_new > nc:
                    raise ValueError('Hunk line counts do not match header')
            highlight(hunk)
            continue
        elif line.startswith(('--- ', '+++ ')):
            value = decode_path(line[4:].split('\t', 1)[0])
            if value != '/dev/null':
                key = 'old_path' if line.startswith('---') else 'path'
                f[key] = value[2:]
            else:
                f['kind'] = 'added' if line.startswith('---') else 'deleted'
        elif line.startswith('rename from '):
            f['old_path'] = decode_path(line[12:])
            f['kind'] = 'renamed'
            f['metadata'].append(line)
        elif line.startswith('rename to '):
            f['path'] = decode_path(line[10:])
            f['metadata'].append(line)
        elif line.startswith(('new file mode ', 'deleted file mode ')):
            f['kind'] = 'added' if line.startswith('new') else 'deleted'
            f['metadata'].append(line)
            if line.endswith(' 160000'):
                f['submodule'] = True
        elif line.startswith(('Binary files ', 'GIT binary patch')):
            f['binary'] = True
            f['metadata'].append('Binary file — textual contents unavailable')
        elif line.startswith(('index ', 'old mode ', 'new mode ', 'similarity index ',
                              'dissimilarity index ', 'copy from ', 'copy to ')):
            f['metadata'].append(line)
            if (line.startswith('index ') and line.endswith(' 160000')) or line in ('new file mode 160000', 'deleted file mode 160000'):
                f['submodule'] = True
        elif f.get('binary'):
            pass  # Encoded Git binary-patch payload is metadata, never source.
        elif line:
            raise ValueError('Unexpected patch line: ' + line[:120])
        i += 1
    for f in files:
        if not f['hunks'] and not f['metadata']:
            raise ValueError('Incomplete patch: file header has no changes')
        f['file_type'] = Path(f['path']).suffix[1:].upper() or 'OTHER'
        if f.get('submodule'):
            f['hunks'] = []
            f['metadata'].append('Submodule pointer changed')
    return files


def add_whole(f, content, side):
    if b'\0' in content:
        f['binary'] = True
    if f.get('binary') or f.get('submodule'):
        f['whole_reason'] = 'Binary file or submodule — no text preview'
        return
    text = content.decode('utf-8', 'replace').replace('\r\n', '\n')
    whole = text.split('\n')
    if whole[-1] == '':
        whole.pop()
    f['whole_lines'] = len(whole)
    f['whole_bytes'] = len(content)
    f['whole_side'] = side
    if len(whole) > MAX_WHOLE_LINES or len(content) > MAX_WHOLE_BYTES:
        f['whole_reason'] = f'Full preview omitted: {len(whole):,} lines, {len(content):,} bytes (limits {MAX_WHOLE_LINES:,} lines / {MAX_WHOLE_BYTES:,} bytes)'
        return
    f.update(whole_file=whole, whole_file_available=True,
             whole_no_newline=bool(content) and not content.endswith(b'\n'))


def revision(root, value):
    return git(root, 'rev-parse', '--verify', '--end-of-options',
               value + '^{commit}').stdout.decode().strip()


def collect(args):
    if args.patch:
        raw = sys.stdin.buffer.read() if args.patch == '-' else Path(args.patch).read_bytes()
        return dict(comparison='Patch: ' + args.patch,
                    files=filter_files(parse_patch(raw.decode('utf-8', 'replace')),
                                       args.include_non_text))
    root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').stdout.decode().strip())
    empty = git(root, 'hash-object', '-t', 'tree', '--stdin').stdout.decode().strip()
    head = git(root, 'rev-parse', '--verify', 'HEAD', check=False)
    baseline = head.stdout.decode().strip() if head.returncode == 0 else empty
    new_revision = None
    if args.commit:
        new_revision = revision(root, args.commit)
        parents = git(root, 'rev-list', '--parents', '-n', '1', new_revision).stdout.decode().split()
        baseline = parents[1] if len(parents) > 1 else empty
        comparison = f'Commit {args.commit} versus first parent'
    elif args.between:
        baseline, new_revision = [revision(root, v) for v in args.between]
        comparison = f'{args.between[0]} → {args.between[1]} (direct comparison)'
    elif args.base:
        baseline = revision(root, args.base)
        comparison = f'Working tree versus {args.base}'
    else:
        branch = git(root, 'symbolic-ref', '--short', '-q', 'HEAD', check=False)
        name = branch.stdout.decode('utf-8', 'replace').strip() or 'detached HEAD'
        comparison = f'Working tree versus {name} (HEAD; staged + unstaged' + (')' if args.no_untracked else ' + untracked)')
    revisions = [baseline] + ([new_revision] if new_revision else [])
    patch = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-color',
                '--find-renames', '--ignore-cr-at-eol', '--submodule=short', f'--unified={args.context}',
                *revisions, '--').stdout
    files = filter_files(parse_patch(patch.decode('utf-8', 'replace')),
                         args.include_non_text)
    for f in files:
        side = 'old' if f['kind'] == 'deleted' else 'new'
        path = f['old_path'] if side == 'old' else f['path']
        if not f['hunks'] and f['kind'] == 'modified':
            f['whole_reason'] = 'Mode-only change — no textual changes'
            continue
        if f.get('binary') or f.get('submodule'):
            f['whole_reason'] = 'Binary file or submodule — no text preview'
            continue
        if side == 'old' or new_revision:
            rev = baseline if side == 'old' else new_revision
            blob = git(root, 'show', f'{rev}:{path}', check=False)
            if blob.returncode == 0:
                add_whole(f, blob.stdout, side)
        else:
            target = root / path
            if target.is_file() and not target.is_symlink():
                add_whole(f, target.read_bytes(), side)
    if not new_revision and not args.no_untracked:
        for raw_path in git(root, 'ls-files', '--others', '--exclude-standard', '-z').stdout.split(b'\0'):
            if not raw_path:
                continue
            path = os.fsdecode(raw_path)
            target = root / path
            if (not args.include_non_text and
                    target.suffix.lower() in NON_TEXT_SUFFIXES):
                continue
            if not target.is_file() or target.is_symlink():
                continue
            content = target.read_bytes()
            if (target.suffix.lower() == '.html' and
                    '<title>diff-review — Local review</title>'.encode('utf-8')
                    in content[:512]):
                continue  # Do not review previous output from this tool.
            f = new_file(path, path)
            f['kind'] = 'untracked'
            if b'\0' in content:
                f['binary'] = True
                f['metadata'].append('Untracked binary file — no text preview')
            else:
                text = content.decode('utf-8', 'replace').replace('\r\n', '\n')
                rows = text.split('\n')
                if rows[-1] == '':
                    rows.pop()
                f['adds'] = len(rows)
                hunk_rows = [dict(kind='add', source=s, old_line=None, new_line=i + 1)
                             for i, s in enumerate(rows)]
                if rows and not content.endswith(b'\n'):
                    hunk_rows[-1]['no_newline'] = True
                f['hunks'] = [dict(old_start=0, new_start=1 if rows else 0,
                                   old_count=0, new_count=len(rows),
                                   header=f'@@ -0,0 +1,{len(rows)} @@', rows=hunk_rows)]
                add_whole(f, content, 'new')
            files.append(f)
    return dict(comparison=comparison,
                files=filter_files(files, args.include_non_text))


def import_comments(path, files):
    comments = json.loads(Path(path).read_text(encoding='utf-8')) if path else []
    if not isinstance(comments, list):
        raise ValueError('Comments JSON must be an array')
    for i, c in enumerate(comments):
        if not isinstance(c, dict) or any(not isinstance(c.get(k), str) for k in ('path', 'body')):
            raise ValueError('Each comment needs string path and body fields')
        c.setdefault('author', 'Codex')
        if not isinstance(c['author'], str):
            raise ValueError('Comment author must be a string')
        c['id'] = f'c{i + 1}'
        c['matched'] = False
        if c.get('side') not in ('old', 'new') or type(c.get('line')) is not int or c['line'] < 1:
            continue
        for f in files:
            if c['path'] != f['path']:
                continue
            rows = [r for h in f['hunks'] for r in h['rows']]
            c['matched'] = (any(r.get(c['side'] + '_line') == c['line'] for r in rows)
                            or (f['whole_file_available'] and c['side'] == f['whole_side']
                                and c['line'] <= len(f['whole_file'])))
    return comments


def render(data):
    encoded = json.dumps(data, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return HTML_TEMPLATE.replace('__REVIEW_DATA__', encoded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--commit', metavar='REV')
    modes.add_argument('--between', nargs=2, metavar=('A', 'B'))
    modes.add_argument('--base', metavar='REV')
    modes.add_argument('--patch', metavar='PATH')
    parser.add_argument('--output', default='diff-review.html')
    parser.add_argument('--context', type=int, default=3)
    untracked = parser.add_mutually_exclusive_group()
    untracked.add_argument('--include-untracked', dest='no_untracked',
                           action='store_false', help='Include untracked files')
    untracked.add_argument('--no-untracked', action='store_true',
                           help='Only tracked files (default)')
    parser.set_defaults(no_untracked=True)
    parser.add_argument('--include-non-text', action='store_true',
                        help='Also include binary and non-text formats')
    opening = parser.add_mutually_exclusive_group()
    opening.add_argument('--open', dest='open_browser', action='store_true',
                         help='Open the generated HTML in a browser')
    opening.add_argument('--no-open', dest='open_browser', action='store_false',
                         help='Only print the output path (default)')
    parser.set_defaults(open_browser=False)
    parser.add_argument('--comments', metavar='JSON')
    args = parser.parse_args(argv)
    if args.context < 0:
        parser.error('--context must be non-negative')
    try:
        data = collect(args)
        data['context'] = args.context
        data['comments'] = import_comments(args.comments, data['files'])
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render(data), encoding='utf-8')
        print(output)
        if args.open_browser:
            webbrowser.open(output.as_uri())
    except (OSError, ValueError) as error:
        parser.exit(2, f'diff-review: {error}\n')


# Embedded template keeps both entrypoints portable, with no runtime assets.
HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>diff-review — Local review</title>
<style>
:root{--bg:#f7f8fa;--white:#fff;--ink:#202a35;--muted:#6b7787;--stroke:#dfe4e9;--stroke2:#edf0f3;--brand:#375cb8;--green:#18844b;--red:#bf4242;--greenbg:#e7f7ec;--redbg:#ffeded;--greenstrong:#bce9cb;--redstrong:#f8cdcd;--bluebg:#ebf2ff;--mono:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;--sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 var(--sans)}button,textarea{font:inherit}button{cursor:pointer}.shell{min-height:100vh}.topbar{height:70px;background:#fff;border-bottom:1px solid var(--stroke);display:flex;align-items:center;justify-content:space-between;padding:0 34px;gap:16px;position:sticky;top:0;z-index:20}.logo{width:34px;height:34px;border-radius:10px;background:#1f2937;display:grid;place-items:center;letter-spacing:-1px;color:#fff;font:700 15px var(--mono)}.brand{display:flex;align-items:center;gap:12px}.brandcopy{display:flex;flex-direction:column;line-height:1.3}.brandcopy strong{font-size:14px;letter-spacing:-.2px}.brandcopy small{font-size:11px;color:var(--muted)}.headright{display:flex;gap:12px;align-items:center}.status{font-size:12px;color:#738091;white-space:nowrap}.status i{display:inline-block;background:#50a981;width:7px;height:7px;border-radius:50%;margin-right:5px}.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;background:#fff;border:1px solid #d5dce3;color:#273443;border-radius:7px;padding:8px 12px;font-weight:600;font-size:12px;box-shadow:0 1px 1px #2221}.btn:hover{background:#f2f5fa}.btn.primary{background:#283b61;border-color:#283b61;color:white}.btn.primary:hover{background:#344f82}.btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.8}.layout{display:grid;grid-template-columns:257px minmax(0,1fr);max-width:1760px;margin:auto;min-height:calc(100vh - 70px)}aside.sidebar{padding:32px 14px 30px 22px;border-right:1px solid var(--stroke);position:sticky;top:70px;height:calc(100vh - 70px);overflow:auto}.sideheading{font-size:10px;text-transform:uppercase;letter-spacing:1.3px;font-weight:800;color:#8a96a4;padding:0 12px 13px}.filelink{width:100%;text-align:left;border:1px solid transparent;background:none;padding:11px 10px;border-radius:8px;display:flex;gap:9px;align-items:flex-start;color:#354254}.filelink:hover{background:#ecf0f6}.filelink.active{background:#e9effb;border-color:#d8e3fb;color:#234da2}.filelink .fileicon{font:600 10px var(--mono);border:1px solid #cbd5e0;border-radius:5px;flex:none;line-height:20px;width:21px;text-align:center;background:white}.filelink .filename{font:600 12px var(--mono);overflow-wrap:anywhere;line-height:1.55}.filelink .filecount{display:block;font:11px var(--mono);margin-top:4px;color:#768296}.plus{color:#19844d}.minus{color:#cf4a44}.sidefoot{margin:28px 8px 0;padding:15px 12px;border-top:1px solid #dee4ea;color:#6c7888;font-size:12px;line-height:1.65}.sidefoot strong{display:block;color:#364252;margin-bottom:4px;font-size:12px}.sidefoot kbd{font:11px var(--mono);background:white;border:1px solid var(--stroke);padding:2px 4px;border-radius:3px}main{min-width:0;padding:36px clamp(20px,3.1vw,56px) 70px}header.pagehead{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:25px}.eyebrow{font-size:11px;font-weight:800;color:#5872a0;text-transform:uppercase;letter-spacing:1.4px;margin:0 0 11px}h1{font-size:29px;letter-spacing:-.95px;line-height:1.15;margin:0 0 10px;font-weight:730}.subtitle{color:#6f7c8c;margin:0;line-height:1.6}.subtitle code,.meta code{font:12px var(--mono);background:#e9edf3;border-radius:4px;padding:3px 5px;color:#4a586b}.summary{display:flex;gap:17px;margin:21px 0 24px;padding:14px 20px;align-items:center;background:white;border:1px solid var(--stroke);border-radius:9px;box-shadow:0 2px 8px #172b4b05;flex-wrap:wrap}.metric{font-size:12px;color:#718091}.metric strong{font-size:14px;color:#273343;margin-right:4px}.mini-bars{display:flex;align-items:center;gap:2px;margin-left:auto}.mini-bars i{display:block;width:9px;height:9px;background:#bfe8cf;border-radius:2px}.mini-bars i.r{background:#efc2c2}.toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}.toolbar h2{font-size:13px;margin:0;color:#3c4857}.toggle{border:0;background:transparent;color:#4e6b9a;font-size:12px;font-weight:700;padding:5px}.toggle:hover{text-decoration:underline}.diffcard{background:white;border:1px solid #dce2e8;border-radius:10px;overflow:hidden;box-shadow:0 3px 12px #15274206;margin:0 0 24px;scroll-margin-top:88px}.diffhead{background:#fbfcfd;padding:13px 16px;display:flex;justify-content:space-between;align-items:center;gap:10px;border-bottom:1px solid var(--stroke)}.diffhead .path{font:600 12px var(--mono);overflow-wrap:anywhere}.diffhead .path span{color:#8b97a4}.diffhead .counts{font:11px var(--mono);white-space:nowrap}.linesheading{display:grid;grid-template-columns:43px 43px minmax(0,1fr) 266px;border-bottom:1px solid #e4e8ec;background:#f6f8fa;font:11px var(--mono);color:#8793a1}.linesheading .guttertitle{grid-column:1/3;text-align:center;padding:7px}.linesheading .codetitle{padding:7px 12px}.linesheading .reviewtitle{border-left:1px solid #e5eaf1;padding:7px 13px}.diffrow{display:grid;grid-template-columns:43px 43px minmax(0,1fr) 266px;min-height:31px;border-bottom:1px solid rgba(205,211,220,.3);font:12px/1.6 var(--mono)}.diffrow:last-child{border-bottom:none}.diffrow.ctx{background:#fff}.diffrow.add{background:var(--greenbg)}.diffrow.del{background:var(--redbg)}.ln{display:flex;justify-content:flex-end;align-items:flex-start;font-size:11px;color:#8d99a5;user-select:none;padding:6px 9px 5px 3px;border-right:1px solid #e4e8ec;cursor:pointer}.ln:hover{background:#cfdbeb;color:#243b5f}.ctx .ln{background:#f8f9fb}.add .ln{background:#daf0e2}.del .ln{background:#f8dfdf}.codecell{display:flex;align-items:flex-start;min-width:0;white-space:pre;overflow:auto;padding:6px 10px 5px 0;scrollbar-width:thin}.codecell .sign{width:24px;display:inline-block;text-align:center;flex:none;color:#87919c}.add .codecell .sign{color:#0d7d43}.del .codecell .sign{color:#b33737}.codecell .src{display:inline-block;tab-size:2;min-width:0}.intra{background:var(--greenstrong);border-radius:2px}.del .intra{background:var(--redstrong)}.reviewcell{position:relative;background:white;border-left:1px solid #e7ebf0;padding:4px 10px 5px;min-width:0;display:flex;align-items:flex-start;flex-direction:column;gap:5px}.commentadd{opacity:0;transition:opacity .12s;background:white;border:1px solid #c7d3e6;border-radius:5px;color:#4265a1;font:700 13px var(--sans);padding:0 5px;min-width:22px;height:22px}.diffrow:hover .commentadd,.commentadd:focus,.diffrow.selected .commentadd{opacity:1}.commentadd:hover{background:#eaf1ff}.commentcard{background:#f5f8fd;border:1px solid #dce5f3;border-radius:7px;padding:10px 11px;width:100%;box-shadow:0 1px 2px #173e7212}.commenttop{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:5px}.avatar{display:inline-grid;place-items:center;width:19px;height:19px;border-radius:6px;color:white;background:#7187b4;font:700 10px var(--sans);margin-right:6px}.commenttop strong{font:700 11px var(--sans);color:#435779}.commenttop small{font:10px var(--sans);color:#9da9b8}.commentcard p{margin:0;color:#3e4b5b;line-height:1.5;font:11.5px/1.5 var(--sans);white-space:pre-wrap;overflow-wrap:anywhere}.commentdelete{background:none;border:none;color:#8e9cac;font-size:12px;padding:0 2px}.commentdelete:hover{color:#be3e3e}.composer{width:100%;background:#fff;border:1px solid #a7bce5;border-radius:7px;padding:9px;box-shadow:0 2px 8px #153f7320}.composer textarea{width:100%;resize:vertical;min-height:69px;padding:8px;border:1px solid #dbe3ec;border-radius:5px;font:12px/1.45 var(--sans);color:#263546;outline:none}.composer textarea:focus{border-color:#7a9cdc}.composeractions{margin-top:7px;display:flex;align-items:center;justify-content:flex-end;gap:6px}.smallbtn{font:600 11px var(--sans);border:1px solid #dae0e7;background:white;color:#5b6776;border-radius:5px;padding:5px 8px}.smallbtn.save{background:#2b4a86;border-color:#2b4a86;color:white}.diffrow.selected .codecell{box-shadow:inset 3px 0 0 #698bd1}.diffrow.selected .reviewcell{background:#f9fbff}.hunk{display:grid;grid-template-columns:86px minmax(0,1fr) 266px;color:#7387a6;background:#f0f4fc;border-top:1px solid #dde8fa;border-bottom:1px solid #dbe6f7;font:11px var(--mono)}.hunk .label{grid-column:1/3;padding:8px 12px}.hunk .annotation{background:white;border-left:1px solid var(--stroke2)}.placeholder{padding:14px 18px;font:12px var(--mono);color:#8290a2}.toast{position:fixed;bottom:24px;right:24px;padding:11px 16px;border-radius:8px;background:#263b5d;color:white;font:12px var(--sans);z-index:50;box-shadow:0 6px 30px #1d2f4b40;display:none}.toast.show{display:block}.endnote{font-size:12px;color:#8190a0;text-align:center;margin-top:23px}.endnote strong{color:#526274}button:focus-visible{outline:2px solid #5e85ce;outline-offset:2px}@media(max-width:1270px){.layout{grid-template-columns:215px minmax(0,1fr)}aside.sidebar{padding-left:9px}.linesheading,.diffrow{grid-template-columns:36px 36px minmax(0,1fr) 228px}.hunk{grid-template-columns:72px minmax(0,1fr) 228px}main{padding:28px 20px 60px}}@media(max-width:920px){.layout{display:block}.sidebar{display:none}main{padding:23px 12px 55px}.topbar{padding:0 13px}.status{display:none}.linesheading,.diffrow{grid-template-columns:32px 32px minmax(0,1fr) 210px}.hunk{grid-template-columns:64px minmax(0,1fr) 210px}.ln{padding-right:6px}.diffrow{font-size:11px}.codecell .sign{width:16px}.reviewcell{padding:4px}.commentcard{padding:7px}.summary{gap:10px}}@media(max-width:650px){.linesheading,.diffrow{grid-template-columns:27px 27px minmax(0,1fr)}.hunk{grid-template-columns:54px minmax(0,1fr)}.reviewcell{grid-column:3;grid-row:2;border-left:0;border-top:1px solid #e2eaf4;min-height:0}.reviewcell:has(> .commentadd:only-child){display:none}.commentadd{opacity:1}.linesheading .reviewtitle,.hunk .annotation{display:none}.headright .btn{padding:8px}.brandcopy small{display:none}h1{font-size:24px}.diffhead{padding:11px}.codecell{font-size:10px}.summary{font-size:11px}.reviewcell .commentcard,.composer{max-width:350px}}

/* Additions to the original approved design: filters, full-file controls. */
.filterblock{margin:0 9px 21px;padding:12px 11px 13px;background:#fff;border:1px solid var(--stroke);border-radius:8px}
.filtertitle{font:800 10px var(--sans);letter-spacing:1px;text-transform:uppercase;color:#8390a0;margin-bottom:9px}
.filteritem{display:flex;align-items:center;gap:7px;padding:4px 0;font-size:12px;color:#44536a;cursor:pointer}
.filteritem input{accent-color:#365ca1;margin:0}.filtercount{margin-left:auto;color:#8996a4;font:11px var(--mono)}
.filterempty{color:#8592a1;font-size:12px;padding:20px 10px}
.diffhead .fileactions{margin-left:auto;display:flex;align-items:center;gap:13px;flex-shrink:0}
.fileview{border:0;background:none;color:#4265a1;font:700 11px var(--sans);cursor:pointer;padding:5px 2px}
.fileview:hover{text-decoration:underline}.fileview:disabled{color:#99a5b2;cursor:not-allowed;text-decoration:none}
.wholelabel{padding:8px 13px;background:#f0f4fc;color:#657e9e;font:11px var(--mono);border-top:1px solid #dde8fa;border-bottom:1px solid #dde8fa}
.full .diffrow .codecell{white-space:pre}.full .diffrow.add .codecell{background:var(--greenbg)}
@media(max-width:650px){.diffhead{align-items:flex-start}.diffhead .fileactions{gap:6px;flex-wrap:wrap;justify-content:flex-end}.fileview{font-size:10px}}

/* A single in-page export summary, with no server or file writes. */
.exportpanel{background:white;border:1px solid #dce2e8;border-radius:10px;padding:21px 23px;margin:33px 0 14px;box-shadow:0 3px 12px #15274206;scroll-margin-top:90px}
.exporthead{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.exporthead .eyebrow{margin:0 0 5px}.exporthead h2{font-size:18px;letter-spacing:-.3px;color:#273343;margin:0}
.exportcount{font:600 12px var(--mono);color:#6c7b8b;margin-left:5px}
.exporthint{font-size:12px;color:#687789;line-height:1.6;margin:13px 0 12px}
.exportbox{display:block;width:100%;min-height:185px;resize:vertical;border:1px solid #d6dfe8;border-radius:7px;padding:13px 15px;background:#f9fbfd;color:#324255;font:12px/1.65 var(--mono);white-space:pre;overflow:auto}
.exportbox:focus{outline:2px solid #7f9ed4;outline-offset:1px}
.exportfoot{color:#8591a0;font-size:11px;line-height:1.6;margin:10px 0 0}

.ln{border-top:0;border-bottom:0;border-left:0;font-family:var(--mono);background:transparent;border-radius:0}
.newline,.previewreason{font:11px var(--sans);color:var(--muted);white-space:normal}
.previewreason{max-width:300px}.hunk .label{overflow:auto;white-space:pre}
.commentcard p{white-space:pre-wrap;overflow-wrap:anywhere}
[hidden]{display:none!important}
@media(max-width:920px){aside.sidebar{display:block;position:static;height:auto;border-right:0;padding:15px}.sidebar .sidefoot{display:none}#fileNav{display:flex;flex-wrap:wrap}.filelink{width:auto}.filterblock{display:flex;gap:15px;flex-wrap:wrap}}
@media(max-width:650px){.reviewcell:has(> .commentadd:only-child){display:block}.diffhead .fileactions{flex-shrink:1}}

/* Warm charcoal and vivid code colours, inspired by Vibrant Ink. */
:root{color-scheme:light}
:root[data-theme="dark"]{color-scheme:dark;--bg:#17191b;--white:#222426;--ink:#eeeae3;--muted:#b3b2ad;--stroke:#42464a;--stroke2:#34383c;--brand:#a8ceff;--green:#a8e978;--red:#ff9a8a;--greenbg:#203323;--redbg:#392427;--greenstrong:#38562b;--redstrong:#633539;--bluebg:#27323f}
[data-theme="dark"] .topbar,[data-theme="dark"] .summary,[data-theme="dark"] .diffcard,[data-theme="dark"] .exportpanel,[data-theme="dark"] .filterblock{background:var(--white);border-color:var(--stroke)}
[data-theme="dark"] .diffhead,[data-theme="dark"] .linesheading{background:#292c2f;border-color:var(--stroke);color:var(--muted)}
[data-theme="dark"] .diffrow.ctx{background:#1c1e20}
[data-theme="dark"] .codecell{color:#f5f2e9}
[data-theme="dark"] .add .codecell,[data-theme="dark"] .add .sign,[data-theme="dark"] .plus{color:var(--green)}
[data-theme="dark"] .del .codecell,[data-theme="dark"] .del .sign,[data-theme="dark"] .minus{color:var(--red)}
[data-theme="dark"] .diffrow,[data-theme="dark"] .ln{border-color:#383c3f}
[data-theme="dark"] .ctx .ln{background:#24272a;color:#b6b8b9}
[data-theme="dark"] .add .ln{background:#263c29;color:#b2d39e}
[data-theme="dark"] .del .ln{background:#442c30;color:#e6b4ae}
[data-theme="dark"] .ln:hover{background:#414b58;color:#fff}
[data-theme="dark"] .reviewcell,[data-theme="dark"] .hunk .annotation{background:#222426;border-color:var(--stroke)}
[data-theme="dark"] .hunk,[data-theme="dark"] .wholelabel{background:#26303b;color:#b5cbe5;border-color:#414d5c}
[data-theme="dark"] .commentcard,[data-theme="dark"] .composer{background:#29313a;border-color:#4a627c}
[data-theme="dark"] .commentcard p,[data-theme="dark"] .commenttop strong{color:#eeeae3}
[data-theme="dark"] .commenttop small,[data-theme="dark"] .commentdelete{color:#bec8d2}
[data-theme="dark"] .exportbox,[data-theme="dark"] .composer textarea{background:#191d21;color:#f5f2e9;border-color:#4c555e}
[data-theme="dark"] .btn,[data-theme="dark"] .smallbtn,[data-theme="dark"] .fileicon,[data-theme="dark"] .sidefoot kbd{background:#30353a;color:#eeeae3;border-color:#59616a}
[data-theme="dark"] .btn:hover,[data-theme="dark"] .smallbtn:hover,[data-theme="dark"] .filelink:hover{background:#3a424b}
[data-theme="dark"] .btn.primary,[data-theme="dark"] .smallbtn.save{background:#3c5e83;border-color:#6f95bd;color:white}
[data-theme="dark"] .metric,[data-theme="dark"] .subtitle,[data-theme="dark"] .exporthint,[data-theme="dark"] .exportfoot,[data-theme="dark"] .sidefoot,[data-theme="dark"] .endnote,[data-theme="dark"] .status,[data-theme="dark"] .brandcopy small,[data-theme="dark"] .filecount,[data-theme="dark"] .filtercount,[data-theme="dark"] .filterempty,[data-theme="dark"] .sideheading,[data-theme="dark"] .filtertitle,[data-theme="dark"] .exportcount{color:var(--muted)}
[data-theme="dark"] .metric strong,[data-theme="dark"] .toolbar h2,[data-theme="dark"] .exporthead h2,[data-theme="dark"] .sidefoot strong,[data-theme="dark"] .filteritem,[data-theme="dark"] .filelink,[data-theme="dark"] .endnote strong{color:var(--ink)}
[data-theme="dark"] .eyebrow,[data-theme="dark"] .toggle,[data-theme="dark"] .fileview{color:#a8ceff}
[data-theme="dark"] .metric strong.plus{color:var(--green)}
[data-theme="dark"] .metric strong.minus{color:var(--red)}
[data-theme="dark"] .commentadd{color:#a8ceff}
[data-theme="dark"] .commentadd:hover{background:#3c5e83;color:white}


.toolbarcontrols{display:flex;align-items:center;justify-content:flex-end;gap:14px;flex-wrap:wrap}
.wrapcontrol{display:inline-flex;align-items:center;gap:7px;padding:6px 10px;border:1px solid var(--stroke);border-radius:7px;background:var(--white);color:var(--ink);font-size:12px;cursor:pointer;white-space:nowrap}
.wrapcontrol input{margin:0;accent-color:var(--brand)}
.wrapcontrol:has(input:checked){border-color:var(--brand)}
.wrap-lines .diffrow .codecell{white-space:pre-wrap;overflow:visible}
.wrap-lines .codecell .src{white-space:pre-wrap;overflow-wrap:anywhere;flex:1;min-width:0}
</style>
</head>
<body>
<div class="shell">
<div class="topbar"><div class="brand"><div class="logo">&lt;/&gt;</div><div class="brandcopy"><strong>diff-review</strong><small>Local HTML review</small></div></div><div class="headright"><button id="themeToggle" class="btn" type="button" aria-pressed="false">Dark theme</button><span class="status"><i></i>Offline · no server</span></div></div>
<div class="layout">
<aside class="sidebar">
<div class="sideheading" id="typeHeading">File types</div><div id="typeFilters" class="filterblock"></div>
<div class="sideheading">Changed files</div><nav id="fileNav" aria-label="Changed files"></nav>
<div class="sidefoot"><strong>How to review</strong>Hover over a line and click <kbd>+</kbd> to add a comment. Use <strong>Show whole file</strong> for small files. At the bottom, inspect the complete list of comments and click <strong>Export comments</strong> to copy them to your clipboard.</div>
</aside>
<main>
<header class="pagehead"><div><p class="eyebrow">Local code review</p><h1>Review changes</h1><p class="subtitle" id="comparison"></p></div></header>
<div id="summary" class="summary"></div>
<div class="toolbar"><h2>Unified diff <span id="contextLabel" style="font-weight:400;color:#8b96a3"></span></h2><div class="toolbarcontrols"><label id="wrapControl" class="wrapcontrol" hidden><input id="wrapToggle" type="checkbox">Wrap long lines</label><button id="contextToggle" class="toggle" type="button">Hide unchanged lines</button></div></div>
<div id="diffRoot"></div><div id="unmatched"></div>
<section id="reviewSummary" class="exportpanel" aria-labelledby="reviewSummaryTitle">
  <div class="exporthead">
    <div><p class="eyebrow">Ready for Codex</p><h2 id="reviewSummaryTitle">All review comments <span id="exportCount" class="exportcount"></span></h2></div>
    <button id="exportBtn" class="btn primary" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4h11v13M5 8h10v12H5z"/></svg>Export comments</button>
  </div>
  <p class="exporthint">Includes comments by Codex and you, even for files hidden by the filters. The button copies the exact text below to the clipboard.</p>
  <textarea id="commentsBox" class="exportbox" readonly spellcheck="false" aria-label="All review comments"></textarea>
  <p class="exportfoot">Comments you add or delete are held in this browser tab only. Copy them before closing or refreshing the page.</p>
</section>
<p class="endnote"><strong>Works offline.</strong> No server or file-writing facilities.</p>
</main></div></div><div class="toast" id="toast" role="status"></div>
<script id="reviewData" type="application/json">__REVIEW_DATA__</script>
<script>
const review=JSON.parse(document.getElementById('reviewData').textContent);
const files=review.files;
let comments=review.comments, nextCommentId=comments.length+1;
let composer=null, hideContext=false, wrapLines=false;
const wholeVisible=new Set(), visibleTypes=new Set(files.map(f=>f.file_type));
const $=id=>document.getElementById(id);
function setTheme(dark){
 document.documentElement.dataset.theme=dark?'dark':'light';
 $('themeToggle').textContent=dark?'Light theme':'Dark theme';
 $('themeToggle').setAttribute('aria-pressed',String(dark));
}
setTheme(window.matchMedia('(prefers-color-scheme: dark)').matches);
$('themeToggle').addEventListener('click',()=>setTheme(document.documentElement.dataset.theme!=='dark'));
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const rows=f=>f.hunks.flatMap(h=>h.rows);
const anchor=(f,side,line)=>`${f.id}:${side}:${line}`;
const same=(c,f,side,line)=>c.matched&&c.path===f.path&&c.side===side&&c.line===line;
files.forEach((f,i)=>{f.id=`f${i}`});
function locate(id){
 const [file,side,number]=id.split(':');
 const f=files.find(x=>x.id===file), line=Number(number);
 return f&&{f,side,line};
}
function markupComment(c){
 return `<div class="commentcard"><div class="commenttop"><span><span class="avatar">${esc(c.author.slice(0,1))}</span><strong>${esc(c.author)}</strong> <small>· ${esc(c.side)} ${esc(c.line)}</small></span><button type="button" class="commentdelete" aria-label="Delete comment" data-delete="${c.id}">×</button></div><p>${esc(c.body)}</p></div>`;
}
function markupComposer(id){return `<form class="composer" data-form="${id}"><textarea name="body" aria-label="Review comment" placeholder="Leave a review comment…" required></textarea><div class="composeractions"><button type="button" data-cancel="1" class="smallbtn">Cancel</button><button type="submit" class="smallbtn save">Add comment</button></div></form>`;}
function codeMarkup(r){
 // Python offsets count Unicode code points; JS string offsets count UTF-16.
 const chars=Array.from(r.source);
 let end=0, text='';
 for(const [a,b] of r.highlights||[]){text+=esc(chars.slice(end,a).join(''))+'<span class="intra">'+esc(chars.slice(a,b).join(''))+'</span>';end=b;}
 return text+esc(chars.slice(end).join(''));
}
function diffRow(f,r,whole=false){
 const sides=[];
 if(r.old_line!=null)sides.push(['old',r.old_line]);
 if(r.new_line!=null)sides.push(['new',r.new_line]);
 const notes=comments.filter(c=>sides.some(([s,n])=>same(c,f,s,n)));
 const composing=sides.map(([s,n])=>anchor(f,s,n)).find(id=>id===composer);
 if(!whole&&hideContext&&r.kind==='context'&&!notes.length&&!composing)return '';
 const side=r.new_line!=null?'new':'old', line=r.new_line??r.old_line;
 const id=anchor(f,side,line), css={context:'ctx',add:'add',delete:'del'}[r.kind];
 const gutter=(s,n)=>n==null?'<div class="ln"></div>':`<button type="button" class="ln" data-compose="${anchor(f,s,n)}" title="Comment on ${s} line ${n}">${n}</button>`;
 return `<div class="diffrow ${css}">${gutter('old',r.old_line)}${gutter('new',r.new_line)}<div class="codecell"><span class="sign">${r.kind==='add'?'+':r.kind==='delete'?'−':' '}</span><span class="src">${codeMarkup(r)}${r.no_newline?'<small class="newline"> [No newline at end of file]</small>':''}</span></div><div class="reviewcell">${notes.map(markupComment).join('')}${composing?markupComposer(composing):`<button type="button" class="commentadd" data-compose="${id}" aria-label="Comment on ${side} line ${line}">+</button>`}</div></div>`;
}
function fullRows(f){
 const side=f.whole_side;
 return f.whole_file.map((source,i)=>{
  const number=i+1, original=rows(f).find(r=>r[side+'_line']===number);
  const row={kind:original?.kind||'context',source,old_line:null,new_line:null,highlights:original?.source===source?original.highlights:undefined};
  row[side+'_line']=number;
  row.no_newline=i===f.whole_file.length-1&&f.whole_no_newline;
  return diffRow(f,row,true);
 }).join('');
}
function formatComments(){
 if(!comments.length)return 'No review comments yet.';
 return [...comments].sort((a,b)=>a.path.localeCompare(b.path)||(Number(a.line)||0)-(Number(b.line)||0)||String(a.side).localeCompare(String(b.side)))
 .map(c=>`File: ${c.path}\nLine: ${c.side??'unknown'} line ${c.line??'unknown'}${c.matched?'':' [unmatched anchor]'}\nAuthor: ${c.author}\nComment: ${c.body}`).join('\n\n---\n\n');
}
function fileNav(){
 const types=[...new Set(files.map(f=>f.file_type))].sort();
 $('typeFilters').hidden=types.length<2;
 $('typeHeading').hidden=types.length<2;
 $('typeFilters').innerHTML=types.map(t=>`<label class="filteritem"><input type="checkbox" data-type="${esc(t)}" ${visibleTypes.has(t)?'checked':''}><span>${esc(t)}</span><span class="filtercount">${files.filter(f=>f.file_type===t).length}</span></label>`).join('');
 $('fileNav').innerHTML=files.filter(f=>visibleTypes.has(f.file_type)).map(f=>`<button type="button" class="filelink" data-file="${f.id}"><span class="fileicon">${esc(f.file_type.slice(0,2))}</span><span><span class="filename">${esc(f.path)}</span><span class="filecount"><span class="plus">+${f.adds}</span> <span class="minus">−${f.deletes}</span> · ${comments.filter(c=>c.path===f.path).length} comment(s)</span></span></button>`).join('');
}
function updateWrapToggle(){
 // Measure unwrapped code, then restore the selected layout.
 const root=$('diffRoot');
 root.classList.remove('wrap-lines');
 const overflow=[...root.querySelectorAll('.codecell')].some(cell=>cell.scrollWidth>cell.clientWidth+1);
 root.classList.toggle('wrap-lines',wrapLines);
 $('wrapControl').hidden=!overflow&&!wrapLines;
 $('wrapToggle').checked=wrapLines;
}
$('wrapToggle').addEventListener('change',e=>{wrapLines=e.target.checked;updateWrapToggle();});
window.addEventListener('resize',()=>requestAnimationFrame(updateWrapToggle));
function render(){
 const active=files.filter(f=>visibleTypes.has(f.file_type));
 $('comparison').textContent=review.comparison;
 $('contextLabel').textContent=`/ context: ${review.context} lines`;
 $('summary').innerHTML=`<span class="metric"><strong>${files.length}</strong> files changed</span><span class="metric"><strong class="plus">+${files.reduce((n,f)=>n+f.adds,0)}</strong> additions</span><span class="metric"><strong class="minus">−${files.reduce((n,f)=>n+f.deletes,0)}</strong> deletions</span><span class="metric"><strong>${comments.length}</strong> review comments</span><span class="metric">Showing ${active.length} of ${files.length}</span>`;
 $('exportCount').textContent=`(${comments.length})`;
 $('commentsBox').value=formatComments();
 $('contextToggle').textContent=hideContext?'Show unchanged lines':'Hide unchanged lines';
 $('diffRoot').innerHTML=active.map(f=>{
  const expanded=wholeVisible.has(f.id);
  const button=f.whole_file_available?`<button type="button" class="fileview" data-expand="${f.id}">${expanded?'Back to diff':'Show whole file'}</button>`:`<span class="previewreason">${esc(f.whole_reason)}</span>`;
  const metadata=f.metadata.length?`<div class="wholelabel">${f.metadata.map(esc).join('<br>')}</div>`:'';
  const body=expanded?`<div class="wholelabel">Entire ${f.whole_side==='old'?'old file (deleted version)':'new file'} · ${f.whole_lines} lines. ${f.whole_side==='new'?'Deletion-side comments remain in diff mode.':''}</div>${fullRows(f)}`:f.hunks.map(h=>`<div class="hunk"><span class="label">${esc(h.header)}</span><div class="annotation"></div></div>${h.rows.map(r=>diffRow(f,r)).join('')}`).join('');
  return `<section class="diffcard" id="${f.id}"><header class="diffhead"><span class="path">${esc(f.path)} <small>(${esc(f.kind)})</small></span><span class="fileactions">${button}<span class="counts"><span class="plus">+${f.adds}</span> <span class="minus">−${f.deletes}</span></span></span></header>${metadata}<div class="linesheading"><span class="guttertitle">− / +</span><span class="codetitle">${expanded?'Whole file':'Code changes'}</span><span class="reviewtitle">Margin comments</span></div>${body||'<div class="wholelabel">No textual changes.</div>'}</section>`;
 }).join('')||`<div class="filterempty">${files.length?'Select a file type to display changes.':'No changes to review.'}</div>`;
 // Notes whose anchors are unavailable remain visible and removable.
 const unmatched=comments.filter(c=>!c.matched);
 $('unmatched').innerHTML=unmatched.length?'<h3>Unmatched imported comments</h3>'+unmatched.map(c=>`<p>${esc(c.path)} — anchor unavailable</p>${markupComment(c)}`).join(''):'';
 fileNav();
 updateWrapToggle();
}
function toast(message){const el=$('toast');el.textContent=message;el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),4000);}
document.addEventListener('click',e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.file)$(b.dataset.file)?.scrollIntoView({behavior:'smooth',block:'start'});
 if(b.dataset.expand){wholeVisible.has(b.dataset.expand)?wholeVisible.delete(b.dataset.expand):wholeVisible.add(b.dataset.expand);composer=null;render();}
 if(b.dataset.compose){composer=b.dataset.compose;render();document.querySelector('form.composer textarea')?.focus();}
 if(b.dataset.cancel){composer=null;render();}
 if(b.dataset.delete){comments=comments.filter(c=>c.id!==b.dataset.delete);render();toast('Comment removed');}
});
document.addEventListener('change',e=>{const x=e.target;if(!x.matches('[data-type]'))return;x.checked?visibleTypes.add(x.dataset.type):visibleTypes.delete(x.dataset.type);composer=null;render();});
document.addEventListener('submit',e=>{
 const form=e.target.closest('form[data-form]');if(!form)return;e.preventDefault();
 const body=form.querySelector('textarea').value.trim(), pos=locate(form.dataset.form);
 if(!body||!pos)return;
 comments.push({id:`c${nextCommentId++}`,author:'You',path:pos.f.path,side:pos.side,line:pos.line,body,matched:true});
 composer=null;render();toast('Comment added');
});
$('contextToggle').addEventListener('click',()=>{hideContext=!hideContext;render();});
$('exportBtn').addEventListener('click',async()=>{
 const box=$('commentsBox'), value=box.value;
 try{
  if(!navigator.clipboard?.writeText)throw new Error('Clipboard API unavailable');
  await navigator.clipboard.writeText(value);toast('Comments copied to clipboard');
 }catch(error){
  box.focus();box.select();
  let copied=false;
  try{copied=typeof document.execCommand==='function'&&document.execCommand('copy');}catch(error){}
  toast(copied?'Comments copied to clipboard':'Automatic copying blocked. The text is selected: press Ctrl+C to copy.');
 }
});
render();

</script>
</body>
</html>
'''


if __name__ == "__main__":
    main()
