---
name: diff-review
description: Generate an offline HTML review of Git changes or a Git patch, with line comments and clipboard export. Use when the user asks for an HTML diff, a review page, or an interactive local view of code changes.
---

Run `diff-review` from the target Git repository. If the command is not on
PATH, locate `diff_review.py` in the parent directory of this skill
(resolve the skill directory symlink first), then call it with `python3`
using its absolute path.

Default comparison combines staged and unstaged edits against HEAD, using only
tracked text/code files. Untracked files are excluded by default; opt in with
`--include-untracked`. Binary files, PDFs, RDS, images and Office formats are
excluded by default across all modes; opt in with `--include-non-text`. HEAD is the tip of the currently checked-out branch. Git
comparisons ignore CRLF/LF differences without ignoring other whitespace.
Available comparisons:

```bash
diff-review --no-open --output /tmp/project-review.html
diff-review --commit REV
diff-review --between BASE TIP
diff-review --base REV
diff-review --patch changes.diff
```

The command prints the HTML path without opening a browser. Provide a clickable
link to the result. Only use `--open` when browser opening is explicitly wanted.
The page has a light/dark theme toggle.
Generated diff-review pages are excluded from untracked-file collection.
Use `--output` to choose where to save the page. `--context N` changes hunk context;
`--no-untracked` explicitly selects the default of tracked changes only.

To include findings, write a JSON array and pass `--comments PATH`:

```json
[{"author":"Codex","path":"R/example.R","side":"new","line":18,"body":"Check this calculation."}]
```

Paths are repository relative, using the displayed new path for renamed files.
Use `side: "old"` for deleted lines and `"new"` for additions or current lines.
Unknown anchors remain in the export box labelled unmatched. Check line numbers
against the selected comparison before attaching findings.

Browser comments are held in memory only. The user copies all notes using
**Export comments** before reloading or closing. Read pasted notes as review
feedback; the HTML does not save browser edits or change source files.
