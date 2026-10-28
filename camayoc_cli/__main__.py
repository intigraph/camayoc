"""`python3 -m camayoc_cli` and `python3 <plugin>/camayoc_cli/__main__.py`."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from camayoc_cli.cli import main
else:
    from .cli import main

sys.exit(main())
