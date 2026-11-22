import sys, logging
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from broker.binance_client import BinanceClient
from database.db_manager import DBManager
from utils.config_loader import load_config
from utils.logger_setup import setup_logging

logger = logging.getLogger(__name__)

def download(symbol, timeframe, start, end=None):
    cfg    = load_config()
    client = BinanceClient(testnet=cfg["exchange"]["testnet"])
    db     = DBManager(cfg["data"]["db_path"])

    total = 0
    start_str = start
    logger.info("Downloading %s %s from %s", symbol, timeframe, start)

    while True:
        rows = client.get_historical_klines(symbol, timeframe, start_str, end_str=end, limit=1000)
        if not rows:
            break
        inserted = db.upsert_ohlcv(rows)
        total += len(rows)
        logger.info("Batch: %d candles (total so far: %d)", len(rows), total)

        # Advance start to last candle close_time + 1ms
        last_close = rows[-1]["close_time"]
        from datetime import datetime, timezone
        next_start = datetime.fromtimestamp((last_close + 1) / 1000, tz=timezone.utc)
        start_str = next_start.strftime("%Y-%m-%d %H:%M:%S")

        if len(rows) < 1000:
            break  # last page

    logger.info("Done. Total downloaded: %d candles", total)
    return total

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",    default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start",     default="2023-01-01")
    parser.add_argument("--end",       default=None)
    args = parser.parse_args()
    setup_logging()
    n = download(args.symbol, args.timeframe, args.start, args.end)
    print(f"Downloaded {n} candles for {args.symbol}/{args.timeframe}")
