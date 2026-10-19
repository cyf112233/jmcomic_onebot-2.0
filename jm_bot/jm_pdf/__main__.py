# -*- coding: utf-8 -*-
"""允许通过 ``python -m jm_bot.jm_pdf`` 直接转换目录。"""

from .main import _main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
