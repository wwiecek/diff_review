# `diff-review`: standalone Git-to-HTML review helper

**Implementation specification for Codex — 15 October 2026 (revised)**

## 1. Objective and boundaries

Implement **`diff-review`**, a small, standalone command-line Python script that turns normal Git diffs into a polished **single self-contained HTML file** for local code review. The viewer lets the user inspect diffs, expand eligible files to their entire contents, filter by file type, and attach margin comments. Comments have an author, usually `Codex` or `You`.

**The complete working HTML/CSS/JavaScript example is included in Appendix A**, and separately as `diff-review-style-sample.html`. Reuse its visual design: restrained GitHub-style white cards, red and green change highlighting, monospaced code, sidebar, file-type checkboxes, and margin comments. Adapt the illustrative data to actual Git output rather than redesigning the page.

The workflow is deliberately simple:

1. Run `diff-review` in a Git repository, or point it at an existing Git patch. The script generates `diff-review.html` in the current directory (unless `--output` specifies another path) and opens it in the default browser.
2. Read diffs; optionally show entire small files. Add or remove line comments directly in the browser. Comments supplied by Codex at generation time appear alongside the user's comments.
3. At the **bottom of the page**, a text box continuously lists **every comment**, with **file path, old/new line number, author and comment text**. Click **Export comments** to copy the exact contents of that box to the clipboard, then paste them into Codex.

**No local server, saving API, rewriting HTML, Markdown file generation, browser downloads, database, external JavaScript or installation dependencies.** Only the Python script creates the initial HTML. After that, the HTML operates offline, even when opened as `file://`. Initial comments are embedded in the generated HTML; comments added in the browser remain in memory and are **lost if the page is reloaded or closed**. State that briefly near the export box. Clipboard export is the persistence mechanism the user wants.

## 2. Command-line interface

Support these commands:

```bash
diff-review                               # All changes: staged + unstaged + untracked
diff-review --commit abc123               # A commit versus its first parent
diff-review --between main feature        # Direct comparison of two Git revisions
diff-review --base main                   # The given revision versus the working tree
diff-review --patch changes.diff          # Read a normal Git unified diff
git diff main HEAD | diff-review --patch -
diff-review --output ./reviews/review.html
diff-review --context 5
diff-review --no-untracked
diff-review --no-open
diff-review --comments codex-comments.json  # Optional initial Codex comments
```

- Default tracked-file comparison is **`git diff HEAD`**, including staged and unstaged edits together, without double-counting. Add untracked regular text files from `git ls-files --others --exclude-standard -z` as new files without staging them. Respect Git ignore rules. For a repository without a `HEAD`, use an empty-tree baseline.
- `--commit REV` uses the first parent, or the empty tree for a root commit. `--between A B` means `git diff A B`, not a merge-base comparison. `--base REV` compares that revision with the current working tree. `--patch PATH` parses a conventional multi-file Git unified patch; `-` reads standard input.
- Default output is `./diff-review.html` relative to the invocation directory. `--output` may change it; create necessary directories. Open the generated file in the default browser with `webbrowser.open(Path(output).resolve().as_uri())`, unless `--no-open` is supplied. Print the file path and exit. **No long-running process**.
- Provide the executable command as a Python shebang script `diff-review`. It should also be runnable as `python diff_review.py` on Windows. Standard library only. Use `subprocess.run` with `shell=False`. Provide clear errors for invalid revisions, invalid patches or wrong working directory.
- Generate a clear no-changes page when there is no diff. Do not alter the index or repository.

## 3. Git diff parsing

Use Git's normal patch output, including rename detection, without colour, text conversions or external diff helpers. For example:

```bash
git diff HEAD --no-ext-diff --no-textconv --no-color --find-renames --unified=3 --
```

