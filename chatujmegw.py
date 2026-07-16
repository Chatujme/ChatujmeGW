#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
  IRC Gateway for Chatujme.cz chat
  Based on lidegw v46 ( http://sourceforge.net/projects/lidegw/ )

  Refactored for Python 3 and RFC compliance

  @license MIT
  @author LuRy <lury@lury.cz>, <lury@chatujme.cz>

  rfc-codes https://www.alien.net.au/irc/irc2numerics.html
  rfc https://tools.ietf.org/html/rfc1459
"""

import argparse
import io
import sys
import traceback as tb

# Force UTF-8 output on Windows to support emoji and special characters
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from gw import config
from gw.server import main
from gw.util import log, fatal_error_pause


def parse_args():
    parser = argparse.ArgumentParser(description=f'ChatujmeGW - v{config.VERSION}')
    parser.add_argument('--port', type=int, help="Default port 6667", default=6667)
    parser.add_argument('--listen', help="Bind gateway. Default 127.0.0.1 (localhost only), use 0.0.0.0 for external access", default="127.0.0.1")
    parser.add_argument('--debug', help="Debug/Verbose print", type=int, default=0)
    parser.add_argument('--ssl', action='store_true', help="Enable SSL/TLS encryption (SSL-only mode)")
    parser.add_argument('--ssl-port', type=int, help="Additional SSL port (enables dual-port mode: non-SSL on --port, SSL on --ssl-port)")
    parser.add_argument('--ssl-cert', help="Path to SSL certificate file (PEM format)")
    parser.add_argument('--ssl-key', help="Path to SSL private key file (PEM format)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config.PORT = args.port
    config.BIND = args.listen
    config.DEBUG = args.debug
    config.VERBOSE_THREADS = args.debug >= 2
    config.SSL_ENABLED = args.ssl
    config.SSL_PORT = args.ssl_port
    config.SSL_CERT = args.ssl_cert
    config.SSL_KEY = args.ssl_key
    config.DUAL_PORT_MODE = args.ssl_port is not None

    try:
        main()
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        if config.DEBUG:
            tb.print_exc()
        fatal_error_pause()
        sys.exit(1)
