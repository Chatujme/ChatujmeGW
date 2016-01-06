#!/usr/bin/env python
# -*- coding: utf-8 -*-
# encoding=utf8  
"""
  IRC Brana pro chat na Chatujme.cz
  Projekt vychazi z lidegw v46 ( http://sourceforge.net/projects/lidegw/ )
  
  @license MIT
  @author LuRy <lury@lury.cz>, <lury@chatujme.cz>
  
  rfc-codes https://www.alien.net.au/irc/irc2numerics.html
  rfc https://tools.ietf.org/html/rfc1459
"""

import copy, os, re, socket, string, sys, threading, time, urllib, urllib2, random, json, cookielib
import traceback
reload(sys)  
sys.setdefaultencoding('utf8')

PORT = 6667 #Default IRC port
BIND = "0.0.0.0" #Bind to all IP
version = 1.1
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
  RPL_ENDOFMOTD = 376
  RPL_YOURHOST = 002
  RPL_LISTSTART = 321
  RPL_LIST = 322
  RPL_LISTEND = 323
  RPL_TOPIC = 332
  RPL_NOTOPIC = 331
  ERR_NOSUCHCHANNEL = 403
  ERR_UNKNOWNCOMMAND = 421
  ERR_NEEDMOREPARAMS = 461
  ERR_NOLOGIN = 444
  ERR_BANNEDFROMCHAN = 474
  RPL_CHANNELMODEIS = 324
  RPL_WHOREPLY = 352
  RPL_ENDOFWHO = 315
  RPL_NAMREPLY = 353
  RPL_ENDOFNAMES = 366
  RPL_NOTICE = "NOTICE"
  RPL_JOIN = "JOIN"
  RPL_PART = "PART"
  RPL_MODE = "MODE"
  RPL_PRIVMSG = "PRIVMSG"
    

class world:
  vlakna = []
  collector = None

class uzivatel:
  username = ""
  nick = ""
  password = ""
  rooms = []
  me = "chatujme.cz"
  login = False
  sex = "boys"
  reading = False
  cookieJar = cookielib.LWPCookieJar(path + "/cookies.txt")
  urlfetcher = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookieJar), urllib2.HTTPSHandler(debuglevel=1))
  settingsShowPMFrom = True
  showSmiles = 1 #0 - Schovat, 1 - Text podoba, 2 - Url  

class roomstruct:
  id = None
  lastId = 0
  lastMess = ""
  firstLoad = True


class Collector (threading.Thread):
  def __init__ (self):
    threading.Thread.__init__(self)
    self.running = True
    log("collector, init")
  def run (self):
    log("collector, start")
    while self.running:
      vlaken = len(world.vlakna)
      for vlakno in world.vlakna:
        if not vlakno.isAlive() and vlakno._Thread__started.is_set():
          world.vlakna.remove(vlakno)
          log("collector, purging %s" %(vlakno))
          del vlakno
          vlaken -= 1
      log("collector, all clear (%s threads)" %(vlaken))
      time.sleep(5)
    
    # shutdown
    for vlakno in world.vlakna:
      vlakno.running = False # shodim zbytek vlaken, aby se to vubec vyplo
    log("collector, shutdown")
  def start_threads (self):
    try:
      for vlakno in world.vlakna:
        if not vlakno._Thread__started.is_set():
          vlakno.start()
    except:
      log("Vlakno odmita startovat, pravdepodobne dosla pamet.", 1)


