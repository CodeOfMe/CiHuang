"""Command line interface for CiHuang."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import api
from .__version__ import __version__

log = logging.getLogger("cihuang")


def _emit(result: api.ToolResult, as_json: bool, quiet: bool) -> int:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    elif not quiet:
        if result.success:
            print(json.dumps(result.data, ensure_ascii=False, indent=2))
        else:
            print(f"error: {result.error}", file=sys.stderr)
    return 0 if result.success else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cihuang",
        description="CiHuang (雌黄) - a PySide6 SVG editor and scriptable SVG rewriter.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"cihuang {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress non-essential output")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    # The same three flags are accepted after a subcommand too.  Defaults are
    # suppressed so an absent flag does not overwrite the main parser's value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command")

    p_info = sub.add_parser("info", parents=[common], help="list editable elements of an SVG")
    p_info.add_argument("input")
    p_info.add_argument("-o", "--output", default=None)

    p_fill = sub.add_parser("fill", parents=[common], help="set fill or stroke color of one element")
    p_fill.add_argument("input")
    p_fill.add_argument("index", type=int)
    p_fill.add_argument("color", nargs="?", default=None, help="CSS color, omit for none")
    p_fill.add_argument("--stroke", action="store_true", help="edit stroke instead of fill")
    p_fill.add_argument("-o", "--output", default=None)

    p_move = sub.add_parser("move", parents=[common], help="translate one element by dx/dy")
    p_move.add_argument("input")
    p_move.add_argument("index", type=int)
    p_move.add_argument("dx", type=float)
    p_move.add_argument("dy", type=float)
    p_move.add_argument("-o", "--output", default=None)

    p_text = sub.add_parser("text", parents=[common], help="replace the content of a <text> element")
    p_text.add_argument("input")
    p_text.add_argument("index", type=int)
    p_text.add_argument("text")
    p_text.add_argument("-o", "--output", default=None)

    p_rm = sub.add_parser("remove", parents=[common], help="delete one element")
    p_rm.add_argument("input")
    p_rm.add_argument("index", type=int)
    p_rm.add_argument("-o", "--output", default=None)

    p_gui = sub.add_parser("gui", parents=[common], help="open the graphical editor")
    p_gui.add_argument("input", nargs="?", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.quiet:
        logging.basicConfig(level=logging.WARNING)
    elif args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.command in (None, "gui"):
        return _run_gui(getattr(args, "input", None))

    if args.command == "info":
        return _emit(api.cihuang_inspect(args.input), args.json, args.quiet)
    if args.command == "fill":
        prop = "stroke" if args.stroke else "fill"
        result = api.cihuang_set_color(
            input_path=args.input,
            element_index=args.index,
            prop=prop,
            color=args.color,
            output_path=args.output,
        )
        return _emit(result, args.json, args.quiet)
    if args.command == "move":
        result = api.cihuang_move(
            input_path=args.input,
            element_index=args.index,
            dx=args.dx,
            dy=args.dy,
            output_path=args.output,
        )
        return _emit(result, args.json, args.quiet)
    if args.command == "text":
        result = api.cihuang_set_text(
            input_path=args.input,
            element_index=args.index,
            text=args.text,
            output_path=args.output,
        )
        return _emit(result, args.json, args.quiet)
    if args.command == "remove":
        result = api.cihuang_remove(
            input_path=args.input,
            element_index=args.index,
            output_path=args.output,
        )
        return _emit(result, args.json, args.quiet)

    parser.print_help()
    return 2


def _run_gui(path: str | None) -> int:
    from .gui import main as gui_main

    return gui_main([path] if path else [])


if __name__ == "__main__":
    sys.exit(main())
