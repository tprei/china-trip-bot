from pathlib import Path

import httpx
import pytest

from tripbot.cli import main
from tripbot.pipeline.health import ping
from tripbot.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_settings_read_dotenv_and_let_the_environment_win(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        '# secrets\nTELEGRAM_TOKEN="from-file"\nSERPAPI_KEY=abc\n\nTRIPBOT_DB=/srv/bot.db\n',
        encoding="utf-8",
    )
    settings = load_settings(tmp_path, {"TELEGRAM_TOKEN": "from-env", "DASHBOARD_URL": " "})
    assert settings.telegram_token == "from-env"
    assert settings.serpapi_key == "abc"
    assert settings.db_path == Path("/srv/bot.db")
    assert settings.site_dir == tmp_path / "site"
    assert settings.dashboard_url is None


def test_ping_reports_success_and_failure_and_never_raises() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/fail"):
            raise httpx.ConnectError("down")
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(answer)) as client:
        ping(client, "https://hc.example/abc", ok=True)
        ping(client, "https://hc.example/abc/", ok=False)
        ping(client, None, ok=True)
    assert seen == ["https://hc.example/abc", "https://hc.example/abc/fail"]


def test_check_config_validates_the_real_files(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(ROOT), "check-config"]) == 0
    out = capsys.readouterr().out
    assert "5 people in 3 groups" in out
    assert "window B: 30 Aug – 12 Sep" in out


def test_build_dashboard_works_on_an_empty_store(tmp_path: Path) -> None:
    for name in ("config", "data"):
        (tmp_path / name).symlink_to(ROOT / name)
    assert main(["--root", str(tmp_path), "build-dashboard"]) == 0
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Ainda sem combinação completa" in html
