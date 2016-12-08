#!/usr/bin/env python
# -*- coding: utf-8 -*-
# encoding=utf8  
from distutils.core import setup
import py2exe
import sys

dll_excludes = [
    # don't import these - otherwise win7 created *.exe won't work in winXP
    # http://stackoverflow.com/questions/1979486/py2exe-win32api-pyc-importerror-dll-load-failed
    "mswsock.dll",
    "powrprof.dll",
    "crypt32.dll"
]
sys.argv.append("--dll-excludes=%s" % ",".join(dll_excludes))


setup(
  name = 'ChatujmeGW IRC Gateway',
  version = '1.9',
  description = 'IRC brana pro pristup k chatu Chatujme.cz',
  author = 'LuRy',
  author_email = 'lury@lury.cz',
  maintainer='LuRy',
  maintainer_email='lury@lury.cz',
  license = 'MIT',
  platforms = ["Windows"],
  url = 'http://lury.cz',
  zipfile = "shared.lib",
  #packages = ['....', '....'],
  console = [{
      'script': 'chatujmegw.py'
      ,"icon_resources": [(0, "chatujme.ico")]
  }],
  options = {
      "py2exe": {
          "optimize": 2,
          "compressed" : True,
          'bundle_files': 1
          
      } 
    }
)
             