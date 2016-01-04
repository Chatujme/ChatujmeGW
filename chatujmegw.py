#!/usr/bin/env python
# -*- coding: utf-8 -*-
# encoding=utf8  
"""
  IRC Brana pro chat na Chatujme.cz
  Projekt vychazi z lidegw v46 ( http://sourceforge.net/projects/lidegw/ )
  
  @license MIT
  @author LuRy <lury@lury.cz>, <lury@chatujme.cz>
"""

import copy, os, re, socket, string, sys, threading, time, urllib, urllib2, random, json
import traceback
reload(sys)  
sys.setdefaultencoding('utf8')

PORT = 1111
BIND = "0.0.0.0"
version = 1.001

class ircrfc:
  cmd_list = 322 

class uzivatel:
  username = ""
  nick = ""
  password = ""
  rooms = []
  me = "chatujme.cz"


class ChatujmeSystem:
  def __init__ (self):
    self.url = "http://api.chatujme.loc/irc"
  def getRooms(self):
    response = urllib2.urlopen( "%s/%s" %(self.url, "get-rooms") )
    data = json.loads(response.read())
    return data

class Chatujme:
  def __init__ (self, mySocket, myAdress):
    self.socket = mySocket
    self.adress = myAdress
    self.user = copy.deepcopy(uzivatel())
    self.system = ChatujmeSystem()
    self.connection = True
    self.rfc = ircrfc()
  
  def parse(self, data, timestamp):
    if data == "":
      self.connection = False
      return 2
    if data.find("\r\n") != -1:
      irccmd = string.split(data, "\r\n")
    else:
      irccmd = string.split(data, "\n")
    for cmd_array in irccmd:
      # rozdelim jednotlivy pole na cmd[X]
      cmd = string.split(cmd_array.strip(), " ")
      log("%s" %(cmd))
      
      if cmd[0] == "NICK":
        self.user.nick = cmd[1] 
      elif cmd[0] == "USER":
        self.user.username = cmd[1] 
      elif cmd[0] == "PASS":
        self.user.passowrd = cmd[1] 
      #elif cmd[0] == "JOIN":
      #elif cmd[0] == "PART":
      elif cmd[0] == "PING":
        if len(cmd) >= 2:
          self.send(":%s PONG :%s\n" % (self.user.me, cmd[1]))
        else :
          self.send(":%s PONG %s\n" % (self.user.me, self.user.me))
      elif cmd[0] == "LIST":
        rooms = self.system.getRooms()
        for room in rooms:
          self.send(self.rfc.cmd_list, "#%d %d :%s" % ( room['id'], room['online'], room['nazev'].encode("utf8") ) )
      #elif cmd[0] == "PRIVMSG":
      #elif cmd[0] == "NOTICE":
      #elif cmd[0] == "NOTICE":
      #else:
        #self.socket.send(":%s PONG :%s\n")
      
  def send(self, _id, msg):
    log("SENDING: %s" %(msg))
    self.socket.send(":%s %d %s %s\n"  %(self.user.me, _id, self.user.nick, msg)  )
    


class SocketHandler(threading.Thread):
  def __init__ (self, socket, address):
    threading.Thread.__init__(self)
    self.socket = socket
    self.address = address
    self.running = True
  def run (self):
    log("Prijato spojeni z %s" % (self.address[0]))

    timestamp = int(time.time())
    instance = Chatujme(self.socket, self.address[0]);    

    while self.running:
      try:
        ircdata = instance.socket.recv(2**13)
        if (instance.parse(ircdata,timestamp) == 2):
          break
      except:
        if traceback:
          traceback.print_exc()
        try:
          instance.socket.send(instance.error())
        except:
          pass
        log("Spojeni z %s uzavreno." %(self.address[0]))
        self.socket.close()
        return False
      


def log(text):
  print "[%s] %s" % (time.strftime('%Y/%m/%d %H:%M:%S'), text)

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind((BIND, PORT))
s.listen(50)


log("ChatujmeGW %s, nasloucham na portu %s:%s" %(version, BIND, PORT))

while 1:
  try:
    connection, address = s.accept()
    handler = SocketHandler(connection,address)
    handler.run()
    
  except (KeyboardInterrupt, EOFError):
    s.close()
    log("Vypinam...", 1)
    break
