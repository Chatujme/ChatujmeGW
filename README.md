# ChatujmeGW

[![Python 3.8+](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](https://www.docker.com/)

IRC Gateway pro [Chatujme.cz](https://chatujme.cz) — připojte se k chatu pomocí libovolného IRC klienta (HexChat, mIRC, irssi, WeeChat a další). Gateway komunikuje s Chatujme.cz API (`api.chatujme.cz/irc`) a překládá IRC protokol na volání interního API.

## Veřejná IRC brána

Chatujme.cz provozuje veřejnou instanci této brány:

| | |
|---|---|
| **Server** | `irc.chatujme.cz` |
| **Port** | `6667` |
| **Port SSL/TLS** | `6697` |

Přihlášení probíhá standardním IRC způsobem — IRC příkazy `NICK`, `USER` a `PASS` s přihlašovacími údaji k účtu na Chatujme.cz. Registrace nových účtů přes IRC není možná, pouze přes web ([chatujme.cz/registrace](https://chatujme.cz/registrace)).

Místnosti se adresují číslem: `/join #12345` (číslo místnosti z webu).

## Doporučené IRC klienty

| Platforma | Klient |
|-----------|--------|
| **Windows** | [HexChat](https://hexchat.github.io/), [mIRC](https://www.mirc.com/) |
| **macOS** | [HexChat](https://hexchat.github.io/), [Textual](https://codeux.com/textual/) |
| **Linux** | [HexChat](https://hexchat.github.io/), [WeeChat](https://weechat.org/), [Irssi](https://irssi.org/) |
| **Android** | [Revolution IRC](https://play.google.com/store/apps/details?id=io.mrarm.irc) |
| **iOS** | [Palaver](https://apps.apple.com/app/palaver/id538073623) |

---

## Self-hosting

### Požadavky

- **Python** 3.8+
- Žádné externí závislosti (pouze stdlib)

### Struktura kódu

```
chatujmegw.py                  launcher (kompatibilita; ekvivalent: PYTHONPATH=src python -m chatujmegw)
src/chatujmegw/
├── cli.py                     argparse + bootstrap procesu
├── config.py                  konstanty a runtime nastavení
├── util.py                    logování, sanitizace, validace
├── state.py                   sdílený stav (registry spojení, rate limiting)
├── models.py                  datové třídy (User, Channel, ChannelMember)
├── numerics.py                RFC numerické kódy, MOTD banner
├── api.py                     HTTP transport na api.chatujme.cz/irc
├── textfilters.py             překlad HTML zpráv na IRC text
├── session.py                 jádro session: login, místnosti, dispatch
├── commands/                  handlery IRC příkazů
│   ├── auth.py                CAP, NICK, USER, PASS, NickServ
│   ├── rooms.py               JOIN, PART, LIST, NAMES, WHO, MODE, TOPIC, KICK
│   ├── messaging.py           PRIVMSG/NOTICE + CTCP
│   ├── info.py                PING/PONG, VERSION, MOTD, WHOIS, USERHOST
│   └── presence.py            AWAY, QUIT, IDLER
├── poller.py                  vlákna (message poller, janitor)
└── server.py                  TCP/SSL listenery
tests/                         unit testy (unittest, bez sítě)
packaging/pyi_entry.py         vstup pro PyInstaller build
```

### Testy

```bash
python -m unittest discover -v
```

Testy běží automaticky v CI (GitHub Actions) na Linuxu i Windows, Python 3.8 a 3.12.

### Spuštění

```bash
# Plain IRC na portu 6667
python3 chatujmegw.py

# S debug výstupem
python3 chatujmegw.py --port 6667 --listen 0.0.0.0 --debug 1

# SSL/TLS na jednom portu
python3 chatujmegw.py --ssl --ssl-cert cert.pem --ssl-key key.pem --port 6697

# Dual-port režim (plain 6667 + SSL 6697)
python3 chatujmegw.py --port 6667 --ssl-port 6697 --ssl-cert cert.pem --ssl-key key.pem
```

### Parametry

| Parametr | Popis | Výchozí |
|----------|-------|---------|
| `--port` | Port pro naslouchání | `6667` |
| `--listen` | Bind adresa | `127.0.0.1` (pouze localhost; pro přístup zvenčí `0.0.0.0`) |
| `--debug` | Debug úroveň (0–2) | `0` |
| `--ssl` | SSL/TLS na hlavním portu | — |
| `--ssl-port` | Druhý port pro SSL (dual-port mód) | — |
| `--ssl-cert` | Cesta k SSL certifikátu (PEM) | — |
| `--ssl-key` | Cesta k SSL privátnímu klíči (PEM) | — |

### SSL/TLS

Minimální verze TLS 1.2. Self-signed certifikát pro testování:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes \
  -subj "/CN=chatujme.cz/O=ChatujmeGW/C=CZ"
```

### Docker

```bash
# Docker Compose
docker compose up -d

# Manuální build
docker build -t chatujmegw .
docker run -d -p 6667:6667 -p 6697:6697 --restart always --name chatujmegw chatujmegw
```

### Build EXE (Windows)

```bash
pip install pyinstaller
pyinstaller --onefile --console --icon=chatujme.ico --paths src --name chatujmegw packaging/pyi_entry.py
```

Release exe se builduje automaticky v GitHub Actions při pushnutí tagu `v*`.

Předkompilovaný `dist/chatujmegw.exe` je součástí repozitáře.

---

## Podporované IRC příkazy

### Základní

| Příkaz | Popis |
|--------|-------|
| `NICK` | Nastavení nicku |
| `PASS` | Heslo k účtu |
| `USER` | Uživatelské jméno |
| `JOIN #id` | Vstup do místnosti (číslo místnosti) |
| `PART #id` | Odchod z místnosti |
| `PRIVMSG #id :text` | Zpráva do místnosti |
| `PRIVMSG nick :text` | Soukromá zpráva |
| `LIST` | Seznam místností |
| `WHO #id` | Uživatelé v místnosti |
| `WHOIS nick` | Informace o uživateli |
| `NAMES #id` | Seznam uživatelů |
| `AWAY :text` | Nastavení away zprávy |
| `QUIT` | Odpojení |
| `PING` / `PONG` | Keepalive |
| `CAP` | Capability negotiation |
| `VERSION` | Verze serveru |
| `USERHOST nick` | Host uživatele |
| `MOTD` | Message of the Day |

### Správce místnosti (OP)

| Příkaz | Popis |
|--------|-------|
| `KICK #id nick :důvod` | Vykopnutí uživatele |
| `MODE #id +o nick` | Předání správce |
| `TOPIC #id :text` | Změna popisu místnosti |

### NickServ / Registrace

| Příkaz | Popis |
|--------|-------|
| `NICKSERV IDENTIFY` / `NS ID` | Již přihlášen přes PASS |
| `NICKSERV REGISTER` / `REGISTER` | Přesměruje na web registraci |

### SMILES (zobrazení smajlíků)

| Příkaz | Popis |
|--------|-------|
| `SMILES` / `SMILES STATUS` | Aktuální režim + nápověda |
| `SMILES TEXT` | Popis smajlíku z webu (aria-label), jinak kód `*ID*` (výchozí) |
| `SMILES CODE` | Vždy kód `*ID*` (lze poslat zpět do chatu) |
| `SMILES URL` | URL obrázku |
| `SMILES HIDE` | Smajlíky skrýt |

### IDLER (anti-idle)

| Příkaz | Popis |
|--------|-------|
| `IDLER ON` / `OFF` | Zapnout/vypnout |
| `IDLER STATUS` | Aktuální nastavení |
| `IDLER TIME <s>` | Interval nečinnosti (výchozí: 2400 s = 40 min) |
| `IDLER TEXT <texty>` | Zprávy oddělené čárkou (výchozí: `.`, `..`, `AFK`) |

### CTCP

`VERSION`, `PING`, `TIME`, `ACTION` (/me)

### Symboly u jmen

| Symbol | Význam |
|--------|--------|
| `@` | Operátor místnosti |
| `%` | Half-op (omezená práva) |
| `+` | Voice |

---

## Bezpečnostní funkce

- Rate limiting: max 5 spojení/IP za 60 s, max 10 příkazů/s na spojení
- Max 378 současných spojení
- Validace nicku (4–23 znaků, `a-z 0-9 - _`, musí začínat písmenem)
- Validace room ID (číselné, max 999999)
- IRC protocol injection prevence (sanitizace CRLF, null bytes)
- Maskování hesel a tokenů v logu
- In-memory cookies (žádné soubory)
- API komunikace výhradně přes HTTPS
- TLS 1.2+ pro SSL spojení

---

## RFC kompatibilita

Gateway implementuje subset [RFC 1459](https://tools.ietf.org/html/rfc1459).

<details>
<summary>Numerické kódy (35+)</summary>

| Kód | Název | Popis |
|-----|-------|-------|
| 001 | RPL_WELCOME | Uvítací zpráva |
| 002 | RPL_YOURHOST | Info o serveru |
| 003 | RPL_CREATED | Datum vytvoření |
| 004 | RPL_MYINFO | Info o serveru |
| 301 | RPL_AWAY | Uživatel je pryč |
| 302 | RPL_USERHOST | Host uživatele |
| 305 | RPL_UNAWAY | Již nejsi pryč |
| 306 | RPL_NOWAWAY | Jsi označen jako pryč |
| 311 | RPL_WHOISUSER | WHOIS info |
| 312 | RPL_WHOISSERVER | WHOIS server |
| 313 | RPL_WHOISOPERATOR | WHOIS operátor |
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
| 351 | RPL_VERSION | Verze serveru |
| 352 | RPL_WHOREPLY | WHO odpověď |
| 353 | RPL_NAMREPLY | Seznam uživatelů |
| 366 | RPL_ENDOFNAMES | Konec NAMES |
| 368 | RPL_ENDOFBANLIST | Konec ban listu |
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
| 482 | ERR_CHANOPRIVSNEEDED | Nedostatečná oprávnění |

</details>

---

## Řešení problémů

- **Kódování**: nastavte **UTF-8** v IRC klientu
- **Firewall**: ověřte, že porty 6667/6697 nejsou blokované
- **Timeout**: spojení má 5minutový timeout, PING se posílá každých 60 s

---

## Licence

MIT License

## Autor

**LuRy** — [lury@lury.cz](mailto:lury@lury.cz)

Původní projekt založen na [lidegw](http://sourceforge.net/projects/lidegw/).
