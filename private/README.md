# private/

Nothing in this folder is committed or published. It's gitignored (except this README), because the repository is public.

- `notes.json`: reading verdicts (read / skimmed / unread) and notes, keyed by work id. Written by `bib.py tag --verdict/--note`.
- `ext.py` (optional): your own commands. Define `register(subparsers, handlers)` and `bib.py` loads it on startup.
- Anything else you want to keep alongside the bibliography.

Three safeguards:
1. `.gitignore` keeps these files out of git.
2. `bib.py build` never reads them into the site, and refuses to build if any text from a JSON file here shows up in `site/`.
3. The GitHub workflow fails if anything in `private/` other than this README is ever committed.

These files exist only on the machine where you create them. To sync them across devices, point the CLI at a folder in iCloud, OneDrive or Dropbox:

```bash
export BIB_PRIVATE_DIR=~/OneDrive/bibliography-private
```

The build's leak check reads from that folder too.
