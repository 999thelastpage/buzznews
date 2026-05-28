

def test_cli_help():
    from buzz_news.cli import COMMANDS
    assert "migrate" in COMMANDS
    assert "fetch-once" in COMMANDS
    assert "run-worker" in COMMANDS
    assert "run-web" in COMMANDS
    assert "cleanup-bad-hindi" in COMMANDS


def test_settings_load():
    from buzz_news.config import get_settings
    s = get_settings()
    assert s.EMBED_DIM == 768
    assert s.SCORE_TIME_GRAVITY == 1.5
    assert s.OPENCLAW_GATEWAY_URL == "http://127.0.0.1:19262"