class getMessages (threading.Thread):
  def __init__ (self, inst, socket):
    threading.Thread.__init__(self)
    self.inst = inst
    self.running = True
    
  def run (self):
    while self.running and self.inst.connection:
      if len(self.inst.user.rooms) == 0:
        time.sleep(5)
        continue
      if not self.inst.connection:
        return False

      for room in self.inst.user.rooms:
        
        response = self.inst.getUrl( "%s/%s?id=%s&from=%s" %(self.inst.system.url, "get-messages", room.id, room.lastId ) )
        try:
          data = json.loads(response)
          for mess in data['mess']:
            if int(room.lastId) >= int(mess['id']):
              continue

            if mess['nick'].lower() == self.inst.user.username.lower():
              continue

            if mess['nick'].lower() == self.inst.user.nick.lower():
              continue

            ''' Pri JOINu nechceme nacist zadne zpravy zpetne '''              
            if not room.lastMess == "" and room.lastMess == mess['zprava']:
              continue

            room.lastId = mess['id']
            room.lastMess = mess['zprava']

            if room.firstLoad:
              continue


            msg = self.inst.cleanHighlight(mess['zprava'].encode("utf8"))
            msg = self.inst.cleanSmiles( msg )
            msg = self.inst.cleanUrls( msg )

            if mess["typ"] == 0: #Public
              self.inst.socket.send( ":%s %s #%s :%s\n" %(mess['nick'].encode("utf8"), self.inst.rfc.RPL_PRIVMSG, room.id, msg) )
            elif mess["typ"] == 1: #PM
              self.inst.socket.send( ":%s %s %s :%s\n" %(mess['nick'].encode("utf8"), self.inst.rfc.RPL_PRIVMSG, mess["komu"].encode("utf8"), msg) )
            elif mess["typ"] == 2: #System
              t = msg.replace("'","")
              if "vstoupil" in t or "vstoupila" in t:
                nick = re.findall(r'.+\s(.+)\svstoupil', msg)[0]
                self.inst.socket.send( ":%s %s #%s :%s\n" %( nick, self.inst.rfc.RPL_JOIN, room.id, msg )  )

              elif "odešel" in t or "odešla" in t:
                nick = re.findall(r'.+\s(.+)\sode', msg)[0]
                self.inst.socket.send( ":%s %s #%s :%s\n" % (nick, self.inst.rfc.RPL_PART, room.id, 'part')  )

              elif "odstran" in t:
                nick = re.findall(r'.+el\s(.+)\sbyl\s', msg)[0]
                self.inst.socket.send( ":%s %s #%s :%s\n" % (nick, self.inst.rfc.RPL_PART, room.id, 'timeout')  )

              elif "předal" in t or "předala" in t:
                target = re.findall(r'.+správce\s(.+)$', msg)[0]
                nick = re.findall(r'.+el\s(.+)\spředal', msg)[0]
                self.inst.socket.send( ":%s %s #%s -h %s\n" % (self.inst.user.me, self.inst.rfc.RPL_MODE, room.id, nick)  )
                self.inst.socket.send( ":%s %s #%s +h %s\n" % (self.inst.user.me, self.inst.rfc.RPL_MODE, room.id, target)  )
                self.inst.reloadUsers(room.id)

              else:
                self.inst.socket.send( ":%s %s #%s :%s\n" %(mess['nick'].encode("utf8"), self.inst.rfc.RPL_PRIVMSG, room.id, msg) )
            elif mess["typ"] == 3: #WALL
              if self.inst.user.settingsShowPMFrom:
                self.inst.socket.send( ":%s %s %s :[Z kanálu %s #%d ] %s\n" %(mess['nick'].encode("utf8"), self.inst.rfc.RPL_PRIVMSG, mess["komu"].encode("utf8"), mess['rname'].encode("utf8"), mess['rid'], msg) )
              else:
                self.inst.socket.send( ":%s %s %s :%s\n" %(mess['nick'].encode("utf8"), self.inst.rfc.RPL_PRIVMSG, mess["komu"].encode("utf8"), msg) )
            
        except:
          #if traceback:
          #  traceback.print_exc()
          time.sleep(1)
          pass

      ''' Pri JOINu nacteme seznam uzivatelu '''              
      if room.firstLoad:          
        self.inst.reloadUsers(room.id)
        room.firstLoad = False
        
      time.sleep(5)
    


class ChatujmeSystem:
  def __init__ (self):
    self.url = "http://api.chatujme.cz/irc"
  def getRooms(self):
    response = urllib2.urlopen( "%s/%s" %(self.url, "get-rooms") )
    data = json.loads(response.read())
    return data
    

