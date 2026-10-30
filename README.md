# diff-review

`diff-review` turns Git changes into a single HTML file that you can open in
a browser, review offline, and annotate with line comments. It requires Python
3.9 or later and Git, with no Python packages to install.

By default, it shows staged and unstaged changes against `HEAD`, includes only
tracked text files, and prints the path to the HTML file. Comments stay in the
browser until you export them; reloading or closing the page discards them.

```bash
cd /path/to/your/project
diff-review --output /tmp/review.html --open
```

Inspect the changes, add comments beside individual lines, then copy them with
**Export comments**. You can paste the feedback into Codex or share it with the
person making the changes.

## Install

On Linux, macOS or WSL, clone the repository and add its directory to your
`PATH`:

```bash
git clone https://github.com/wwiecek/diff_review.git
cd diff_review
export PATH="$PWD:$PATH"
diff-review --help
```

This makes the command available in the current shell. To make it available in
new terminals, add the following line to your shell's startup file (for example,
`~/.bashrc` or `~/.zshrc`), replacing the path with the clone's absolute path:

```bash
export PATH="/path/to/diff_review:$PATH"
```

If you move the clone, update that path. Keep `diff-review` and `diff_review.py`
together. To update the tool, run `git pull` inside the clone.

You can also call the Python module directly, including on Windows:

```bash
python /path/to/diff_review/diff_review.py --help
```

## Review changes

Run the command from the Git repository you want to review:

```bash
cd /path/to/your/project
diff-review
```

Open the printed path in your browser, or use `diff-review --open` to ask the tool
to open it. The default output is `./diff-review.html`; `--output PATH` chooses
another location and creates any missing parent directories.

| Command | Comparison |
| --- | --- |
| `diff-review` | Staged and unstaged changes against `HEAD` |
| `diff-review --commit REV` | A commit against its first parent |
| `diff-review --between main feature` | Direct comparison of two revisions |
| `diff-review --base main` | Current working tree against a revision |
| `diff-review --patch changes.diff` | An existing Git patch |
| `git diff -- src/ \| diff-review --patch -` | A patch read from standard input |

Git comparisons ignore CRLF/LF differences while preserving other whitespace
changes. Patch input is displayed as supplied and must use conventional
`diff --git` unified format; combined merge diffs are not supported.

Useful options:

- `--include-untracked`: include files that Git does not yet track.
- `--include-non-text`: include normally excluded formats such as PDFs, RDS,
  images and Office documents. Binary changes appear as metadata cards.
- `--context N`: show `N` lines of context around each change (default: 3).
- `--comments PATH`: import comments from a JSON file.
- `--output PATH`: choose where to save the HTML file.
- `--open`: open the result in a browser.

Generated review pages are excluded from untracked-file collection. Generating
a review does not stage or commit changes. Use `diff-review --help` for all
options.

## Use the review page

The page provides file-type filters, light and dark themes, and an optional
**Wrap long lines** toggle when code overflows. Character highlights mark small
edits between similar lines. You can expand a file to see unchanged lines and
attach comments to them.

Whole-file previews are limited to 1,000 lines and 200 KiB; changed hunks are
shown in full. Patch input has no whole-file preview.

Add line comments, then use **Export comments** to copy them
before reloading or closing the page. The HTML file is self-contained and needs
no server or internet connection, but it does not save comments back to disk.

To include comments when generating a page, pass `--comments comments.json` with
a JSON array such as:

```json
[
  {
    "author": "Reviewer",
    "path": "R/example.R",
    "side": "new",
    "line": 18,
    "body": "Check this calculation."
  }
]
```

Paths are relative to the repository. Use the displayed new path for renamed
files, `"old"` for deleted lines, and `"new"` for additions or current lines.
Comments that cannot be matched to a line remain visible in the export.

## Optional Codex skill

The bundled skill lets Codex recognise requests for an HTML review. To install
it, run these commands from the clone:

```bash
mkdir -p "$HOME/.codex/skills"
ln -sfn "$PWD/skill" "$HOME/.codex/skills/diff-review"
```

If you move the clone, repeat the skill installation command. This optional
symlink lets Codex find the skill; the command itself only needs the `PATH`
setting above.

For example, ask Codex: "Create an HTML review of my uncommitted changes."

## Tests

Run from the clone:

```bash
python3 -m unittest test_diff_review -v
python3 test_browser.py
```

Browser tests require a local Chromium, Chrome or Edge installation. Use
`python3 test_browser.py --browser /path/to/chromium` to select it explicitly.

The original [implementation specification](diff-review-spec.md) records the
initial design. This README and `diff-review --help` describe the current
behaviour, including the defaults for browser opening and untracked files.