Parse file headers, hunk headers and rows. Preserve exact old and new line numbers over multiple hunks, zero-length ranges and absent trailing newlines. Track `old` versus `new` for comments on deletions and additions. Show hunk dividers. Always HTML-escape file paths, code and comment bodies. Display binary files, mode-only changes and submodules as short metadata cards; don't pretend to have textual contents. Support added, modified, deleted and renamed files, and paths containing spaces. Prefer UTF-8, with replacement on malformed characters. Truncation of large patches must be explicitly indicated.

**Whole-file previews:** use the current on-disk file in worktree modes, but the correct **new-side Git blob** for `--commit` and `--between` (and, if relevant, `--base` where it references a worktree). Deletions may show the old file if labelled clearly. A patch alone does not normally include unchanged lines; disable whole-file mode unless the complete corresponding file is known and verified.

## 4. HTML presentation and interaction

- **Header:** tool name, what was compared, files changed, addition/deletion totals and comment count.
- **Sidebar:** changed paths and counts. Display file-type checkboxes when at least two file types occur. Initially show all. Hiding a type hides its cards and navigation links **without affecting comments or their export**.
- **Diff:** unified view with red removals, green additions, highlighted changed text where possible, old/new line gutters, hunk boundaries, a margin for notes, and controls to hide/show unchanged context. Long code lines should scroll horizontally.
- **Whole file:** `Show whole file` / `Back to diff` on each small text file, preserving correct line numbers and comment anchors. Allow comments on unchanged lines shown only in whole-file view. Deletion-side comments remain accessible in diff mode.
- **Large files:** do not embed a whole-file preview for a file with **more than 1,000 lines or 200 KiB** (configurable constants). Explain that the complete preview is unavailable while keeping changed hunks. Omit full contents for binary files.
- **Margin comments:** hover and click `+` on any eligible line, type a note and add it. Show author (`Codex` / `You`) and content. Permit deleting a comment; editing is optional. Each anchor identifies file, side and line number, not merely a row position. Do not need threads, approvals or collaborative accounts.
- **Bottom comments box:** a visible, read-only multiline `<textarea>` beneath all file cards. Every addition/deletion of a comment immediately regenerates its text. Include all comments (including hidden file types), with at least these fields: file, line number with `old`/`new` distinction, author, and comment body. Order by file and then by location, or by visual review order. When there are none, say `No review comments yet.`
- **Export comments:** one button beside the box heading, labelled **Export comments**, that copies `textarea.value` **verbatim** into the clipboard and confirms success. Try `navigator.clipboard.writeText` first; provide a fallback using `textarea.select()` and `document.execCommand('copy')` for browsers restricting the Clipboard API on `file://`. If automatic copying is blocked, leave the box selectable for manual Ctrl+C and explain what happened. Do not create or download a file.

All assets must be embedded in the HTML. No HTTP calls, fonts, dependencies, bundlers or JavaScript frameworks.

## 5. Comment input and representation

An optional `--comments codex-comments.json` imports comments at HTML generation time. A minimal input format is:

```json
[
  {
    "author": "Codex",
    "path": "R/prepare_ma.R",
    "side": "new",
    "line": 18,
    "body": "Please test the behaviour with zero event counts."
  }
]
```

The Python script embeds imported comments directly in the initial HTML's serialised review data (`<script type="application/json">` or a safely escaped JavaScript object). **This is initial data, not a mechanism for browser edits to rewrite the HTML.** Encode dangerous sequences such as `<` and `</script>` safely. Validate imported anchors; show unmatched notes in the bottom comments box with a clear label rather than dropping them. Comments created in the browser use author `You`. Editing the exported text box itself is unnecessary: users add and remove comments using the corresponding margin controls.

A suggested serialisable review object:

```json
{
  "comparison": "working tree vs HEAD",
  "files": [{
    "path": "R/prepare_ma.R",
    "kind": "modified",
    "file_type": "R",
    "adds": 12,
    "deletes": 4,
    "hunks": [{"old_start": 9, "new_start": 9, "rows": []}],
    "whole_file": ["line 1", "line 2"],
    "whole_file_available": true
  }],
  "comments": [{
    "id": "c1", "author": "Codex", "path": "R/prepare_ma.R",
    "side": "new", "line": 18, "body": "Check the standard error."
  }]
}
```

