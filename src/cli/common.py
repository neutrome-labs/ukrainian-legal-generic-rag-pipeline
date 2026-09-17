"""Shared argument conventions for command-line entry points."""

import argparse


def add_storage_arguments(parser, *, default_local=False):
    """Storage destinations are mutually exclusive, not independent booleans."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--local", dest="local", action="store_true",
                       help=f"Save to local storage{' (default)' if default_local else ''}")
    group.add_argument("--remote", dest="local", action="store_false",
                       help=f"Save to remote storage (R2){' (default)' if not default_local else ''}")
    parser.set_defaults(local=default_local)


def add_boolean_argument(parser, name, *, default=False, help):
    """Expose both --name and --no-name, with an explicit default."""
    parser.add_argument(name, action=argparse.BooleanOptionalAction,
                        default=default, help=f"{help} (default: {str(default).lower()})")


def add_dry_run_argument(parser):
    add_boolean_argument(parser, "--dry-run",
                         help="Preview the operation without writing files or accessing remote storage")
