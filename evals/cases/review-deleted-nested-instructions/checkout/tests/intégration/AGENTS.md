# Integration tests

Mark every test in this directory with `@pytest.mark.integration`. CI selects
the integration job by that marker, so an unmarked test runs in the unit job,
which has no database.
