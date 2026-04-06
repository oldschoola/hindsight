"""CLI for installing the Hindsight Cursor plugin into a project."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Plugin files to copy (relative to the plugin_data directory).
# Preserves directory structure.
_PLUGIN_FILES = [
    ".cursor-plugin/plugin.json",
    "hooks/hooks.json",
    "rules/hindsight-memory.mdc",
    "scripts/lib/__init__.py",
    "scripts/lib/bank.py",
    "scripts/lib/client.py",
    "scripts/lib/config.py",
    "scripts/lib/content.py",
    "scripts/lib/daemon.py",
    "scripts/lib/llm.py",
    "scripts/lib/state.py",
    "scripts/recall.py",
    "scripts/retain.py",
    "settings.json",
    "skills/hindsight-recall/SKILL.md",
]

_USER_CONFIG_DIR = Path.home() / ".hindsight"
_USER_CONFIG_FILE = _USER_CONFIG_DIR / "cursor.json"


def _plugin_data_dir() -> Path:
    """Return the path to the bundled plugin data shipped with this package."""
    return Path(__file__).resolve().parent / "plugin_data"


def _copy_plugin(dest: Path) -> None:
    """Copy plugin files into *dest*, creating directories as needed."""
    src_root = _plugin_data_dir()
    for rel in _PLUGIN_FILES:
        src = src_root / rel
        if not src.exists():
            print(f"  warning: missing bundled file {rel}, skipping", file=sys.stderr)
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _scaffold_config(api_url: str | None, api_token: str | None, bank_id: str) -> None:
    """Create ~/.hindsight/cursor.json if it does not already exist."""
    if _USER_CONFIG_FILE.exists():
        print(f"  Config already exists at {_USER_CONFIG_FILE}, skipping.")
        return

    config: dict = {"bankId": bank_id}
    if api_url:
        config["hindsightApiUrl"] = api_url
    if api_token:
        config["hindsightApiToken"] = api_token

    _USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _USER_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  Created {_USER_CONFIG_FILE}")


def cmd_init(args: argparse.Namespace) -> None:
    """Install the Hindsight plugin into a Cursor project."""
    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"Error: {project} is not a directory.", file=sys.stderr)
        sys.exit(1)

    dest = project / ".cursor-plugin" / "hindsight-memory"
    if dest.exists() and not args.force:
        print(f"Plugin already installed at {dest}.")
        print("  Use --force to overwrite.")
        return

    print(f"Installing Hindsight plugin into {dest} ...")
    _copy_plugin(dest)
    print("  Plugin files copied.")

    _scaffold_config(args.api_url, args.api_token, args.bank_id)

    print()
    print("Done! Fully quit and reopen Cursor to activate the plugin.")


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Remove the Hindsight plugin from a Cursor project."""
    project = Path(args.project).resolve()
    dest = project / ".cursor-plugin" / "hindsight-memory"
    if not dest.exists():
        print("Plugin not found — nothing to remove.")
        return
    shutil.rmtree(dest)
    print(f"Removed {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hindsight-cursor",
        description="Hindsight memory plugin for Cursor",
    )
    sub = parser.add_subparsers(dest="command")

    # -- init --
    init_p = sub.add_parser("init", help="Install the plugin into a Cursor project")
    init_p.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Path to the Cursor project (default: current directory)",
    )
    init_p.add_argument("--force", action="store_true", help="Overwrite existing installation")
    init_p.add_argument("--api-url", default=None, help="Hindsight API URL (e.g. https://api.hindsight.vectorize.io)")
    init_p.add_argument("--api-token", default=None, help="Hindsight API token")
    init_p.add_argument("--bank-id", default="cursor", help="Memory bank ID (default: cursor)")
    init_p.set_defaults(func=cmd_init)

    # -- uninstall --
    uninst_p = sub.add_parser("uninstall", help="Remove the plugin from a Cursor project")
    uninst_p.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Path to the Cursor project (default: current directory)",
    )
    uninst_p.set_defaults(func=cmd_uninstall)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
