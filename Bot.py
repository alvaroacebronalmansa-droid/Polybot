import asyncio
import aiohttp
import json
import time
import logging
import logging.handlers
import os
import math
import hashlib
from datetime import datetime, timezone
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
import config

# ==================== CONFIG v14 ====================

CAPITAL         = 1000.0
PAPER_MODE      = True
MAX_POSITIONS   = 4
DAILY_LOSS_LIMIT= 50.0
MAX_DRAWDOWN    = 0.10
TICK_INTERVAL   = 5
MARKET_RELOAD   = 60
BLACKLIST_TTL   = 14400
STATE_FILE      = “/root/bot_state.json”
LOG_FILE        = “/root/bot.log”
ODDS_KEY        = config.ODDS_KEY

MIN_YES_PRICE   = 0.08
MAX_YES_PRICE   = 0.32
MIN_EDGE        = 0.18
MIN_VOLUME_24H  = 1000
MIN_DEPTH       = 2000
MIN_DAYS        = 2
MAX_DAYS        = 21
MIN_PROFIT      = 12.0
KELLY_FRACTION  = 0.25
ODDS_THRESHOLD  = 0.12

HOST      = “https://clob.polymarket.com”
GAMMA_URL = “https://gamma-api.polymarket.com/markets?limit=500&active=true&closed=false”
ODDS_URL  = “https://api.the-odds-api.com/v4/sports/{sport}/odds/?apiKey={key}&regions=eu&markets=h2h&oddsFormat=decimal”

# ==================== LOGGING ====================

def setup_logging():
fmt = “%(asctime)s [%(levelname)s] %(message)s”
handlers = [
logging.StreamHandler(),
logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5000000, backupCount=3)
]
logging.basicConfig(level=logging.INFO, format=fmt, datefmt=”%Y-%m-%d %H:%M:%S”, handlers=handlers)
return logging.getLogger(“polybot”)

log = setup_logging()

# ==================== ESTADO ====================

class BotState:
def **init**(self):
self.capital      = CAPITAL
self.pnl          = 0.0
self.daily_pnl    = 0.0
self.peak         = CAPITAL
self.wins         = 0
self.losses       = 0
self.trades       = 0
self.positions    = {}
self.blacklist    = {}
self.pnl_history  = []
self.price_history= {}
self.daily_reset_day = datetime.now(timezone.utc).date().isoformat()

```
def save(self):
    try:
        data = {
            "capital": self.capital, "pnl": self.pnl,
            "daily_pnl": self.daily_pnl, "peak": self.peak,
            "wins": self.wins, "losses": self.losses, "trades": self.trades,
            "blacklist": self.blacklist, "pnl_history": self.pnl_history[-500:],
            "daily_reset_day": self.daily_reset_day
        }
        json.dump(data, open(STATE_FILE, "w"), indent=2)
    except Exception as e:
        log.warning("Save error: " + str(e))

def load(self):
    if not os.path.exists(STATE_FILE):
        return
    try:
        data = json.load(open(STATE_FILE))
        self.capital     = data.get("capital", CAPITAL)
        self.pnl         = data.get("pnl", 0.0)
        self.peak        = data.get("peak", CAPITAL)
        self.wins        = data.get("wins", 0)
        self.losses      = data.get("losses", 0)
        self.trades      = data.get("trades", 0)
        self.blacklist   = data.get("blacklist", {})
        self.pnl_history = data.get("pnl_history", [])
        today = datetime.now(timezone.utc).date().isoformat()
        if data.get("daily_reset_day", "") == today:
            self.daily_pnl = data.get("daily_pnl", 0.0)
        else:
            self.daily_pnl = 0.0
        self.daily_reset_day = today
        log.info("Estado cargado: trades=" + str(self.trades) + " pnl=$" + str(round(self.pnl, 2)))
    except Exception as e:
        log.warning("Load error: " + str(e))

def check_daily_reset(self):
    today = datetime.now(timezone.utc).date().isoformat()
    if today != self.daily_reset_day:
        log.info("Reset diario. PnL ayer: $" + str(round(self.daily_pnl, 2)))
        self.daily_pnl = 0.0
        self.daily_reset_day = today
        self.save()

def is_halted(self):
    if self.daily_pnl <= -DAILY_LOSS_LIMIT:
        log.warning("HALT: daily loss limit")
        return True
    dd = (self.peak - (CAPITAL + self.pnl)) / self.peak if self.peak > 0 else 0
    if dd >= MAX_DRAWDOWN:
        log.warning("HALT: max drawdown " + str(round(dd * 100, 1)) + "%")
        return True
    return False

@property
def win_rate(self):
    t = self.wins + self.losses
    return self.wins / t if t > 0 else 0.75

@property
def drawdown(self):
    if self.peak <= 0:
        return 0.0
    return (self.peak - (CAPITAL + self.pnl)) / self.peak

@property
def sharpe(self):
    if len(self.pnl_history) < 10:
        return 0.0
    n = len(self.pnl_history)
    mean = sum(self.pnl_history) / n
    var = sum((x - mean) ** 2 for x in self.pnl_history) / n
    std = math.sqrt(var) if var > 0 else 1e-9
    return (mean / std) * math.sqrt(252)
```

