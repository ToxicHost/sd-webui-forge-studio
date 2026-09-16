"""Guarded module entry point.

Routine use goes through ``bootstrap.py``. Invoking the package without the
required isolated/no-site flags fails before importing third-party modules.
"""

from .bootstrap import main


raise SystemExit(main())