A row should have `kind` (`context`, `add`, `delete`), `source`, `old_line` and/or `new_line`. The browser holds the comment array in memory, updates the margin cards and read-only export box on each change, and copies the assembled text on request. **No `localStorage`, saving, automatic synchronisation or regeneration of previous session comments is required.**

## 6. Acceptance tests

Build a few simple standard-library tests in temporary Git repositories, plus browser checks:

1. Staged and unstaged changes to the same tracked file, plus an untracked file, appear once each in the default comparison.
2. `--commit`, `--between`, `--base`, and `--patch` display the expected changes. Multi-hunk patches and spaces in paths preserve correct line numbers.
3. File-type checkboxes hide/show the right cards without losing comments.
4. Whole-file toggle works for small files; unavailable for files above either threshold. Notes on unchanged full-file lines can be exported.
5. Imported Codex notes and browser-added You notes appear in the margin and **bottom summary box** with correct file and line numbers.
6. Adding or deleting a note updates the bottom box immediately; the summary includes comments on hidden file types and deletion-side lines.
7. Clicking **Export comments** copies precisely the visible summary-box text. Test a locally opened `file://` page, with a fallback if the Clipboard API is unavailable. **No files are downloaded or modified.**
8. The HTML is portable and fully usable offline. Reloading the page returns to the initially embedded comment set; show the user a short warning about unsaved browser edits near the box.
9. Escape malicious-looking code/comment content so it renders as plain text rather than executing.

**Out of scope:** server, browser write permissions, persisted user comments, database, Markdown generation, downloads, Git staging/commits, IDE integration, side-by-side view, comment threads or Codex API calls. Maintain the sample's appearance and keep the code easy to inspect.

---

## Appendix A. Complete HTML style and interaction reference

This is the **full source** of `diff-review-style-sample.html`, included so Codex can reproduce the visual style and the bottom-of-page clipboard workflow exactly. This remains illustrative data, not a Git parser.

~~~~html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>diff-review — HTML diff sample</title>
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
</style>
</head>
<body>
<div class="shell">
<div class="topbar"><div class="brand"><div class="logo">&lt;/&gt;</div><div class="brandcopy"><strong>diff-review</strong><small>Local HTML review · style reference</small></div></div><div class="headright"><span class="status"><i></i>Illustrative changes · offline</span></div></div>
<div class="layout">
<aside class="sidebar">
<div class="sideheading">File types</div><div id="typeFilters" class="filterblock"></div>
<div class="sideheading">Changed files</div><nav id="fileNav" aria-label="Changed files"></nav>
<div class="sidefoot"><strong>How to review</strong>Hover over a line and click <kbd>+</kbd> to add a comment. Use <strong>Show whole file</strong> for small files. At the bottom, inspect the complete list of comments and click <strong>Export comments</strong> to copy them to your clipboard.</div>
</aside>
<main>
<header class="pagehead"><div><p class="eyebrow">Design sample for the proposed CLI</p><h1>Review changes</h1><p class="subtitle"><code>diff-review</code> · staged + unstaged · sample branch · five files</p></div></header>
<div id="summary" class="summary"></div>
<div class="toolbar"><h2>Unified diff <span style="font-weight:400;color:#8b96a3">/ context: 3 lines</span></h2><button id="contextToggle" class="toggle" type="button">Hide unchanged lines</button></div>
<div id="diffRoot"></div>
<section id="reviewSummary" class="exportpanel" aria-labelledby="reviewSummaryTitle">
  <div class="exporthead">
    <div><p class="eyebrow">Ready for Codex</p><h2 id="reviewSummaryTitle">All review comments <span id="exportCount" class="exportcount"></span></h2></div>
    <button id="exportBtn" class="btn primary" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4h11v13M5 8h10v12H5z"/></svg>Export comments</button>
  </div>
  <p class="exporthint">Includes comments by Codex and you, even for files hidden by the filters. The button copies the exact text below to the clipboard.</p>
  <textarea id="commentsBox" class="exportbox" readonly spellcheck="false" aria-label="All review comments"></textarea>
  <p class="exportfoot">Comments you add or delete are held in this browser tab only. Copy them before closing or refreshing the page.</p>
