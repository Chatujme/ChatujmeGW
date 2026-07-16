#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
  IRC Gateway for Chatujme.cz chat
  Based on lidegw v46 ( http://sourceforge.net/projects/lidegw/ )

  Launcher kept for compatibility - the actual code lives in src/chatujmegw/.
  Equivalent: PYTHONPATH=src python -m chatujmegw

  @license MIT
  @author LuRy <lury@lury.cz>, <lury@chatujme.cz>
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from chatujmegw.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