class Chatujme:
  def __init__ (self, mySocket, myAdress, handler):
    self.socket = mySocket
    self.adress = myAdress
    self.user = copy.deepcopy(uzivatel())
    self.system = ChatujmeSystem()
    self.connection = True
    self.parent = handler
    self.rfc = ircrfc()
  
  def cleanHighlight(self, msg):
    return re.sub("<span style='background:#eded1a'>([^<]+)</span>", "\\1", msg)

  def cleanUrls(self, msg):
    return re.sub('<a href="([^"]+)" target="_blank">([^<]+)</a>', "\\1", msg)
  
  def cleanSmiles(self, msg):
    if self.user.showSmiles == 0:
      pattern = ""
    elif self.user.showSmiles == 1:
      pattern = "\\3"
    elif self.user.showSmiles == 2:
      pattern = "\\1"
      
    return re.sub('<img src=\'(.+?smiles/([^.]+).gif)\' alt=\'(.+?)\'>', pattern, msg)

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
  
  ''' Prenacteni seznamu uzivatelu /NAMES '''
  def reloadUsers(self, rid):
    data = self.getRoomUsers( rid )
    users = "";
    for user in data:
      users = "%s%s%s " %(users, self.userOPStatus(user), user['nick'].encode("utf8") )
    self.send( self.rfc.RPL_NAMREPLY, "= #%s :%s" %( rid, users ) )
    self.send( self.rfc.RPL_ENDOFNAMES, "#%s :End of /NAMES list" %(rid) )
    
  
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

    if data['code'] == 401: #Spatny login
      self.send(self.rfc.ERR_NOLOGIN, "%s: %s" % (self.user.username, data['message'].encode("utf8") ) )
      return False
    elif data['code'] == 200: #Nove prihlaseni
      self.send( self.rfc.RPL_WELCOME, motd %( self.user.username, self.user.me, version ))
      self.send( self.rfc.RPL_ENDOFMOTD, ":End of MOTD" )
      return True
    elif data['code'] == 201: #Uzivatel je jiz prihlasen podle cookies 
      self.send( self.rfc.RPL_WELCOME, motd %( self.user.username, self.user.me, version ))
      self.send( self.rfc.RPL_ENDOFMOTD, ":End of MOTD" )
      return True 
    else:
      return False
  
  ''' Kontrola jeslti je v mistnosti '''
  def isInRoom(self, room, rtn=False):
    for croom in self.user.rooms:
      if int(room) == int(croom.id):
        if rtn:
          return croom
        else:
          return True
    return False
  
  def joinToRoom(self, room_id, Key = None):
    response = self.getUrl( "%s/%s?id=%s" % ( self.system.url, "join", room_id ) )
    data = json.loads(response)
    return data

  
  def getRoomUsers(self, room_id):
    response = self.getUrl( "%s/%s?id=%s" %(self.system.url, "get-users", room_id) )
    data = json.loads(response)
    return data

  # @todo Dodelat mody mistnosti
  def part(self,room_id):
    croom = self.isInRoom(room_id, True) 
    if not croom == False:  
      self.user.rooms.remove(croom)
    try:
      self.socket.send( ":%s %s #%s :\n" %( self.user.nick, self.rfc.RPL_PART, room_id ) )
    except:
      pass
    self.getUrl( "%s/%s?id=%s" %(self.system.url, "part", room_id) )

  '''
    Zakladatel - +q ~
    Admin - +a 
    SS - +o @
    DS - +h %
    Girl -  +
  '''  
  def userOPStatus(self, user):
    if user['isOwner']:
      return "@"
    elif user['isHalfOP']:
      return "%"
    elif user['isOP']:
      return "@"
    elif user['sex'] == "girls":
      return "+"
    else:
      return "" 
  
  def sendText( self, text, room_id, target ):
    postdata = "roomId=%s&text=%s&target=%s" %(room_id, text, target)
    response = self.postUrl( "%s/%s" %(self.system.url, "post-text"), postdata )
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
      cmd = string.split(cmd_array.strip(), " ")
      command = cmd[0].upper()
      #log("CMD %s" %( cmd[0]))
      
      if command == "NICK":
        self.user.nick = cmd[1]
        if self.user.password != "":
          self.user.login = self.checkLogin()
         
      elif command == "USER":
        self.user.username = cmd[1]
        if self.user.password != "":
          self.user.login = self.checkLogin()
         
      elif command == "PASS":
        self.user.password = cmd[1]
        if self.user.username == "":
          self.send(self.rfc.ERR_NOLOGIN, "%s: User not logged in. Use /USER" % (self.user.me))
        elif self.user.nick == "":
          self.send(self.rfc.ERR_NOLOGIN, "%s: User not logged in. Use /NICK" % (self.user.me))
        else:
          self.user.login = self.checkLogin()
         
      elif command == "JOIN":
        room = cmd[1].replace('#', '')
        rooms = string.split(room, ",")
        
        if not self.user.login:
          self.send(self.rfc.ERR_NOLOGIN, "%s: User not logged in" % (self.user.me))
          return False

        for room in rooms:
          
          inRoom = self.isInRoom(room)
          
          data = self.joinToRoom(room)
          #log("JOIN to %s" %( room ))
          
          if data['code'] == 403:
            self.send( self.rfc.ERR_BANNEDFROMCHAN, "#%s :Cannot join channel" %( data['id'].encode("utf8") ) ) 
            self.send( self.rfc.RPL_NOTICE, ":%s" % ( data['message'].encode("utf8") ) )
          elif data['code'] == 200:
            getusers = self.getRoomUsers( room )
            users = "";
            for user in getusers:
              users = "%s%s%s " %(users, self.userOPStatus(user), user['nick'].encode("utf8") )
            
            if not inRoom:
              nowroom = roomstruct()
              nowroom.id = int(data['id']) 
              self.user.rooms.append(nowroom)
            
            self.send( self.rfc.RPL_JOIN, "#%s" %(data['id'].encode("utf8")) )
            self.send( self.rfc.RPL_TOPIC, "#%s :%s" %(data['id'].encode("utf8"), data['topic'].encode("utf8")) )
            self.send( self.rfc.RPL_NAMREPLY, "= #%s :%s" %( data['id'].encode("utf8"), users ) )
            self.send( self.rfc.RPL_ENDOFNAMES, "#%s :End of /NAMES list" %(room) )
         
      elif command == "PART":
        if len(cmd) < 2:
          self.send( self.rfc.ERR_NEEDMOREPARAMS, "%s :Not enough parameters\n" % ( command ))
        else:
          room_id = cmd[1].lstrip('#')
          self.part(room_id)
      
      elif command == "NAMES":
        room_id = cmd[1].lstrip('#')
        for room in self.user.rooms:
          if int(room_id) == int(room.id):
            getusers = self.getRoomUsers( room.id )
            users = ""
            for user in getusers:
              users = "%s%s%s " %(users, self.userOPStatus(user), user['nick'].encode("utf8") )
            self.send( self.rfc.RPL_NAMREPLY, "= #%s :%s" %( room.id, users ) )
            self.send( self.rfc.RPL_ENDOFNAMES, "#%s :End of /NAMES list" %(room.id) )
            return True
        self.send(self.rfc.ERR_NOSUCHCHANNEL, "#%s :No such channel\n" % (room_id))
      
      elif command == "PING":
        if len(cmd) >= 2:
          self.socket.send(":%s PONG :%s\n" % (self.user.me, cmd[1]))
        else :
          self.socket.send(":%s PONG %s\n" % (self.user.me, self.user.me))

      elif command == "LIST":
        rooms = self.system.getRooms()
        self.send(self.rfc.RPL_LISTSTART, "Channels :Users Name")
        for room in rooms:
          self.send(self.rfc.RPL_LIST, "#%d %d :%s" % ( room['id'], room['online'], room['nazev'].encode("utf8") ) )
        self.send(self.rfc.RPL_LISTEND, "END of /List")

      elif command == "MODE": # @todo Dodelat mody mistnosti
        self.send(self.rfc.RPL_CHANNELMODEIS, "%s +%s" % ( cmd[1], "tn" ))

      elif command == "WHO": # @todo Fixnout /WHO
        users = self.getRoomUsers( cmd[1].lstrip('#') )
        for user in users:
          self.send( self.rfc.RPL_WHOREPLY, "#%s %s %s %s %s H :0 %s" 
          %( 
              cmd[1].lstrip('#'), 
              user['nick'].encode("utf8"), 
              user['sex'].encode("utf8"), 
              self.user.me, 
              user['nick'].encode("utf8"), 
              user['nick'].encode("utf8")
          ))
        self.send( self.rfc.RPL_ENDOFWHO, ":End of /WHO list." )
      
      elif command == "PRIVMSG" or command == "NOTICE" and len(cmd[2]) > 0:
        
        if command == "NOTICE":
          cmd[2] = ":Notice: %s" %cmd[2][1:]
        
        if cmd[1][0] == "#":
          isPM = False
        else:
          isPM = True
        
        if not cmd[2].startswith(":"):
          cmd[2] = ":%s" %(cmd[2])
          
        text = ' '.join(cmd[2:])[1:]
        
        if (text.find("VERSION") !=-1) and (len(text) > 20):
          text = text + version
        elif cmd[2].find("PING") == 2:
          text = "/m %s \xc2PING %s" % (cmd[1], cmd[3].replace("\x01","\xc2"))
        elif cmd[2].find("PONG") == 2:
          text = "/m %s \xc2PONG %s" % (cmd[1], cmd[3].replace("\x01","\xc2"))
          log(text)
        
        
        msg_len = 390
        msgArray = [text[i:i+msg_len] for i in range(0, len(text), msg_len)]
        
        for msg in msgArray:
          if isPM:
            msg = "/m %s %s" % (cmd[1], msg)
            r = self.user.rooms[0]
            roomId = r.id
          else:
            roomId = cmd[1][1:]
            
          data = self.sendText( msg, roomId, cmd[1] )
          
      elif command == "KICK":
        if len(cmd) == 3:
          self.sendText("/kick " + cmd[2], cmd[1].lstrip('#')) # nick, room
        elif len(cmd) > 3:
          if not cmd[3].startswith(":"):
            cmd[3] = ":%s" % (cmd[3])
          reason = ' '.join(cmd[3:])[1:] # dvojtecku nechceme
          self.sendText("/kick %s %s" % (cmd[2], reason), cmd[1].lstrip('#')) # nick, room
        else:
          self.send(self.rfc.ERR_NEEDMOREPARAMS ,"%s :Not enough parameters\n" % ("KICK"))
      
      elif command == "QUIT" or command == "QUIT2":
        if command == "QUIT":
          for room in self.user.rooms:
            self.part(room.id)
          self.connection = False
          self.parent.running = False
        else:
          for r in self.user.rooms:
            self.user.rooms.remove(r)
          self.parent.running = False
          self.connection = False

      elif command != "":
        self.send( self.rfc.ERR_UNKNOWNCOMMAND, ":%s Unknown command" %(cmd[0]) )
        
  def send(self, _id, msg):
    log("SENDING: %s -> %s" %(_id,msg))
    if _id == "JOIN":
      self.socket.send(":%s %s %s\n"  %(self.user.username, _id, msg)  )
    elif _id == "PRIVMSG":
      self.socket.send(":%s %s %s\n" %(self.user.username, _id, msg))
    else:
      self.socket.send(":%s %s %s %s\n"  %(self.user.me, _id, self.user.nick, msg)  )
    


