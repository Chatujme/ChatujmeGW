#!/usr/bin/env python
# -*- coding: utf-8 -*-
# encoding=utf8  
"""
  IRC Brana pro chat na Chatujme.cz
  Projekt vychazi z lidegw v46 ( http://sourceforge.net/projects/lidegw/ )
  
  @license MIT
  @author LuRy <lury@lury.cz>, <lury@chatujme.cz>
"""

import copy, os, re, socket, string, sys, threading, time, urllib, urllib2, random, json, cookielib
import traceback
reload(sys)  
sys.setdefaultencoding('utf8')

PORT = 1111
BIND = "0.0.0.0"
version = 1.001
path = os.path.dirname(__file__)

motd = ''':
                       
                     
  .g8"""bgd` MM             Vitam te na Chatujme.cz
.dP'     `M  MM             Prihlasen jako %s@%s
dM'       `  MMpMMMb.  
MM           MM    MM       Verze brány %s  
MM.          MM    MM       
`Mb.     ,'  MM    MM  
  `"bmmmd' .JMML  JMML.

                         
'''

class ircrfc:
  RPL_WELCOME = 001
  RPL_YOURHOST = 002
  RPL_LISTSTART = 321
  RPL_LIST = 322
  RPL_LISTEND = 323
  RPL_TOPIC = 332
  RPL_NOTOPIC = 331
  ERR_NEEDMOREPARAMS = 461
  ERR_NOLOGIN = 444
    

class uzivatel:
  username = ""
  nick = ""
  password = ""
  rooms = []
  me = "chatujme.cz"
  login = False
  cookieJar = cookielib.LWPCookieJar(path + "/cookies.txt")
  urlfetcher = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookieJar), urllib2.HTTPSHandler(debuglevel=1))


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
  
  ''' Funkce na GET '''
  def getUrl(self, url):
    response = self.user.urlfetcher.open(url)
    self.user.cookieJar.save(ignore_discard=True)
    return response.read()
  
  ''' Funkce na POST '''
  def postUrl(self, url, postdata):
    response = self.user.urlfetcher.open(url , data=postdata)
    self.user.cookieJar.save(ignore_discard=True)
    return response.read()
  
  ''' Funkce na prihlaseni '''
  def checkLogin(self):
    if self.user.username == "":
      return False
    if self.user.nick == "":
      return False
    if self.user.password == "":
      return False
    postdata = "username=%s&password=%s" %( self.user.username, self.user.password )
    response = self.postUrl ( "%s/%s" % (self.system.url, "check-login"), postdata )
    data = json.loads(response)

    if data['code'] == 401:
      self.send(self.rfc.ERR_NOLOGIN, "%s: %s" % (self.user.username, data['message'].encode("utf8") ) )
      return False
    elif data['code'] == 200:
      self.send( self.rfc.RPL_WELCOME, motd %( self.user.username, self.user.me, version ))
      return True 
    elif data['code'] == 201:
      self.send( self.rfc.RPL_WELCOME, motd %( self.user.username, self.user.me, version ))
      return True 
    else:
      return False
  
  ''' Kontrola jeslti je v mistnosti '''
  def isInRoom(self, room):
    for croom in self.user.rooms:
      if room == croom.id:
        return True
    return False
  
  def joinToRoom(self, room_id, Key = None):
    response = self.getUrl( "%s/%s?id=%d" % ( self.system.url, "join", room_id ) )
    data = json.loads(response)
    return data
  
  ''' Parsovani prikazu z IRC '''
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
        if self.user.password != "":
          self.user.login = self.checkLogin()
         
      elif cmd[0] == "USER":
        self.user.username = cmd[1]
        if self.user.password != "":
          self.user.login = self.checkLogin()
         
      elif cmd[0] == "PASS":
        self.user.password = cmd[1]
        if self.user.username == "":
          self.send(self.rfc.ERR_NOLOGIN, "%s: User not logged in. Use /USER" % (self.user.me))
        elif self.user.nick == "":
          self.send(self.rfc.ERR_NOLOGIN, "%s: User not logged in. Use /NICK" % (self.user.me))
        else:
          self.user.login = self.checkLogin()
         
      elif cmd[0] == "JOIN":
        room = cmd[1].replace('#', '')
        rooms = string.split(room, ",")
        
        if not self.user.login:
          self.send(self.rfc.ERR_NOLOGIN, "%s: User not logged in" % (self.user.me))
          return False
        
        for room in rooms:
          if self.isInRoom(room):
            continue
          
          print room
          #data = self.joinToRoom(room)
          
         
        #if self.user.nick
      #elif cmd[0] == "PART":
      elif cmd[0] == "PING":
        if len(cmd) >= 2:
          self.send(":%s PONG :%s\n" % (self.user.me, cmd[1]))
        else :
          self.send(":%s PONG %s\n" % (self.user.me, self.user.me))
      elif cmd[0] == "LIST":
        rooms = self.system.getRooms()
        self.send(self.rfc.RPL_LISTSTART, "Channels :Users Name")
        for room in rooms:
          self.send(self.rfc.RPL_LIST, "#%d %d :%s" % ( room['id'], room['online'], room['nazev'].encode("utf8") ) )
        self.send(self.rfc.RPL_LISTEND, "END of /List")
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
