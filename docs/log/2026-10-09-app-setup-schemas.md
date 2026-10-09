# web/app.py loses the setup page, the request models and the provider catalog (shipped)

Why: first part of splitting `web/app.py` that needs no freeze: three blocks that no
route's body depends on, moved verbatim, 580 lines fewer in the file.

## Shipped
- `web/provider_catalog.py`: `PROVIDER_CATALOG`, `BUILTIN_PROVIDERS` and the env-variable
  lists derived from them.
- `web/setup_app.py`: the first-run setup page and its app. It is `build_setup_app(configured,
  find_dotenv_path, load_settings_or_none)`; `app.py` keeps `create_setup_app(configured)`
  and passes `cli`'s two functions in, so the package still imports `cli` from one place only
  (`web.app`, an ignored import) and tests call it as before.
- `web/schemas.py`: the 25 request models. `app.py` imports them, so
  `from coscribe.web.app import ScriptEnvPackageInstall` still works.
- `_NPM_PACKAGE_NAME_RE` stays in `app.py`, next to its one use.

## Not verified
- The first-run page in the desktop app (the test covers the routes and the saved `.env`).

## Follow-ups
- `docs/plan.md` item 1: test helpers out of `tests/test_web.py`, `AppState`, then the routes
  in an announced freeze.