state = BotState()

# ==================== ODDS API ====================

odds_cache = {}
odds_last_update = 0

async def fetch_odds(session):
global odds_cache, odds_last_update
now = time.time()
if now - odds_last_update < 300:
return
sports = [“basketball_nba”, “soccer_epl”, “americanfootball_nfl”,
“baseball_mlb”, “icehockey_nhl”, “tennis_atp_french_open”]
for sport in sports:
try:
url = ODDS_URL.format(sport=sport, key=ODDS_KEY)
async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
if r.status != 200:
continue
data = await r.json()
for event in data:
for bookmaker in event.get(“bookmakers”, [])[:1]:
for market in bookmaker.get(“markets”, []):
if market.get(“key”) != “h2h”:
continue
outcomes = market.get(“outcomes”, [])
total = sum(1.0 / o[“price”] for o in outcomes if o[“price”] > 0)
for o in outcomes:
name = o[“name”].lower()
prob = round((1.0 / o[“price”]) / total, 4) if o[“price”] > 0 else 0
odds_cache[name] = prob
odds_last_update = now
log.info(“Odds API OK: “ + sport + “ teams=” + str(len(odds_cache)))
except Exception as e:
log.warning(“Odds API error “ + sport + “: “ + str(e))

def get_odds_prob(question):
q = question.lower()
best_match = None
best_score = 0
for team, prob in odds_cache.items():
if team in q and len(team) > best_score:
best_score = len(team)
best_match = prob
return best_match

# ==================== GAMMA API ====================

