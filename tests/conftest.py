import pytest

from investai.config import load_config


@pytest.fixture
def tmp_cfg(tmp_path):
    """Real config, but all runtime artifacts redirected to a temp dir."""
    cfg = load_config()
    cfg.raw["paths"]["db"] = str(tmp_path / "paper.db")
    cfg.raw["paths"]["instruments_cache"] = str(tmp_path / "instruments.json")
    cfg.raw["paths"]["token_store"] = str(tmp_path / ".token.json")
    cfg.raw["paths"]["log_dir"] = str(tmp_path / "logs")
    return cfg