class SocketHandler(threading.Thread):
  def __init__ (self, socket, address):
    threading.Thread.__init__(self)
    self.socket = socket
    self.address = address
    self.running = True
  def run (self):
    log("Prijato spojeni z %s" % (self.address[0]))
    instance = Chatujme(self.socket, self.address[0], self);    

    while self.running:
      timestamp = int(time.time())
      try:
        ircdata = instance.socket.recv(2**13)
        if (instance.parse(ircdata,timestamp) == 2):
          break
      except:
        log("Spojeni z %s uzavreno." %(self.address[0]))
        if traceback:
          traceback.print_exc()
        for room in instance.user.rooms:
          instance.part(room.id)
        instance.connection = False
        return False
        
      if instance.user.nick != "" and instance.user.login and not instance.user.reading:
        try:
          world.vlakna.append(getMessages(instance, self.socket))
          world.collector.start_threads()
          instance.user.reading = True
        except:
          if traceback:
            traceback.print_exc()
          return False
          
    log("Spojeni z %s uzavreno." %(self.address[0]))
    self.socket.close()
      


def log(text):
  print "[%s] %s" % (time.strftime('%Y/%m/%d %H:%M:%S'), text)

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind((BIND, PORT))
s.listen(50)

if not world.collector:
  world.collector = Collector()
  world.collector.start()

log("ChatujmeGW %s, nasloucham na portu %s:%s" %(version, BIND, PORT))

while world.collector.running:
  try:
    connection, address = s.accept()
    if len(world.vlakna) <= 378:
      world.vlakna.append(SocketHandler(connection,address))
    else:
      connection.close()
    world.collector.start_threads()
    
  except (KeyboardInterrupt, EOFError):
    world.collector.running = False
    s.close()
    log("Vypinam...", 1)
    break
