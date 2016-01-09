#!/usr/bin/env python
# -*- coding: utf-8 -*-
# encoding=utf8  
from distutils.core import setup
import py2exe

setup(
  name = 'ChatujmeGW IRC Gateway',
  version = '1.4',
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
