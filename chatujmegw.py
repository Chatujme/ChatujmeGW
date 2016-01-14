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

import copy, os, re, socket, string, sys, threading, time, urllib, urllib2, random, json, cookielib, argparse
reload(sys)  
sys.setdefaultencoding('utf8')

PORT = 6667 #Default IRC port
BIND = "0.0.0.0" #Bind to all IP
version = 1.7
ua = 'ChatujmeGW/v%s (%s %s) Python %s' %(str(version), sys.platform, os.name, sys.version.split(" ")[0] )

parser = argparse.ArgumentParser(description='ChatujmeGW - v'+str(version))
parser.add_argument('--port',type=int, help="Default port 6667")
parser.add_argument('--listen',help="Bind gateway. Default 0.0.0.0")
parser.add_argument('--debug',help="Debug/Verbose print", type=int)
args = parser.parse_args()

verboseThreads = False
if args.port:
  PORT = args.port
if args.listen:
  BIND = args.listen
if args.debug:
  import traceback
  if args.debug == 2:
    verboseThreads = True
else:
  traceback = False

#force debug
#import traceback


try:
  path = os.path.dirname(os.path.abspath(__file__))
except NameError:  # We are the main py2exe script, not a module
  path = os.path.dirname(os.path.abspath(sys.argv[0]))


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
  RPL_WELCOME = "001"
  RPL_ENDOFMOTD = 376
  RPL_YOURHOST = "002"
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
  RPL_KICK = "KICK"
  RPL_PRIVMSG = "PRIVMSG"
    

class world:
  vlakna = []
  collector = None

class uzivatel:
  username = ""
  nick = ""
  password = ""
#  rooms = []
  me = "chatujme.cz"
  login = False
  sex = "boys"
  reading = False
  cookieJar = cookielib.LWPCookieJar(path + "/cookies.txt")
  urlfetcher = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookieJar), urllib2.HTTPSHandler(debuglevel=1))
  settingsShowPMFrom = True
  timer = 5
  idler_enable = False
  idler_timer = 2400 #40min
  idler_text = [ ".", "..", "Jsem AFK" ]
#  idler_lastsend = 0
  showSmiles = 1 #0 - Schovat, 1 - Text podoba, 2 - Url  

class userInRoom:
  nick = ""
  sex = ""
  
class roomstruct:
  id = None
  nick = ""
  users = [ ]
  lastId = 0
  lastMess = ""
  firstLoad = True
  idler_lastsend= 0


def dump(obj):
  for attr in dir(obj):
    print "obj.%s = %s" % (attr, getattr(obj, attr))

class Collector (threading.Thread):
  def __init__ (self):
    threading.Thread.__init__(self)
    self.running = True
    if traceback:
      if verboseThreads:
        log("collector, init")
  def run (self):
    if traceback:
      if verboseThreads:
        log("collector, start")
    while self.running:
      vlaken = len(world.vlakna)
      for vlakno in world.vlakna:
        if not vlakno.isAlive() and vlakno._Thread__started.is_set():
          world.vlakna.remove(vlakno)
          if traceback:
            if verboseThreads:
              log("collector, purging %s" %(vlakno))
          del vlakno
          vlaken -= 1
      if traceback:
        if verboseThreads:
          log("collector, all clear (%s threads)" %(vlaken))
      time.sleep(5)
    
    # shutdown
    for vlakno in world.vlakna:
      vlakno.running = False # shodim zbytek vlaken, aby se to vubec vyplo
    if traceback:
      log("collector, shutdown")
  def start_threads (self):
    try:
      for vlakno in world.vlakna:
        if not vlakno._Thread__started.is_set():
          vlakno.start()
    except:
      log("Vlakno odmita startovat, pravdepodobne dosla pamet.")


