from __future__ import annotations

import argparse
from pathlib import Path

import anyio

from core.config import load_config
from core.engine import GenerationEngine
from core.logging_utils import configure_logging, get_logger


logger = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rss-generator", description="Generic RSS generator")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate feeds for all configured sites")
    gen.add_argument(
        "--config",
        type=Path,
        default=Path("config/sites.yaml"),
        help="Path to sites configuration YAML",
    )
    gen.add_argument(
        "--cache",
        type=Path,
        default=Path("data/cache.json"),
        help="Path to dedup cache file",
    )
    gen.add_argument(
        "--feeds-dir",
        type=Path,
        default=Path("feeds"),
        help="Directory to write generated feeds into",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        cfg = load_config(args.config)
        engine = GenerationEngine(config=cfg, cache_path=args.cache, feeds_dir=args.feeds_dir)

        async def _run() -> None:
            await engine.run()

        anyio.run(_run)
        logger.info("cli.generate.done")
        return 0

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

