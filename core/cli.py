from __future__ import annotations

import argparse
import logging
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
        "--site",
        type=str,
        default=None,
        help="If set, generate only this site id (filename stem under config dir)",
    )
    gen.add_argument(
        "--config",
        type=Path,
        default=Path("config/sites"),
        help="Sites directory (movies/*.yaml + series/*.yaml, or flat *.yaml) or legacy sites.yaml",
    )
    gen.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="Path to dedup cache file (default: data/cache.json or data/cache/<site>.json when --site is set)",
    )
    gen.add_argument(
        "--feeds-dir",
        type=Path,
        default=Path("feeds"),
        help="Directory to write generated feeds into",
    )

    onboard = sub.add_parser(
        "onboard-site",
        help="Interactively discover a new site, write config, and trigger the feed workflow",
    )
    onboard.add_argument(
        "url",
        nargs="?",
        help="Site URL to analyze",
    )
    onboard.add_argument(
        "--config",
        type=Path,
        default=Path("config/sites"),
        help="Sites directory (movies/*.yaml + series/*.yaml, or flat *.yaml) or legacy sites.yaml",
    )
    onboard.add_argument(
        "--workflow",
        default="",
        help="Workflow YAML under .github/workflows/ to dispatch (default: update.yml)",
    )
    onboard.add_argument(
        "--no-push",
        action="store_true",
        help="Write the config locally without committing or pushing",
    )
    onboard.add_argument(
        "--no-dispatch",
        action="store_true",
        help="Skip workflow dispatch after pushing",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        configure_logging()
        cfg = load_config(args.config)
        cache_path = args.cache
        if cache_path is None:
            cache_path = (
                Path("data") / "cache" / f"{args.site}.json"
                if args.site
                else Path("data/cache.json")
            )
        engine = GenerationEngine(config=cfg, cache_path=cache_path, feeds_dir=args.feeds_dir)

        async def _run() -> None:
            await engine.run(only_site=args.site)

        anyio.run(_run)
        logger.info("cli.generate.done")
        return 0

    if args.command == "onboard-site":
        configure_logging(level=logging.WARNING)
        from core.onboarding import run_onboarding

        return run_onboarding(
            url=args.url,
            config_path=args.config,
            workflow_name=args.workflow or None,
            push=not args.no_push,
            dispatch=not args.no_dispatch,
        )

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