async def fetch_markets(session):
try:
headers = {“User-Agent”: “Mozilla/5.0”, “Accept”: “application/json”}
async with session.get(GAMMA_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
if r.status != 200:
log.error(“Gamma status: “ + str(r.status))
return []
data = await r.json()
log.info(“Mercados Gamma: “ + str(len(data)))
return data
except Exception as e:
log.error(“Gamma error: “ + str(e))
return []

# ==================== ORDERBOOKS ====================

async def fetch_orderbooks(session, markets):
token_ids = []
for m in markets:
ids = m.get(“clobTokenIds”, [])
if ids:
token_ids.append(ids[0])
if not token_ids:
return {}
books = {}
try:
payload = [{“token_id”: t} for t in token_ids[:500]]
async with session.post(HOST + “/books”, json=payload,
timeout=aiohttp.ClientTimeout(total=20)) as r:
if r.status != 200:
return {}
data = await r.json()
for book in data:
tid = book.get(“asset_id”, “”)
if tid:
books[tid] = book
except Exception as e:
log.warning(“Orderbooks error: “ + str(e))
return books

# ==================== HELPERS ====================

def parse_prices(market):
raw = market.get(“outcomePrices”, “[]”)
prices = json.loads(raw) if isinstance(raw, str) else raw
if len(prices) < 2:
return 0.0, 0.0
return float(prices[0] or 0), float(prices[1] or 0)

def days_until(market):
s = market.get(“endDateIso”, “”) or market.get(“endDate”, “”)
if not s:
return 30.0
try:
end = datetime.fromisoformat(s.replace(“Z”, “+00:00”))
d = (end - datetime.now(timezone.utc)).total_seconds() / 86400
return max(0.0, d)
except:
return 30.0

def calc_depth(book):
if not book:
return 0.0
bids = sum(float(b.get(“size”, 0)) for b in book.get(“bids”, []))
asks = sum(float(a.get(“size”, 0)) for a in book.get(“asks”, []))
return bids + asks

def has_momentum_against(market_id, yes_price):
history = state.price_history.get(market_id, [])
if len(history) < 3:
return False
recent = history[-3:]
if yes_price < 0.5:
return (recent[0] - recent[-1]) > 0.12
return (recent[-1] - recent[0]) > 0.12

def orderbook_favors_reversion(book, yes_price):
if not book:
return True
bids = len(book.get(“bids”, []))
asks = len(book.get(“asks”, []))
if yes_price < 0.5:
return bids > asks * 1.6
return asks > bids * 1.6

def get_external_score(market):
q = market.get(“question”, “”).lower()
if any(x in q for x in [“election”, “president”, “senate”, “congress”, “vote”, “mayor”, “prime minister”]):
return 2.5
if any(x in q for x in [“vs”, “match”, “game”, “championship”, “final”, “nba”, “nfl”, “nhl”, “mlb”, “soccer”, “tennis”]):
odds_prob = get_odds_prob(q)
return 2.2 if odds_prob else 1.8
if any(x in q for x in [“bitcoin”, “btc”, “ethereum”, “eth”, “solana”]):
return 1.4
return 1.0

def kelly_bet(edge):
n = state.wins + state.losses
wr = (0.75 * 20 + state.wins) / (20 + n)
f = max(0.0, ((wr * (1 + edge) - 1) / edge)) * KELLY_FRACTION
f = max(0.02, min(f, 0.18))
equity = CAPITAL + state.pnl
bet = round(equity * f, 2)
return min(max(bet, 12.0), 50.0)

def update_price_history(market_id, yes_price):
if market_id not in state.price_history:
state.price_history[market_id] = []
state.price_history[market_id].append(yes_price)
state.price_history[market_id] = state.price_history[market_id][-10:]

# ==================== DETECCION ====================

def detect_opportunities(markets, books):
now = time.time()
state.blacklist = {k: v for k, v in state.blacklist.items() if now - v < BLACKLIST_TTL}
opps = []

```
for m in markets:
    cid = m.get("conditionId", "")
    if not cid or cid in state.blacklist or cid in state.positions:
        continue

    yes_price, no_price = parse_prices(m)
    if yes_price <= 0 or no_price <= 0:
        continue

    update_price_history(cid, yes_price)

    in_yes_zone = MIN_YES_PRICE <= yes_price <= MAX_YES_PRICE
    in_no_zone  = MIN_YES_PRICE <= no_price  <= MAX_YES_PRICE

    if not in_yes_zone and not in_no_zone:
        continue

    side        = "YES" if in_yes_zone else "NO"
    entry_price = yes_price if side == "YES" else no_price
    edge        = abs(entry_price - 0.5)

    if edge < MIN_EDGE:
        continue

    vol24 = float(m.get("volume24hr", 0) or 0)
    if vol24 < MIN_VOLUME_24H:
        continue

    days = days_until(m)
    if not (MIN_DAYS <= days <= MAX_DAYS):
        continue

    if has_momentum_against(cid, yes_price):
        continue

    yes_tokens = m.get("clobTokenIds", [])
    yes_tid    = yes_tokens[0] if yes_tokens else ""
    book       = books.get(yes_tid, {})
    depth      = calc_depth(book)

    if depth > 0 and depth < MIN_DEPTH:
        continue

    if not orderbook_favors_reversion(book, yes_price):
        continue

    ext = get_external_score(m)
    if ext < 1.5:
        continue

    odds_prob = get_odds_prob(m.get("question", ""))
    if odds_prob:
        discrepancy = abs(entry_price - odds_prob)
        if discrepancy < ODDS_THRESHOLD:
            continue

    bet        = kelly_bet(edge)
    exp_profit = round(bet * edge * 0.85, 2)

    if exp_profit < MIN_PROFIT:
        continue

    opps.append({
        "cid":         cid,
        "market":      m,
        "side":        side,
        "yes_price":   yes_price,
        "entry_price": entry_price,
        "edge":        round(edge, 4),
        "bet":         bet,
        "exp_profit":  exp_profit,
        "ext_score":   ext,
        "days":        round(days, 1),
        "odds_prob":   odds_prob,
        "tp_price":    0.50,
        "sl_pct":      0.18,
        "opened_at":   now,
    })

opps.sort(key=lambda x: x["ext_score"] * x["edge"], reverse=True)
return opps
```

# ==================== SALIDAS ====================

def check_exits():
now = time.time()
to_close = []

```
for cid, pos in list(state.positions.items()):
    market       = pos.get("market", {})
    yes_price, _ = parse_prices(market)
    if yes_price <= 0:
        continue

    side         = pos["side"]
    entry        = pos["entry_price"]
    bet          = pos["bet"]
    hours_open   = (now - pos.get("opened_at", now)) / 3600
    days         = days_until(market)
    current      = yes_price if side == "YES" else (1 - yes_price)
    move         = current - entry

    if current >= pos.get("tp_price", 0.50):
        profit = round(bet * move, 2)
        log.info("TP HIT " + side + " +$" + str(profit) + " | " + market.get("question", "")[:50])
        to_close.append((cid, profit))
        continue

    if hours_open > 24 and move < -pos.get("sl_pct", 0.18):
        loss = round(bet * move, 2)
        log.info("SL HIT " + side + " $" + str(loss) + " | " + market.get("question", "")[:50])
        to_close.append((cid, loss))
        continue

    if days < 2 and move < 0:
        loss = round(bet * move, 2)
        log.info("TIME STOP " + side + " $" + str(loss) + " | " + market.get("question", "")[:50])
        to_close.append((cid, loss))
        continue

for cid, result in to_close:
    state.pnl       += result
    state.daily_pnl += result
    state.pnl_history.append(result)
    equity = CAPITAL + state.pnl
    if equity > state.peak:
        state.peak = equity
    if result >= 0:
        state.wins += 1
    else:
        state.losses += 1
    del state.positions[cid]
    state.blacklist[cid] = time.time()
    state.save()
```

# ==================== EJECUCION ====================

def execute_trade(opp):
if state.is_halted() or len(state.positions) >= MAX_POSITIONS:
return
if opp[“cid”] in state.positions:
return

```
bet  = opp["bet"]
side = opp["side"]
edge = opp["edge"]
q    = opp["market"].get("question", "")[:60]

state.trades += 1
log.info(
    "TRADE #" + str(state.trades) + " " + side +
    " $" + str(bet) +
    " edge=" + str(round(edge * 100, 1)) + "%" +
    " ext=" + str(opp["ext_score"]) +
    " dias=" + str(opp["days"]) +
    " exp=$" + str(opp["exp_profit"]) +
    " | " + q
)

if PAPER_MODE:
    h      = int(hashlib.md5((opp["cid"] + str(state.trades)).encode()).hexdigest(), 16)
    slip   = 0.001 + (h % 20) * 0.0001
    eff    = edge - slip
    win    = eff > 0
    result = round(bet * eff if win else -bet * 0.03, 2)

    state.positions[opp["cid"]] = {**opp, "result": result, "win": win}

    state.pnl       += result
    state.daily_pnl += result
    state.pnl_history.append(result)
    equity = CAPITAL + state.pnl
    if equity > state.peak:
        state.peak = equity
    if win:
        state.wins += 1
    else:
        state.losses += 1

    del state.positions[opp["cid"]]

    log.info(
        "   " + ("WIN" if win else "LOSS") +
        " $" + str(result) +
        " PnL=$" + str(round(state.pnl, 2)) +
        " WR=" + str(round(state.win_rate * 100, 1)) + "%" +
        " DD=" + str(round(state.drawdown * 100, 1)) + "%" +
        " Sharpe=" + str(round(state.sharpe, 2))
    )
    state.save()
else:
    log.warning("LIVE MODE: ejecucion pendiente para " + opp["cid"])
    state.blacklist[opp["cid"]] = time.time()
    state.save()
```

# ==================== HEALTH ====================

async def health_loop():
while True:
await asyncio.sleep(60)
log.info(
“HEALTH equity=$” + str(round(CAPITAL + state.pnl, 2)) +
“ pnl=$” + str(round(state.pnl, 2)) +
“ daily=$” + str(round(state.daily_pnl, 2)) +
“ trades=” + str(state.trades) +
“ WR=” + str(round(state.win_rate * 100, 1)) + “%” +
“ DD=” + str(round(state.drawdown * 100, 1)) + “%” +
“ Sharpe=” + str(round(state.sharpe, 2)) +
“ pos=” + str(len(state.positions))
)

# ==================== MAIN LOOP ====================

async def main_loop(session):
markets = []
books   = {}
tick    = 0

```
log.info("Cargando mercados y odds iniciales...")
markets = await fetch_markets(session)
await fetch_odds(session)

while True:
    try:
        tick += 1
        state.check_daily_reset()

        if tick % MARKET_RELOAD == 0:
            nm = await fetch_markets(session)
            if nm:
                markets = nm
            await fetch_odds(session)

        if tick % 6 == 0:
            books = await fetch_orderbooks(session, markets)

        check_exits()

        opps = detect_opportunities(markets, books)
        for opp in opps[:MAX_POSITIONS - len(state.positions)]:
            execute_trade(opp)

        if tick % 10 == 0:
            log.info(
                "Tick#" + str(tick) +
                " mercados=" + str(len(markets)) +
                " books=" + str(len(books)) +
                " opps=" + str(len(opps)) +
                " trades=" + str(state.trades) +
                " pnl=$" + str(round(state.pnl, 2))
            )

        await asyncio.sleep(TICK_INTERVAL)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error("Loop error: " + str(e))
        await asyncio.sleep(TICK_INTERVAL)
```

# ==================== MAIN ====================

async def main():
log.info(”=” * 55)
log.info(”  POLYMARKET MEAN REVERSION BOT v14”)
log.info(”  Capital: $” + str(CAPITAL) + “ | Paper: “ + str(PAPER_MODE))
log.info(”  Min Edge: “ + str(MIN_EDGE) + “ | Min Vol: $” + str(MIN_VOLUME_24H))
log.info(”=” * 55)

```
state.load()

connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300, family=2)
async with aiohttp.ClientSession(connector=connector) as session:
    ht = asyncio.create_task(health_loop())
    try:
        await main_loop(session)
    finally:
        ht.cancel()
        state.save()
        log.info("Bot detenido. Estado guardado.")
```

if **name** == “**main**”:
asyncio.run(main())
