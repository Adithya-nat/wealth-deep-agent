"""The front door: an interactive menu, a live run panel, and the argparse layer.

`menu` is what a human uses; `app` is what `make` and CI call. The flags still
exist and still work — they simply stopped being the interface.
"""

from wealth_agent.cli.app import build_parser, main

__all__ = ["build_parser", "main"]