class getMessages (threading.Thread):
  def __init__ (self, inst, socket):
    threading.Thread.__init__(self)
    self.inst = inst
    self.running = True
    
  def run (self):
    while self.running and self.inst.connection:
    
      if len(self.inst.rooms) == 0:
        time.sleep(5)
        continue
      if not self.inst.connection:
        return False

      for room in self.inst.rooms:
        
        try:
          response = self.inst.getUrl( "%s/%s?id=%s&from=%d" %(self.inst.system.url, "get-messages", room.id, int(room.lastId) ) )
          data = json.loads(response)
        except:
          #if traceback:
          #  traceback.print_exc()
          data = { 'mess' : [ ] }

        try:
          for mess in data['mess']:
          
            if int(room.lastId) >= int(mess['id']):
              continue
            if mess['nick'].lower() == self.inst.user.username.lower():
              continue
            if mess['nick'].lower() == self.inst.user.nick.lower():
              continue
            ''' Pri JOINu nechceme nacist zadne zpravy zpetne '''              
            #if not room.lastMess == "" and room.lastMess == mess['zprava']:
            #  continue

            room.lastId = mess['id']
            room.lastMess = mess['zprava']

            if room.firstLoad:
              continue

            msg = self.inst.cleanHighlight(mess['zprava'].encode("utf8"))
            msg = self.inst.cleanSmiles( msg )
            msg = self.inst.cleanUrls( msg )

            if mess["typ"] == 0: #Public
              self.inst.send(None, ":%s %s #%s :%s\n" %(self.inst.hash(mess['nick'].encode("utf8"),room.id), self.inst.rfc.RPL_PRIVMSG, room.id, msg) )
            elif mess["typ"] == 1: #PM
              self.inst.send(None, ":%s %s %s :%s\n" %(self.inst.hash(mess['nick'].encode("utf8"),room.id), self.inst.rfc.RPL_PRIVMSG, mess["komu"].encode("utf8"), msg) )
            elif mess["typ"] == 2: #System
              t = msg.replace("'","")

              u = copy.deepcopy(userInRoom())

              if "vstoupil" in t or "vstoupila" in t:
                ret = re.findall(r'.+\s(.+)\svstoupi(la|l)', msg)[0]
                nick = ret[0]
                u.nick = nick
                if ret[1] == "la":
                  u.sex = "girls"
                else:
                  u.sex = "boys"
                r = self.inst.isInRoom(room.id, True)
                if r:
                  r.users.append(u)
                self.inst.send(None, ":%s %s #%s :%s\n" %( self.inst.hash(nick,room.id), self.inst.rfc.RPL_JOIN, room.id, msg )  )

              elif "odešel" in t or "odešla" in t:
                nick = re.findall(r'.+\s(.+)\s(odešel|odešla)', msg)[0]
                msg = re.sub(r'(.*?):\s*','',msg)
                partmess = "part"
                if nick[1] == "odešel":
                  partmess = "Odesel"
                if nick[1] == "odešla":
                  partmess = "Odesla"
                self.inst.send(None, ":%s %s #%s :%s\n" % (self.inst.hash(nick[0],room.id), self.inst.rfc.RPL_PART, room.id, partmess)  )

              elif "smazal" in t:
                nick = re.findall(r'ce\s(.+)\ssmazal', msg)[0]
                msg = re.sub(r'(.*?):\s*','',msg)
                self.inst.send(None, ":%s %s #%s :%s\n" %( self.inst.user.me, self.inst.rfc.RPL_NOTICE, room.id, msg ))

              elif "odstranil" in t:
                nick = re.findall(r'ce\s(.+)\sodstranil\szprávy\sod\s(.+)\sze', msg)[0]
                target = nick[1]
                nick = nick[0]
                msg = re.sub(r'(.*?):\s*','',msg)
                self.inst.send(None,":%s %s #%s :%s\n" %(self.inst.user.me, self.inst.rfc.RPL_NOTICE, room.id, msg ))
              
              elif "odstraněn" in t:
                nick = re.findall(r'.+e(lka|l)\s(.+)\sby(la|l)\s', msg)[0]
                nick = nick[1]
                self.inst.send(None, ":%s %s #%s :%s\n" % (self.inst.hash(nick, room.id), self.inst.rfc.RPL_PART, room.id, 'timeout')  )

              elif "vykopnutý" in t:
                msg = re.sub(r'(.*?):\s*','',msg)
                self.inst.send(None,":%s %s #%s :%s\n" %(self.inst.user.me, self.inst.rfc.RPL_NOTICE, room.id, msg ))
              
              elif "vykopnut" in t:
                nick = re.findall(r'(lka|l)\s(.+)\sby(la|l)\svykopnu(ta|t)\sz\smístnosti.\sVykop(l|nul)\s(jej|ji)\s(.+)\sz\sdůvodu:\s(.*?)\.$',msg)[0]
                target = nick[1]
                duvod = nick[7]
                if not nick[7]:
                  duvod = "Důvod nebyl zadán"
                else:
                  duvod = nick[7]
                nick = nick[6]
                self.inst.send(None, ":%s %s #%s %s :%s\n" %( self.inst.hash(nick,room.id), self.inst.rfc.RPL_KICK, room.id, target, duvod ))

              elif "opět povolený" in t:
                nick = re.findall(r'el\s(.+)\smá',msg)[0]
                msg = re.sub(r'(.*?):\s*','',msg)
                self.inst.send(None,":%s %s #%s :%s\n" %(self.inst.user.me, self.inst.rfc.RPL_NOTICE, room.id, msg ))

              elif "předal" in t or "předala" in t:
                target = re.findall(r'.+správce\s(.+)$', msg)[0]
                nick = re.findall(r'.+e(lka|l)\s(.+)\spředa(l|la)\ssprávce\s(.+)', msg)[0]
                target = nick[3]
                nick = nick[1]
                self.inst.send(None, ":%s %s #%s -h %s\n" % (self.inst.hash(nick,room.id), self.inst.rfc.RPL_MODE, room.id, self.inst.hash(nick,room.id))  )
                self.inst.send(None, ":%s %s #%s +h %s\n" % (self.inst.hash(nick,room.id), self.inst.rfc.RPL_MODE, room.id, self.inst.hash(target,room.id))  )
                self.inst.reloadUsers(room.id)

              else:
                msg = re.sub(r'(.*?):\s*','',msg)
                self.inst.send(None, ":%s %s #%s :%s\n" %(self.inst.hash(mess['nick'].encode("utf8"),room.id), self.inst.rfc.RPL_PRIVMSG, room.id, msg) )
            elif mess["typ"] == 3: #WALL
              if self.inst.user.settingsShowPMFrom:
                self.inst.send(None, ":%s %s %s :[Z kanálu %s #%d ] %s\n" %(self.inst.hash(mess['nick'].encode("utf8"),room.id), self.inst.rfc.RPL_PRIVMSG, mess["komu"].encode("utf8"), mess['rname'].encode("utf8"), mess['rid'], msg) )
              else:
                self.inst.send(None, ":%s %s %s :%s\n" %(self.inst.hash(mess['nick'].encode("utf8"),room.id), self.inst.rfc.RPL_PRIVMSG, mess["komu"].encode("utf8"), msg) )
            
        except:
          
            previousTraceback = sys.exc_info()
            try:

              # If user part from another device/webchat
              if data['code'] == "404":
                self.inst.send(None, ":%s %s #%s\n" %( self.inst.user.me, self.inst.rfc.RPL_PART, room.id ))
                log("Odchod %s z mistnosti z jineho umisteni" %(self.inst.user.username))
                self.inst.part(room.id)
                continue
              elif data['code'] == "403": #Kicked
                self.inst.send(None, ":%s %s #%s\n" %( self.inst.user.me, self.inst.rfc.RPL_PART, room.id ))
                log("Odchod %s z mistnosti z jineho umisteni" %(self.inst.user.username))
                self.inst.part(room.id)
                continue
              # If user session expired
              elif data['code'] == "401":
                self.inst.user.login = False
                self.inst.send(None,":%s %s #%s :%s\n" %(self.inst.user.me, self.inst.rfc.RPL_NOTICE, room.id, "Pokus o re-login. Příhlášení expirovalo.." ))
                if self.inst.user.username == "":
                  self.inst.send(self.inst.rfc.ERR_NOLOGIN, "%s: User not logged in. Use /USER" % (self.inst.user.me))
                elif self.inst.user.nick == "":
                  self.inst.send(self.inst.rfc.ERR_NOLOGIN, "%s: User not logged in. Use /NICK" % (self.inst.user.me))
                else:
                  self.inst.user.login = self.inst.checkLogin()
                
                if not self.inst.user.login:
                  self.inst.send(None,":%s %s #%s :%s\n" %(self.inst.user.me, self.inst.rfc.RPL_NOTICE, room.id, "Pokus o re-login se nezdařil.." ))
                  log("Pokus o prihlaseni %s se nezdaril, cekam 10s" %(self.inst.user.username))
                  time.sleep(10)
                else:
                  self.inst.send(None,":%s %s #%s :%s\n" %(self.inst.user.me, self.inst.rfc.RPL_NOTICE, room.id, "Pokus o re-login byl úspěšný" ))
                  log("Pokus o prihlaseni %s se zdaril" %(self.inst.user.username))

              else: # Neznamy stav..
                if traceback and previousTraceback:
                  print "----- Previous -----"
                  exc_type, exc_value, exc_traceback = previousTraceback
                  traceback.print_exception(exc_type, exc_value, exc_traceback) 
                if traceback:
                  print "----- This -----"
                  traceback.print_exc()
                  print "----- Data -----"
                  print data
            
            except:
              if traceback and previousTraceback:
                print "----- Previous -----"
                exc_type, exc_value, exc_traceback = previousTraceback
                traceback.print_exception(exc_type, exc_value, exc_traceback) 
              if traceback:
                print "----- This -----"
                traceback.print_exc()
              pass 
            time.sleep(1)
            pass

        myTime = time.time()
        if (myTime - room.idler_lastsend) >= self.inst.user.idler_timer and self.inst.user.idler_timer != 0 and self.inst.user.idler_enable:
          self.inst.send(None,":%s %s #%s :Odeslan idler [ %s ]\n" %( self.inst.user.me, self.inst.rfc.RPL_NOTICE, room.id, room.idler_lastsend ))
          room.idler_lastsend = time.time()
          self.inst.sendText( random.choice( self.inst.user.idler_text ), room.id, room.id)

      ''' Pri JOINu nacteme seznam uzivatelu '''              
      if room.firstLoad:          
        self.inst.reloadUsers(room.id)
        room.firstLoad = False

      time.sleep(self.inst.user.timer)
    


