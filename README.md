# ChatujmeGW

IRC Gateway pro [Chatujme.cz](https://chatujme.cz) chat. Umožňuje připojení k Chatujme.cz pomocí standardního IRC klienta.

## Požadavky

- **Python:** 3.8+

## Podporované IRC RFC

Gateway implementuje subset [RFC 1459](https://tools.ietf.org/html/rfc1459):

### Numerické kódy
| Kód | Název | Popis |
|-----|-------|-------|
| 001 | RPL_WELCOME | Uvítací zpráva |
| 002 | RPL_YOURHOST | Info o serveru |
| 003 | RPL_CREATED | Datum vytvoření |
| 004 | RPL_MYINFO | Info o serveru |
| 351 | RPL_VERSION | Verze serveru |
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
| 301 | RPL_AWAY | Uživatel je pryč |
| 305 | RPL_UNAWAY | Již nejsi pryč |
| 306 | RPL_NOWAWAY | Jsi označen jako pryč |
| 401 | ERR_NOSUCHNICK | Nick neexistuje |
| 403 | ERR_NOSUCHCHANNEL | Místnost neexistuje |
| 421 | ERR_UNKNOWNCOMMAND | Neznámý příkaz |
| 444 | ERR_NOLOGIN | Chyba přihlášení |
| 461 | ERR_NEEDMOREPARAMS | Chybí parametry |
| 474 | ERR_BANNEDFROMCHAN | Zakázán vstup |
| 482 | ERR_CHANOPRIVSNEEDED | Nedostatečná oprávnění |

### Podporované příkazy
`NICK`, `PASS`, `USER`, `JOIN`, `PART`, `PRIVMSG`, `NOTICE`, `MODE`, `KICK`, `WHO`, `WHOIS`, `LIST`, `PING`, `PONG`, `QUIT`, `CAP`, `VERSION`, `REGISTER`, `NAMES`, `TOPIC`, `USERHOST`, `AWAY`, `MOTD`, `NICKSERV`/`NS`

### OP příkazy
| Příkaz | Popis |
|--------|-------|
| `KICK #room nick :důvod` | Vykopnutí uživatele z místnosti |
| `MODE #room +o nick` | Předání správce (`/predej`) |
| `TOPIC #room :nový popis` | Změna popisu místnosti (vyžaduje oprávnění) |

### CTCP příkazy
`VERSION`, `PING`, `TIME`

## Spuštění

```bash
# Základní spuštění (plain IRC)
python3 chatujmegw.py

# S debug výstupem
python3 chatujmegw.py --port 6667 --listen 0.0.0.0 --debug 1

# SSL/TLS režim
python3 chatujmegw.py --ssl --ssl-cert cert.pem --ssl-key key.pem --port 6697

# Dual-port režim (plain + SSL současně)
python3 chatujmegw.py --port 6667 --ssl-port 6697 --ssl-cert cert.pem --ssl-key key.pem
```

### Parametry
| Parametr | Popis | Výchozí |
|----------|-------|---------|
| `--port` | Port pro naslouchání | 6667 |
| `--listen` | IP adresa pro binding | 127.0.0.1 |
| `--debug` | Debug úroveň (0-2) | 0 |
| `--ssl` | Povolit SSL/TLS na hlavním portu | - |
| `--ssl-port` | Druhý port pro SSL (dual-port mód) | - |
| `--ssl-cert` | Cesta k SSL certifikátu | - |
| `--ssl-key` | Cesta k SSL privátnímu klíči | - |

**Poznámka:** Pro přístup z jiných počítačů použijte `--listen 0.0.0.0`

### SSL/TLS

Pro produkční nasazení doporučujeme použít certifikát od důvěryhodné CA (např. Let's Encrypt).

Generování self-signed certifikátu pro testování:
```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes \
  -subj "/CN=chatujme.cz/O=ChatujmeGW/C=CZ"
```

## Docker

```bash
# Docker Compose (recommended)
docker compose up -d
docker compose logs -f

# Manual build & run
docker build -t chatujmegw .
docker run -d -p 6667:6667 -p 6697:6697 --restart always --name chatujmegw chatujmegw
```

### Docker s SSL
```bash
# Vytvořte složku certs/ s certifikáty
mkdir certs
cp cert.pem key.pem certs/

# Upravte docker-compose.yml - odkomentujte volumes a command pro SSL
docker compose up -d
```

## Build EXE (Windows)

```bash
pip install pyinstaller
pyinstaller --onefile --console --icon=chatujme.ico chatujmegw.py
# Výstup: dist/chatujmegw.exe
```

Předkompilovaný `dist/chatujmegw.exe` je součástí repozitáře.

## Použití

1. Spusťte gateway
2. V IRC klientu nastavte:
   - **Server:** localhost
   - **Port:** 6667 (nebo 6697 pro SSL)
   - **Nick:** váš nick na Chatujme.cz
   - **Password:** vaše heslo
3. Připojte se a vstupte do místnosti: `/join #12345`

## License

MIT License

## Autor

**LuRy** - [lury@lury.cz](mailto:lury@lury.cz)

Původní projekt založen na [lidegw](http://sourceforge.net/projects/lidegw/)
