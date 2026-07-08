"""
Unit tests must never require live AWS credentials or network access.
Patching happens here, at collection time, before any test_*.py module in
this directory is imported — app.core.security and friends call
get_settings() at their own import time, so the patch has to land first.
"""
import app.core.config as _config

_TEST_SECRETS = {
    "secret_key": "test-secret-key-for-unit-tests-only",
    "database_url": "postgresql://test:test@localhost:5432/test",
}

_config._fetch_secrets_from_aws = lambda: _TEST_SECRETS
_config.get_settings.cache_clear()
