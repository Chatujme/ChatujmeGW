# ChatujmeGW

IRC Gateway pro [Chatujme.cz](https://chatujme.cz) chat. Umožňuje připojení k Chatujme.cz pomocí standardního IRC klienta.

## Verze

### ChatujmeGW3 (Python 3) - Doporučeno
- **Soubor:** `chatujmegw3.py` / `chatujmegw3.exe`
- **Python:** 3.8+
- **Status:** Aktivně vyvíjeno

### ChatujmeGW (Python 2.7) - DEPRECATED
- **Soubor:** `chatujmegw.py` / `chatujmegw.exe`
- **Python:** 2.7.x
- **Status:** ⚠️ **ZASTARALÉ** - Pouze pro zpětnou kompatibilitu, nebude dále vyvíjeno

## Podporované IRC RFC

Gateway implementuje subset [RFC 1459](https://tools.ietf.org/html/rfc1459):

### Numerické kódy
| Kód | Název | Popis |
|-----|-------|-------|
| 001 | RPL_WELCOME | Uvítací zpráva |
| 002 | RPL_YOURHOST | Info o serveru |
| 003 | RPL_CREATED | Datum vytvoření |
| 004 | RPL_MYINFO | Info o serveru |
| 302 | RPL_USERHOST | Host uživatele |
| 311 | RPL_WHOISUSER | WHOIS info |
| 312 | RPL_WHOISSERVER | WHOIS server |
| 315 | RPL_ENDOFWHO | Konec WHO |
| 317 | RPL_WHOISIDLE | WHOIS idle |
| 318 | RPL_ENDOFWHOIS | Konec WHOIS |
| 319 | RPL_WHOISCHANNELS | WHOIS místnosti |
| 321 | RPL_LISTSTART | Začátek LIST |
| 322 | RPL_LIST | Položka LIST |
| 323 | RPL_LISTEND | Konec LIST |
| 324 | RPL_CHANNELMODEIS | Módy místnosti |
| 331 | RPL_NOTOPIC | Bez tématu |
| 332 | RPL_TOPIC | Téma místnosti |
| 352 | RPL_WHOREPLY | WHO odpověď |
| 353 | RPL_NAMREPLY | Seznam uživatelů |
| 366 | RPL_ENDOFNAMES | Konec NAMES |
| 372 | RPL_MOTD | MOTD řádek |
| 375 | RPL_MOTDSTART | Začátek MOTD |
| 376 | RPL_ENDOFMOTD | Konec MOTD |
| 378 | RPL_WHOISHOST | WHOIS host |
| 401 | ERR_NOSUCHNICK | Nick neexistuje |
| 403 | ERR_NOSUCHCHANNEL | Místnost neexistuje |
| 421 | ERR_UNKNOWNCOMMAND | Neznámý příkaz |
| 444 | ERR_NOLOGIN | Chyba přihlášení |
| 461 | ERR_NEEDMOREPARAMS | Chybí parametry |
| 474 | ERR_BANNEDFROMCHAN | Zakázán vstup |

### Podporované příkazy
`NICK`, `PASS`, `USER`, `JOIN`, `PART`, `PRIVMSG`, `NOTICE`, `MODE`, `KICK`, `WHO`, `WHOIS`, `LIST`, `PING`, `PONG`, `QUIT`

## Spuštění

```bash
# Předkompilovaný EXE
chatujmegw3.exe
chatujmegw3.exe --port 6667 --listen 127.0.0.1 --debug 1

# Python skript
python3 chatujmegw3.py
python3 chatujmegw3.py --port 6667 --listen 0.0.0.0 --debug 1
```

### Parametry
| Parametr | Popis | Výchozí |
|----------|-------|---------|
| `--port` | Port pro naslouchání | 6667 |
| `--listen` | IP adresa pro binding | 0.0.0.0 |
| `--debug` | Debug úroveň (0-2) | 0 |

## Build EXE

```bash
# Instalace PyInstaller
pip install pyinstaller

# Build
pyinstaller --onefile --console --icon=chatujme.ico chatujmegw3.py

# Nebo pomocí spec souboru
pyinstaller chatujmegw3.spec

# Výstup: dist/chatujmegw3.exe
```

## Použití

1. Spusťte `chatujmegw3.exe`
2. V IRC klientu nastavte:
   - **Server:** localhost
   - **Port:** 6667
   - **Nick:** váš nick na Chatujme.cz
   - **Password:** vaše heslo
3. Připojte se a vstupte do místnosti: `/join #12345`

## License

MIT License

## Autor

**LuRy** - [lury@lury.cz](mailto:lury@lury.cz)

Původní projekt založen na [lidegw](http://sourceforge.net/projects/lidegw/)
