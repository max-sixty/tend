# Tests

Tests must never write inside the repository tree. Write generated files under
pytest's `tmp_path` fixture; a file written under `tests/` is picked up by the
next run's fixture discovery and breaks parallel runs.