class ChatujmeSystem:
  def __init__ (self, parent):
    self.url = "http://api.chatujme.cz/irc"
    self.parent = parent
  def getRooms(self):
    response = self.parent.getUrl( "%s/%s" %(self.url, "get-rooms") )
    data = json.loads(response)
    return data
    

class Chatujme:
  def __init__ (self, mySocket, myAdress, handler):
    self.socket = mySocket
    self.adress = myAdress
    self.user = copy.deepcopy(uzivatel())
    self.system = ChatujmeSystem(self)
    self.connection = True
    self.rooms = [ ]
    self.parent = handler
    self.rfc = ircrfc()
    
  def cleanHighlight(self, msg):
    return re.sub("<span style='background:#eded1a'>([^<]+)</span>", "\\1", msg)

  def cleanUrls(self, msg):
    return re.sub('<a href="([^"]+)" target="_blank">([^<]+)</a>', "\\1", msg)
  
  def hash(self, nick, room_id):
    try:
      room = self.isInRoom(room_id, True);
      for u in room.users:
        if u.nick == nick:
          return "%s!%s@%s" %(nick, nick, u.sex.encode("utf8"))
      return nick
    except:
      if traceback:
        traceback.print_exc()
      return nick
  
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
    self.user.urlfetcher.addheaders = [('User-agent', ua)]
    response = self.user.urlfetcher.open(url)
    self.user.cookieJar.save(ignore_discard=True)
    return response.read()
  
  ''' Funkce na POST '''
  def postUrl(self, url, postdata):
    self.user.urlfetcher.addheaders = [('User-agent', ua)]
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
    
    ''' Zmena cookie souboru podle prihlasenyho usera'''
    self.user.cookieJar = cookielib.LWPCookieJar("%s/cookies_%s.txt" % ( path, self.user.username ))
    self.user.urlfetcher = urllib2.build_opener(urllib2.HTTPCookieProcessor(self.user.cookieJar), urllib2.HTTPSHandler(debuglevel=1))
    
    postdata = "username=%s&password=%s" %( self.user.username, self.user.password )
    response = self.postUrl ( "%s/%s" % (self.system.url, "check-login"), postdata )
    data = json.loads(response)

    if data['code'] == 401: #Spatny login
      self.send(self.rfc.ERR_NOLOGIN, "%s: %s" % (self.user.username, data['message'].encode("utf8") ) )
      return False
    elif data['code'] == 200: #Nove prihlaseni
      self.send( self.rfc.RPL_WELCOME, motd %( self.user.username, self.user.me, version ))
      self.send( self.rfc.RPL_ENDOFMOTD, ":End of MOTD" )
      log("Prihlasen user %s" %( self.user.username) )
      return True
    elif data['code'] == 201: #Uzivatel je jiz prihlasen podle cookies 
      self.send( self.rfc.RPL_WELCOME, motd %( self.user.username, self.user.me, version ))
      self.send( self.rfc.RPL_ENDOFMOTD, ":End of MOTD" )
      log("Prihlasen user %s" %( self.user.username) )
      return True 
    else:
      return False
  
  ''' Kontrola jeslti je v mistnosti '''
  def isInRoom(self, room, rtn=False):
    for croom in self.rooms:
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
    
    r = self.isInRoom(room_id, True)
    if r:
      r.users = [ ]
      for user in data:
        u = copy.deepcopy(userInRoom())
        u.nick = user["nick"]
        u.sex = user["sex"]
        r.users.append(u)
    
    return data

  # @todo Dodelat mody mistnosti
  def part(self,room_id):
    croom = self.isInRoom(room_id, True) 
    if not croom == False:  
      self.rooms.remove(croom)
    try:
      self.send(None, ":%s %s #%s :\n" %( self.user.nick, self.rfc.RPL_PART, room_id ) )
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
    elif user['isOP']:
      return "@"
    elif user['isHalfOP']:
      return "%"
    elif user['sex'] == "girls":
      return "+"
    else:
      return "" 
  
  def sendText( self, text, room_id, target ):
    postdata = "roomId=%s&text=%s&target=%s" %(room_id, urllib.quote_plus(text), target)
    response = self.postUrl( "%s/%s" %(self.system.url, "post-text"), postdata )
    try:
      data = json.loads(response)
    except:
      data = [ ]
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
      if command == "":
        continue

      if traceback:
        log("RECEIVING: %s" %( cmd))
      
      if command == "NICK":
        if self.user.login:
          self.send(None,":%s %s %s: %s\n" %(self.user.me, self.rfc.RPL_NOTICE, self.user.username,"Uživatel %s je již přihlášen" % (self.user.username) ))
          continue
        self.user.nick = cmd[1]
        if self.user.password != "":
          self.user.login = self.checkLogin()
         
      elif command == "USER":
        if self.user.login:
          self.send(None,":%s %s %s: %s\n" %(self.user.me, self.rfc.RPL_NOTICE, self.user.username,"Uživatel %s je již přihlášen" % (self.user.username) ))
          continue
        self.user.username = cmd[1]
        if self.user.password != "":
          self.user.login = self.checkLogin()
         
      elif command == "PASS":
        if self.user.login:
          self.send(None,":%s %s %s: %s\n" %(self.user.me, self.rfc.RPL_NOTICE, self.user.username,"Uživatel %s je již přihlášen" % (self.user.username) ))
          continue
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
          if traceback:
            log("JOIN to %s" %( room ))
          
          if data['code'] == 403:
            self.send( self.rfc.ERR_BANNEDFROMCHAN, "#%s :Cannot join channel" %( data['id'].encode("utf8") ) ) 
            self.send( self.rfc.RPL_NOTICE, ":%s" % ( data['message'].encode("utf8") ) )
          elif data['code'] == 200:
            getusers = self.getRoomUsers( room )
            users = "";
            for user in getusers:
              users = "%s%s%s " %(users, self.userOPStatus(user), user['nick'].encode("utf8") )
            
            if not inRoom:
              nowroom = copy.deepcopy(roomstruct())
              nowroom.id = int(data['id'])
              nowroom.nick = self.user.username
              nowroom.idler_lastsend = time.time()
              self.rooms.append(nowroom)
            
            self.send( self.rfc.RPL_JOIN, "#%s" %(data['id'].encode("utf8")) )
            self.send( self.rfc.RPL_TOPIC, "#%s :[%s] %s" %(data['id'].encode("utf8"), data['nazev'].encode("utf8"), data['topic'].encode("utf8")) )
            self.send( self.rfc.RPL_NAMREPLY, "= #%s :%s" %( data['id'].encode("utf8"), users ) )
            self.send( self.rfc.RPL_ENDOFNAMES, "#%s :End of /NAMES list" %(room) )
         
      elif command == "PART":
        if len(cmd) < 2:
          self.send( self.rfc.ERR_NEEDMOREPARAMS, "%s :Not enough parameters\n" % ( command ))
        else:
          room_id = cmd[1].lstrip('#')
          self.part(room_id)
          
      elif command == "TOPIC":
        room_id = cmd[1].lstrip('#')
        try:
          response = self.getUrl( "%s/%s?id=%d" %( self.system.url, "get-room", int(room_id) ) )
          data = json.loads(response)
          self.send( self.rfc.RPL_TOPIC, "#%s :%s" %(data['id'], data['topic'].encode("utf8")) )
        except:
          if traceback:
            traceback.print_exc()

      
      elif command == "NAMES":
        try:
          room_id = cmd[1].lstrip('#')
          for room in self.rooms:
            if int(room_id) == int(room.id):
              getusers = self.getRoomUsers( room.id )
              users = ""
              for user in getusers:
                users = "%s%s%s " %(users, self.userOPStatus(user), user['nick'].encode("utf8") )
              self.send( self.rfc.RPL_NAMREPLY, "= #%s :%s" %( room.id, users ) )
              self.send( self.rfc.RPL_ENDOFNAMES, "#%s :End of /NAMES list" %(room.id) )
              return True
          self.send(self.rfc.ERR_NOSUCHCHANNEL, "#%s :No such channel\n" % (room_id))
        except:
          self.send( self.rfc.ERR_NEEDMOREPARAMS, "%s :Not enough parameters\n" % ( command ))
      
      elif command == "PING":
        try:
          self.getUrl( "%s/%s" %(self.system.url, "ping") )
        except:
          pass
        if len(cmd) >= 2:
          self.send(None, ":%s PONG :%s\n" % (self.user.me, cmd[1]))
        else :
          self.send(None, ":%s PONG %s\n" % (self.user.me, self.user.me))

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
          if traceback:
            log(text)
        
        
        msg_len = 390
        msgArray = [text[i:i+msg_len] for i in range(0, len(text), msg_len)]
        
        for msgx in msgArray:
          for msg in msgx.split("\n"):
            if isPM:
              msg = "/m %s %s" % (cmd[1], msg)
              r = self.rooms[0]
              roomId = r.id
              r.idler_lastsend = time.time()
            else:
              roomId = cmd[1][1:]

            for room in self.rooms:
              if room.id == cmd[1][1:]:
                room.idler_lastsend = time.time()

            data = self.sendText( msg, roomId, cmd[1] )
          
      elif command == "KICK":
        if len(cmd) == 3:
          self.sendText("/kick " + cmd[2], cmd[1].lstrip('#'), cmd[1].lstrip('#')) # nick, room
          self.send(None, ":%s %s #%s %s :%s\n" %( self.hash(self.user.username,cmd[1].lstrip('#')), self.rfc.RPL_KICK, cmd[1].lstrip('#'), cmd[2], "" ))
        elif len(cmd) > 3:
          if not cmd[3].startswith(":"):
            cmd[3] = ":%s" % (cmd[3])
          reason = ' '.join(cmd[3:])[1:] # dvojtecku nechceme
          self.sendText("/kick %s %s" % (cmd[2], reason), cmd[1].lstrip('#'), cmd[1].lstrip('#')) # nick, room
          self.send(None, ":%s %s #%s %s :%s\n" %( self.hash(self.user.username,cmd[1].lstrip('#')), self.rfc.RPL_KICK, cmd[1].lstrip('#'), cmd[2], reason ))
        else:
          self.send(self.rfc.ERR_NEEDMOREPARAMS ,"%s :Not enough parameters\n" % ("KICK"))
          
      
      elif command == "SET" and len(cmd) >= 2:
        message = None
        
        if cmd[1].upper() == "TIMER":
          try:
            num = int(cmd[2])
            self.user.timer = num
            message = "Prodleva mezi aktualizacemi nastavena na %s sekund." % (num)
          except:
            message = "SET TIMER cislo - nastaveni prodlevy mezi aktializaci chatu, aktualni hodnota: %s" % (self.user.timer)
        
        if cmd[1].upper() == "IDLER_TIMER":
          try:
            num = int(cmd[2])
            if self.user.idler_enable:
              if num <= 1800:
                message = "Udrzovac nelze nastavit pod 1800 vterin (30min)"
              else:
                self.user.idler_timer = num
                message = "Udrzovac nastaven na %s sekund." % (num)
            else:
                message = "Idler neni zapnuty, pouzijte nejdrive prosim /SET IDLER_ENABLE 1"
          except:
            message = "SET IDLER_TIMER cislo - nastaveni prodlevy udrzovacich vet, aktualni hodnota: %s" % (self.user.idler_timer)

        if cmd[1].upper() == "IDLER_ENABLE":
          try:
            num = int(cmd[2])
            if num == 1:
              self.user.idler_enable = True
              message = "Idler aktivován."
            else:
              self.user.idler_enable = False
              message = "Idler deaktivován."
          except:
            message = "SET IDLER_ENABLE 1|0 - aktivace idleru, aktualni hodnota: %s" % (self.user.idler_enable)
        
        if message:
          self.send(self.rfc.RPL_NOTICE, ":%s" %(message) )

      elif command == "LOAD":
        try:
          filename = cmd[1]
          f = open(filename, "r")
          
          for line in f:
            l=line.strip()
            if l.startswith("#"):
              continue
            timestamp = time.time()
            log("Nastavuji %s" %(l))
            self.parse(l, timestamp)
          f.close()
          message = "Nastaveni ze souboru %s nacteno" %(filename)
        except:
          if traceback and verboseThreads:
            traceback.print_exc()
          try:
            if cmd[1]:
              message = "Soubor nebyl %s neexistuje nebo ho nelze nacist" %(cmd[1])
          except:
            message = "Soubor nebyl zadan"
          

        if message:
          self.send(self.rfc.RPL_NOTICE, ":%s" %(message) )
        
        
      elif command == "QUIT" or command == "QUIT2":
        if command == "QUIT":
          for room in self.rooms:
            self.part(room.id)
        else:
          for r in self.rooms:
            self.rooms.remove(r)
        self.parent.running = False
        self.connection = False

      elif command != "":
        self.send( self.rfc.ERR_UNKNOWNCOMMAND, ":%s Unknown command" %(cmd[0]) )
        
  def send(self, _id, msg):
    if traceback:
      log("SENDING: %s -> %s" %(_id,msg))
    if _id == None:
      self.socket.send(msg )
    elif _id == "JOIN":
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
    instance.send(None,":%s %s %s: %s\n" %(instance.user.me, instance.rfc.RPL_NOTICE, instance.user.me,"Připojeno z %s, čekám na přihlášení." % (self.address[0]) ))    

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
        for room in instance.rooms:
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