</section>
<p class="endnote">Design demonstration with illustrative data. <strong>Works offline.</strong> No server or file-writing facilities.</p>
</main></div></div><div class="toast" id="toast" role="status"></div>
<script>
// This is a deliberately small browser demo, not the production Git parser.
const files = [
  {id:'f1',path:'R/prepare_ma.R',oldStart:9,newStart:9,rows:[
    ['ctx','prepare_ma <- function(data, effect = "OR") {'],
    ['del','  if (effect == "OR") {'],
    ['del','    estimate <- log((a * d) / (b * c))'],
    ['del','    se <- sqrt(1 / a + 1 / b + 1 / c + 1 / d)'],
    ['del','  }'],
    ['add','  effect <- match.arg(effect, c("OR", "RR", "RD"))',[17,52]],
    ['add','  p_t <- with(data, events_t / total_t)'],
    ['add','  p_c <- with(data, events_c / total_c)'],
    ['add','  if (effect == "OR") {'],
    ['add','    estimate <- qlogis(p_t) - qlogis(p_c)'],
    ['add','  } else if (effect == "RR") {',[22,26]],
    ['add','    estimate <- log(p_t) - log(p_c)'],
    ['add','  } else {'],
    ['add','    estimate <- p_t - p_c'],
    ['add','  }'],
    ['ctx','  tibble::tibble(estimate = estimate, se = se)'],
    ['ctx','}']
  ],before:['# Binary effect preparation','','# This example illustrates three effect measures.','','library(tibble)','','# Helpers return study-level outcomes.',''],after:['','# More conversion routines can go here.']},
  {id:'f2',path:'R/baggr.R',oldStart:7,newStart:7,rows:[
    ['ctx',"#' @param data A data frame with study-level outcomes."],
    ['del',"#' @param effect_label Label shown in summaries and plots."],
    ['add',"#' @param effect_label Effect measure for binary data (OR, RR, RD).",[24,56]],
    ['add',"#'   For other models, used only in summaries and plots."],
    ['ctx',"#' @param model Model family to fit."],
    ['ctx',"#' @export"],
    ['ctx','baggr <- function(data, model = "rubin", effect_label = "OR") {'],
    ['del','  prepared <- prepare_ma(data)'],
    ['add','  prepared <- prepare_ma(data, effect = effect_label)',[14,57]],
    ['ctx','  fit_model(prepared, model)'],
    ['ctx','}']
  ],before:['# Model front end','','# Simplified demonstration for code review.','','# Main modelling interface',''],after:['','# End of demo source file']},
  {id:'f3',path:'tests/testthat/test-prepare-ma.R',oldStart:4,newStart:4,rows:[
    ['ctx','test_that("effect measures are transformed correctly", {'],
    ['add','  x <- tibble::tibble('],
    ['add','    events_t = c(10, 20), total_t = c(100, 100),'],
    ['add','    events_c = c(5, 10), total_c = c(100, 100)'],
    ['add','  )'],
    ['add','  expect_equal(prepare_ma(x, "RR")$estimate, log(c(2, 2)))'],
    ['add','  expect_equal(prepare_ma(x, "RD")$estimate, c(0.05, 0.10))'],
    ['ctx','})']
  ],before:['library(testthat)','','# Binary measures'],after:['','# Additional regression checks follow.']},
  {id:'f4',path:'README.md',oldStart:5,newStart:5,rows:[
    ['ctx','## Supported effects'],
    ['del','The binary model reports odds ratios.'],
    ['add','The binary model supports odds ratios, risk ratios and risk differences.'],
    ['add','Choose the measure with `effect_label` when preparing count data.'],
    ['ctx',''],
    ['ctx','See the binary vignette for a worked example.']
  ],before:['# baggr','','An R package for Bayesian meta-analysis.',''],after:['','## Installation','','See the package website.']},
  {id:'f5',path:'data/example-registry.csv',oldStart:118,newStart:118,wholeLength:2860,wholeBytes:245000,rows:[
    ['ctx','study,year,n,estimate'],
    ['del','trial_117,2023,84,0.16'],
    ['add','trial_117,2023,84,0.14'],
    ['ctx','trial_118,2024,96,0.08']
  ]}
];
for(const f of files){
 let old=f.oldStart,neu=f.newStart;
 f.rows=f.rows.map((r,i)=>{const row={type:r[0],source:r[1],highlight:r[2],index:i,id:f.id+':'+i};row.old=row.type==='add'?null:old++;row.new=row.type==='del'?null:neu++;return row});
 f.adds=f.rows.filter(r=>r.type==='add').length;
 f.dels=f.rows.filter(r=>r.type==='del').length;
 f.type=(f.path.match(/\.([^.\/]+)$/)||[])[1]?.toUpperCase()||'OTHER';
 if(!f.wholeLength){f.whole=[...(f.before||[]),...f.rows.filter(r=>r.type!=='del').map(r=>r.source),...(f.after||[])];f.wholeLength=f.whole.length;}
}
let comments=[
 {id:1,author:'Codex',file:'f1',row:'f1:15',body:'The previous SE calculation has disappeared, but `se` is still referenced. Add formulas for RR and RD.'},
 {id:2,author:'You',file:'f3',row:'f3:5',body:'Please add a test for zero event counts and state the continuity-correction policy.'},
 {id:3,author:'Codex',file:'f4',row:'f4:2',body:'Documentation should also explain whether this choice changes preprocessing or just labels.'}
];
let nextCommentId=4,composer=null,selected=null,hideContext=false;
const wholeVisible=new Set(), visibleTypes=new Set(files.map(f=>f.type));
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const activeFiles=()=>files.filter(f=>visibleTypes.has(f.type));
const smallFile=f=>f.whole&&f.wholeLength<=1000&&(f.wholeBytes||0)<=204800;
function codeMarkup(r){const s=r.source;if(!r.highlight)return esc(s);const[a,b]=r.highlight;return esc(s.slice(0,a))+'<span class="intra">'+esc(s.slice(a,b))+'</span>'+esc(s.slice(b));}
function fileNav(){
 const types=[...new Set(files.map(f=>f.type))].sort();
 $('typeFilters').innerHTML=types.map(t=>`<label class="filteritem"><input type="checkbox" data-type="${esc(t)}" ${visibleTypes.has(t)?'checked':''}/><span>${esc(t)}</span><span class="filtercount">${files.filter(f=>f.type===t).length}</span></label>`).join('');
 $('fileNav').innerHTML=activeFiles().map(f=>`<button type="button" class="filelink" data-file="${f.id}"><span class="fileicon">${esc(f.type==='OTHER'?'·':f.type.slice(0,2))}</span><span><span class="filename">${esc(f.path)}</span><span class="filecount"><span class="plus">+${f.adds}</span> <span class="minus">−${f.dels}</span> · ${comments.filter(c=>c.file===f.id).length} comment(s)</span></span></button>`).join('')||'<p class="filterempty">No file types selected.</p>';
}
function markupComment(c){const codex=c.author==='Codex';return `<div class="commentcard"><div class="commenttop"><span><span class="avatar" style="background:${codex?'#7b7f96':'#7187b4'}">${codex?'C':'Y'}</span><strong>${esc(c.author)}</strong> <small>· ${codex?'imported':'draft'}</small></span><button type="button" class="commentdelete" title="Delete comment" aria-label="Delete comment" data-delete="${c.id}">×</button></div><p>${esc(c.body)}</p></div>`;}
function markupComposer(r){return `<form class="composer" data-form="${r.id}"><textarea name="body" aria-label="Review comment" placeholder="Leave a review comment…" required></textarea><div class="composeractions"><button type="button" data-cancel="${r.id}" class="smallbtn">Cancel</button><button type="submit" class="smallbtn save">Add comment</button></div></form>`;}
function diffRow(r){if(hideContext&&r.type==='ctx'&&!comments.some(c=>c.row===r.id)&&composer!==r.id)return '';
 const notes=comments.filter(c=>c.row===r.id),sign=r.type==='add'?'+':r.type==='del'?'−':' ';
 return `<div class="diffrow ${r.type} ${selected===r.id?'selected':''}"><div class="ln" data-select="${r.id}" title="Mark this line">${r.old??''}</div><div class="ln" data-select="${r.id}" title="Mark this line">${r.new??''}</div><div class="codecell"><span class="sign">${sign}</span><span class="src">${codeMarkup(r)}</span></div><div class="reviewcell">${notes.map(markupComment).join('')}${composer===r.id?markupComposer(r):`<button type="button" class="commentadd" title="Comment on this line" aria-label="Comment on line" data-compose="${r.id}">+</button>`}</div></div>`;
}
function fullFileRow(f,source,i){const num=i+1,r=f.rows.find(x=>x.new===num),id=r?r.id:`${f.id}:full:${num}`;
 const notes=comments.filter(c=>c.row===id),added=!!r&&r.type==='add';
 return `<div class="diffrow ${added?'add':'ctx'} ${selected===id?'selected':''}"><div class="ln"></div><div class="ln" data-select="${id}">${num}</div><div class="codecell"><span class="sign">${added?'+':' '}</span><span class="src">${esc(source)}</span></div><div class="reviewcell">${notes.map(markupComment).join('')}${composer===id?markupComposer({id}):`<button type="button" class="commentadd" data-compose="${id}" aria-label="Comment on line">+</button>`}</div></div>`;
}
function formatComments(){
 if(!comments.length)return 'No review comments yet.';
 const blocks=[];
 for(const f of files){
  for(const c of comments.filter(c=>c.file===f.id)){
   const pos=locateRow(c.row);
   const side=pos?.r?.new!=null?'new':'old';
   const number=pos?.r?.new??pos?.r?.old;
   const line=number!=null?`${side} line ${number}`:'line unavailable';
   blocks.push(`File: ${f.path}\nLine: ${line}\nAuthor: ${c.author}\nComment: ${c.body}`);
  }
 }
 return blocks.join('\n\n---\n\n');
}
function render(){
 const fs=activeFiles(),adds=files.reduce((t,f)=>t+f.adds,0),dels=files.reduce((t,f)=>t+f.dels,0);
 $('summary').innerHTML=`<span class="metric"><strong>${files.length}</strong> files changed</span><span class="metric"><strong class="plus">+${adds}</strong> additions</span><span class="metric"><strong class="minus">−${dels}</strong> deletions</span><span class="metric"><strong>${comments.length}</strong> review comments</span><span class="metric">Showing ${fs.length} of ${files.length}</span><span class="mini-bars" aria-hidden="true">${'<i></i>'.repeat(9)}${'<i class="r"></i>'.repeat(5)}</span>`;
 $('exportCount').textContent='('+comments.length+')';
 $('commentsBox').value=formatComments();
 $('exportBtn').disabled=comments.length===0;
 $('contextToggle').textContent=hideContext?'Show unchanged lines':'Hide unchanged lines';
 $('diffRoot').innerHTML=fs.map(f=>{
 const expanded=wholeVisible.has(f.id),available=smallFile(f),button=available?`<button type="button" class="fileview" data-expand="${f.id}">${expanded?'Back to diff':'Show whole file'}</button>`:`<button type="button" class="fileview" disabled title="Full-file preview omitted for large files">Full preview unavailable · ${f.wholeLength.toLocaleString()} lines</button>`;
 return `<section class="diffcard" id="${f.id}"><header class="diffhead"><span class="path"><span>↳</span> ${esc(f.path)}</span><span class="fileactions">${button}<span class="counts"><span class="plus">+${f.adds}</span> &nbsp; <span class="minus">−${f.dels}</span></span></span></header><div class="linesheading"><span class="guttertitle">− / +</span><span class="codetitle">${expanded?'Whole file (new version)':'Code changes'}</span><span class="reviewtitle">Margin comments</span></div>${expanded&&available?`<div class="wholelabel">Showing the entire new file · ${f.wholeLength} lines · changes in green</div><div class="full">${f.whole.map((src,i)=>fullFileRow(f,src,i)).join('')}</div>`:`<div class="hunk"><span class="label">@@ −${f.oldStart} +${f.newStart} @@</span><div class="annotation"></div></div>${f.rows.map(diffRow).join('')}`}</section>`;
 }).join('')||'<div class="filterempty">Select a file type in the sidebar to display changes.</div>';
 fileNav();
}
function locateRow(id){for(const f of files){const r=f.rows.find(x=>x.id===id);if(r)return {f,r};const prefix=f.id+':full:';if(id.startsWith(prefix)&&f.whole){const number=Number(id.slice(prefix.length));if(Number.isInteger(number)&&number>0&&number<=f.whole.length)return {f,r:{id,old:null,new:number,source:f.whole[number-1]}}}}return null;}
function toast(msg){const el=$('toast');el.textContent=msg;el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),3000);}
document.addEventListener('click',e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.file)document.getElementById(b.dataset.file)?.scrollIntoView({behavior:'smooth',block:'start'});
 if(b.dataset.expand){wholeVisible.has(b.dataset.expand)?wholeVisible.delete(b.dataset.expand):wholeVisible.add(b.dataset.expand);render();}
 if(b.dataset.compose){composer=b.dataset.compose;selected=composer;render();document.querySelector(`form[data-form="${composer}"] textarea`)?.focus();}
 if(b.dataset.cancel){composer=null;render();}
 if(b.dataset.select){selected=selected===b.dataset.select?null:b.dataset.select;render();}
 if(b.dataset.delete){comments=comments.filter(c=>c.id!==Number(b.dataset.delete));render();toast('Comment removed');}
});
document.addEventListener('change',e=>{const x=e.target;if(!x.matches('[data-type]'))return;x.checked?visibleTypes.add(x.dataset.type):visibleTypes.delete(x.dataset.type);render();});
document.addEventListener('submit',e=>{
 const form=e.target.closest('form[data-form]');if(!form)return;e.preventDefault();
 const note=form.querySelector('textarea').value.trim();if(!note)return;const pos=locateRow(form.dataset.form);if(!pos)return;
 comments.push({id:nextCommentId++,author:'You',file:pos.f.id,row:pos.r.id,body:note});composer=null;render();toast('Comment added');
});
$('contextToggle').addEventListener('click',()=>{hideContext=!hideContext;render()});
$('exportBtn').addEventListener('click',async()=>{
 const box=$('commentsBox');
 const value=box.value;
 if(!comments.length)return;
 try{
  if(!navigator.clipboard?.writeText)throw new Error('Clipboard API unavailable');
  await navigator.clipboard.writeText(value);
  toast('Comments copied to clipboard');
 }catch(e){
  // Works when the HTML is opened directly as file://, including without HTTPS.
  box.focus();box.select();
  const copied=typeof document.execCommand==='function'&&document.execCommand('copy');
  box.setSelectionRange(0,0);
  toast(copied?'Comments copied to clipboard':'Select and copy the text in the box manually');
 }
});
render();

</script>
</body>
</html>

~~~~
