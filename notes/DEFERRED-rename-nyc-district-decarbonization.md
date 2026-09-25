# DEFERRED — rename project SignalNYC → "NYC District Decarbonization"

Requested 2026-09-25; explicitly deferred by the user ("save these steps for later")
in favour of BID-boundary / campus-district work. NOT yet done.

## Why this is not a find-and-replace

`signalnyc` is a real Python package name (`src/signalnyc/`), referenced by:
  * `pyproject.toml` [project].name + setuptools packages.find where=src
  * every `from signalnyc...` import across src/ and tests/
  * the dev server invocation `python -m signalnyc.api --port 3320`
  * env vars: `SIGNALNYC_FOOTPRINTS`, `SIGNALNYC_SCRATCH`
So renaming the package requires moving the directory AND updating all imports,
the module entrypoint, and any env-var names — a code change, not a text edit.

## Distinct layers to decide on separately

1. **Python package** `signalnyc` — internal; breaking-change level.
2. **Repo + directory** `/mnt/e/OC_Projects/projects/signalnyc` — note the repo
   has history and a private GitHub remote `github.com/bckaroo/signalnyc`.
3. **Display name** "SignalNYC" in `web/index.html`, `web/src/app.css`, the app
   header — user-facing, cheap, and what the request most likely means.
4. **DevOS/ProjectOS records** — PROJ-026, existing DEV-* task ids, and the
   `_skill-factory` / skills referencing SignalNYC.
5. **Generated data provenance** — manifests store sha256 + source paths; renaming
   paths invalidates nothing cryptographically but makes old manifests' paths stale.
   Do NOT rewrite historical manifests (they are audit records of what was built).

## Suggested order (least risk first)

1. Display strings + page title (no identifiers).
2. DevOS/ProjectOS project name.
3. Repo/dir rename LAST, with remotes + any cron/service references updated,
   since it breaks the launch command and every absolute path in this repo and
   in the skills that reference `/mnt/e/OC_Projects/projects/signalnyc`.

## Open question for the user
Which of the five layers above should change? "nyc-district-decarbonization" as a
slug, "NYC District Decarbonization" as a display name, or both — and is the
Python package name to change too (breaks imports) or stay `signalnyc`?
