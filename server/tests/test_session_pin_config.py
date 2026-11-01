"""Server configuration locates the single pinned conversation."""
from nexo_server.config import HermesSettings


def test_pinning_settings_have_safe_defaults():
    settings = HermesSettings()
    assert settings.session_id == ''
    assert settings.hermes_home == ''
    assert settings.session_binding_path == ''
    assert settings.session_name == 'nexo'
