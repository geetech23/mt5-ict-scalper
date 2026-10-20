"""Entry point: `python main.py`."""
from __future__ import annotations

import getpass
import signal
import sys
import threading
import time
import webbrowser

from config import ENV_PATH, Config
from utils.logger import get_logger, setup_logging
from utils.validators import validate_config


def setup_wizard() -> None:
    """First-run wizard: writes a gitignored .env."""
    print("👋 First-run setup (press Enter for defaults). Credentials are stored only in .env\n")
    broker = (input("Broker [tradelocker/mt5/sim] (tradelocker): ").strip().lower() or "tradelocker")
    sim = (input("Simulation mode - no real orders? [Y/n]: ").strip().lower() or "y") != "n"
    lines = [f"BROKER={broker}", f"SIMULATION_MODE={'true' if sim else 'false'}"]
    if broker == "tradelocker":
        lines += [f"TL_ENVIRONMENT={input('Environment [demo/live] (demo): ').strip().lower() or 'demo'}",
                  f"TL_EMAIL={input('TradeLocker email (blank to skip): ').strip()}",
                  f"TL_PASSWORD={getpass.getpass('TradeLocker credential (hidden): ').strip()}",
                  f"TL_SERVER={input('Server name: ').strip()}"]
    elif broker == "mt5":
        lines += [f"MT5_LOGIN={input('MT5 login: ').strip() or 0}",
                  f"MT5_PASSWORD={getpass.getpass('MT5 credential (hidden): ').strip()}",
                  f"MT5_SERVER={input('MT5 server: ').strip()}"]
    lines.append(f"SYMBOLS={input('Symbols (EURUSD,XAUUSD): ').strip().upper() or 'EURUSD,XAUUSD'}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass
    print(f"✅ Saved {ENV_PATH}\n")


def main() -> int:
    setup_logging()
    log = get_logger("main")
    if not ENV_PATH.exists() and sys.stdin.isatty():
        setup_wizard()
    cfg = Config.from_env()
    errors = validate_config(cfg)
    if errors:
        for e in errors:
            log.error("❌ Config: %s", e)
        return 1
    if not cfg.simulation_mode:
        log.warning("⚠️ LIVE ORDER MODE (%s). Real %s orders will be placed.", cfg.broker,
                    "money" if cfg.tl_environment == "live" else "demo")

    from bot import TradingBot
    from brokers import create_connector
    from dashboard.app import start_dashboard

    bot = TradingBot(cfg, create_connector(cfg))
    start_dashboard(bot.snapshot, cfg.dashboard_host, cfg.dashboard_port)
    url = f"http://{cfg.dashboard_host}:{cfg.dashboard_port}"
    log.info("✅ Dashboard: %s", url)
    if cfg.open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    signal.signal(signal.SIGINT, lambda *_: bot.stop())
    signal.signal(signal.SIGTERM, lambda *_: bot.stop())
    worker = threading.Thread(target=bot.run, name="bot", daemon=True)
    worker.start()
    while worker.is_alive():
        time.sleep(0.5)
    log.info("👋 Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
