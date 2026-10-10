# Setting up the script environments is serialized (shipped)

Why: `ensure_script_env` and `ensure_node_env` had no lock. Two first-ever script runs at once
(two scripts started together) both created the venv in one directory, and a second caller saw
the venv's Python as soon as it existed and used it while the first was still installing the
baseline packages into it.

## Shipped
- A process-wide lock around both `ensure_*` functions (`tools/script_env.py`, `tools/node_env.py`),
  so the second caller waits and then finds the finished environment.
- `tests/test_env_lock.py`: two threads call each `ensure_*` at the same time with the process
  calls faked (no network); the venv is created and seeded once, `npm install` runs once. Both
  tests fail without the lock.

## Not verified
- A second process (the CLI next to the server) is not covered; the lock is in-process.
- A seeding that fails still leaves a half-made venv that later calls return as ready; unchanged.

## Follow-ups
- Remove the venv when seeding fails so the next call retries.
