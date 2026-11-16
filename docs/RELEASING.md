# Releasing UHB

One version, one tag, one GitHub release. The release workflow does the upload;
everything before it is a gate that must be green, because a published version
number cannot be reused on PyPI.

The distribution is `uhb-harness`; the import package is `uhb`; the console
script is `uhb`. `pyproject.toml` reads the version from `uhb/__init__.py`
(`dynamic = ["version"]`), so the bump is one line.

## Before the tag

- [ ] `CHANGELOG.md`: the `[Unreleased]` section is renamed to the version and
      dated (`## [1.0.0] — YYYY-MM-DD`), and its compare link is added at the
      bottom. Every user-visible change since the previous tag is in it.
- [ ] `uhb/__init__.py`: `__version__ = "1.0.0"`, and nothing else in that file.
- [ ] `README.md` **and** `uhb/README.md` state the same roadmap and the same
      version claim — PyPI shows `uhb/README.md`, the repository shows the root
      one, and a divergence is the most common release bug in this project.
- [ ] `python -m pytest uhb/tests -q` green locally.
- [ ] CI green on the three operating systems of the matrix (the matrix, not a
      hand-written count, is the source of truth for pass/skip figures).
- [ ] `uhb --help`, `uhb status`, `uhb ui --markup`, `uhb modes list` answer.
- [ ] `python tools/make_test_ui.py --check` — the proof page still matches
      `uhb ui --markup`.
- [ ] `python -m build --wheel` then inspect the wheel:
      `unzip -l dist/*.whl | grep -E 'LICENSE|catalog.json|profiles/'` — both
      licence files, the routing catalog and the four mode profiles must be
      inside, or an installed UHB boots with no policy.
- [ ] `python -m twine check dist/*` (if `twine` is installed): the PyPI page
      renders, the description is not empty.
- [ ] Security greps on every touched file: no key prefix, no credential value,
      no absolute path, no environment dump.

## Docker, once per release

- [ ] `docker compose build` succeeds from a clean tree.
- [ ] `docker compose up -d` then, in order:
      `curl -s localhost:8101/status` → `"connected": true`;
      `curl -s -X POST localhost:8101/route -H 'content-type: application/json' -d '{"prompt":"..."}'` → a chain;
      `curl -s localhost:8101/rag/status` → `"service": true`;
      `curl -s localhost:8101/siyuan/blocks` → 200 with a token, 400 with a clear
      message without one.
- [ ] `docker compose logs` shows no traceback, and `docker compose down` removes
      all three containers.

## Publish

- [ ] Commit: `chore(release): 1.0.0`, one commit, nothing unrelated in it.
- [ ] `git tag -a v1.0.0 -m "UHB 1.0.0"` — annotated, message = version.
- [ ] `git push origin main --follow-tags`.
- [ ] GitHub → Releases → *Draft a new release*: tag `v1.0.0`, title
      `UHB 1.0.0`, notes = the `[1.0.0]` section of `CHANGELOG.md`.
- [ ] **Publish** the release. `publish.yml` triggers on `release: published`:
      it runs the suite, builds, and uploads through PyPI trusted publishing.
- [ ] If the `pypi` environment requires a reviewer, approve the pending
      deployment; without that approval the upload stays queued.
- [ ] `gh run list --repo Antoine-Thomas/UHB-Universal-Harness-Backend` → the
      *Publish to PyPI* run is green.

## After the publish

- [ ] `pip download uhb-harness==1.0.0 --no-deps -d /tmp/x` succeeds, and
      `unzip -l` on it shows both licences.
- [ ] The PyPI page shows `FSL-1.1-MIT` and the README section on the licence
      split — expected: a non-OSI licence, stated as such.
- [ ] Roadmap item 3 is updated in both READMEs; `CHANGELOG.md` gets a fresh
      empty `[Unreleased]` section.
- [ ] `uhb update` on an installed copy reports 1.0.0 available and installs it.

## Standing rules

- A published version number is consumed for ever: fix forward, never re-upload.
- A release with a red matrix is not published; the tag waits.
- Never `git add -A`: the tree may carry unrelated `.gitignore` edits.
