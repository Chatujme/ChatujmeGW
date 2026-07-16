"""IRC protocol constants: RFC numeric codes and the MOTD banner."""


class IRC_RFC:
    RPL_WELCOME = "001"
    RPL_YOURHOST = "002"
    RPL_CREATED = "003"
    RPL_MYINFO = "004"
    RPL_VERSION = "351"
    RPL_MOTDSTART = "375"
    RPL_MOTD = "372"
    RPL_ENDOFMOTD = "376"
    RPL_LISTSTART = "321"
    RPL_LIST = "322"
    RPL_LISTEND = "323"
    RPL_TOPIC = "332"
    RPL_NOTOPIC = "331"
    ERR_NOSUCHNICK = "401"
    ERR_NOSUCHCHANNEL = "403"
    ERR_UNKNOWNCOMMAND = "421"
    ERR_NEEDMOREPARAMS = "461"
    ERR_NOLOGIN = "444"
    ERR_BANNEDFROMCHAN = "474"
    RPL_CHANNELMODEIS = "324"
    RPL_WHOREPLY = "352"
    RPL_ENDOFWHO = "315"
    RPL_NAMREPLY = "353"
    RPL_ENDOFNAMES = "366"
    RPL_USERHOST = "302"
    RPL_WHOISUSER = "311"
    RPL_WHOISCHANNELS = "319"
    RPL_WHOISSERVER = "312"
    RPL_WHOISHOST = "378"
    RPL_WHOISIDLE = "317"
    RPL_ENDOFWHOIS = "318"
    RPL_AWAY = "301"
    RPL_UNAWAY = "305"
    RPL_NOWAWAY = "306"
    RPL_NOTICE = "NOTICE"
    RPL_JOIN = "JOIN"
    RPL_PART = "PART"
    RPL_MODE = "MODE"
    RPL_KICK = "KICK"
    RPL_PRIVMSG = "PRIVMSG"


# MOTD lines (without empty lines for RFC compliance)
MOTD_LINES = [
    "  .g8\"\"\"bgd` MM             Welcome to Chatujme.cz",
    ".dP'     `M  MM             Logged in as {user}@{sex}.{host}",
    "dM'       `  MMpMMMb.",
    "MM           MM    MM       Gateway version {version}",
    "MM.          MM    MM",
    "`Mb.     ,'  MM    MM",
    "  `\"bmmmd' .JMML  JMML.",
]
