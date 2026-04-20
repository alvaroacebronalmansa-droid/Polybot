import asyncio,aiohttp,hmac,hashlib,time,json,logging,logging.handlers,os,math
from datetime import datetime,timezone

# ==================== CONFIG ====================

API_KEY=””
SECRET_KEY=””
PASSPHRASE=””
PAPER=True
CAPITAL=1000.0
PAIRS=[“GOMININGUSDT”,“ABUSDT”,“AITECHUSDT”,“JCTUSDT”,“CROSSUSDT”]
SPREAD_TARGET=0.003
ORDER_SIZE=50.0
MAX_POSITIONS=5
DAILY_LOSS_LIMIT=0.02
MAX_DRAWDOWN=0.05
REBALANCE_INTERVAL=30
LOG_FILE=”/root/mmbot.log”
STATE_FILE=”/root/mmbot_state.json”
BASE_URL=“https://api.bitget.com”

# ==================== LOGGING ====================

def setup_logging():
fmt=”%(asctime)s [%(levelname)s] %(message)s”
h1=logging.StreamHandler()
h2=logging.handlers.RotatingFileHandler(LOG_FILE,maxBytes=5000000,backupCount=3)
logging.basicConfig(level=logging.INFO,format=fmt,datefmt=”%Y-%m-%d %H:%M:%S”,handlers=[h1,h2])
return logging.getLogger(“mmbot”)
log=setup_logging()

# ==================== STATE ====================

class State:
def **init**(self):
self.capital=CAPITAL
self.pnl=0.0
self.daily_pnl=0.0
self.peak=CAPITAL
self.trades=0
self.wins=0
self.losses=0
self.positions={}
self.pnl_history=[]
self.daily_reset_day=datetime.now(timezone.utc).date().isoformat()

```
def save(self):
    try:
        json.dump({"capital":self.capital,"pnl":self.pnl,"daily_pnl":self.daily_pnl,
                  "peak":self.peak,"trades":self.trades,"wins":self.wins,"losses":self.losses,
                  "pnl_history":self.pnl_history[-500:],"daily_reset_day":self.daily_reset_day},
                 open(STATE_FILE,"w"),indent=2)
    except Exception as e:log.warning("Save:"+str(e))

def load(self):
    if not os.path.exists(STATE_FILE):return
    try:
        d=json.load(open(STATE_FILE))
        self.capital=d.get("capital",CAPITAL)
        self.pnl=d.get("pnl",0.0)
        self.peak=d.get("peak",CAPITAL)
        self.trades=d.get("trades",0)
        self.wins=d.get("wins",0)
        self.losses=d.get("losses",0)
        self.pnl_history=d.get("pnl_history",[])
        today=datetime.now(timezone.utc).date().isoformat()
        self.daily_pnl=d.get("daily_pnl",0.0) if d.get("daily_reset_day","")==today else 0.0
        self.daily_reset_day=today
        log.info("Estado cargado: trades="+str(self.trades)+" pnl=$"+str(round(self.pnl,2)))
    except Exception as e:log.warning("Load:"+str(e))

def check_reset(self):
    today=datetime.now(timezone.utc).date().isoformat()
    if today!=self.daily_reset_day:
        log.info("Reset diario. PnL ayer:$"+str(round(self.daily_pnl,2)))
        self.daily_pnl=0.0;self.daily_reset_day=today;self.save()

def is_halted(self):
    if self.daily_pnl<=-(CAPITAL*DAILY_LOSS_LIMIT):
        log.warning("HALT: daily loss");return True
    dd=(self.peak-(CAPITAL+self.pnl))/self.peak if self.peak>0 else 0
    if dd>=MAX_DRAWDOWN:
        log.warning("HALT: drawdown "+str(round(dd*100,1))+"%");return True
    return False

@property
def win_rate(self):
    t=self.wins+self.losses;return self.wins/t if t>0 else 0.0

@property
def drawdown(self):
    if self.peak<=0:return 0.0
    return (self.peak-(CAPITAL+self.pnl))/self.peak
```

state=State()

# ==================== BITGET API ====================

def sign(timestamp,method,path,body=””):
msg=str(timestamp)+method.upper()+path+(body or “”)
return hmac.new(SECRET_KEY.encode(),msg.encode(),hashlib.sha256).digest().hex()

async def get_ticker(session,symbol):
try:
url=BASE_URL+”/api/v2/spot/market/tickers?symbol=”+symbol
async with session.get(url,timeout=aiohttp.ClientTimeout(total=5)) as r:
if r.status!=200:return None
d=await r.json()
if d.get(“code”)!=“00000”:return None
t=d[“data”][0]
return {
“bid”:float(t.get(“bidPr”,0) or 0),
“ask”:float(t.get(“askPr”,0) or 0),
“last”:float(t.get(“lastPr”,0) or 0),
“vol”:float(t.get(“usdtVolume”,0) or 0),
“bid_sz”:float(t.get(“bidSz”,0) or 0),
“ask_sz”:float(t.get(“askSz”,0) or 0),
}
except Exception as e:
log.warning(“Ticker “+symbol+”:”+str(e));return None

# ==================== MARKET MAKING LOGIC ====================

def calc_spread(ticker):
if not ticker or ticker[“bid”]<=0 or ticker[“ask”]<=0:return 0
return (ticker[“ask”]-ticker[“bid”])/ticker[“bid”]

def should_enter(ticker,symbol):
if not ticker:return False
spread=calc_spread(ticker)
if spread<SPREAD_TARGET:return False
if ticker[“vol”]<200000:return False
if ticker[“bid_sz”]<ORDER_SIZE/ticker[“bid”]*0.5:return False
if symbol in state.positions:return False
return True

def paper_trade(symbol,ticker):
bid=ticker[“bid”]
ask=ticker[“ask”]
spread=calc_spread(ticker)
fee=0.0002
net_spread=spread-(fee*2)
if net_spread<=0:return

```
profit=round(ORDER_SIZE*net_spread,4)
win=net_spread>0

state.trades+=1
state.pnl+=profit
state.daily_pnl+=profit
state.pnl_history.append(profit)
eq=CAPITAL+state.pnl
if eq>state.peak:state.peak=eq
if win:state.wins+=1
else:state.losses+=1

log.info("TRADE #"+str(state.trades)+" "+symbol+
         " spread="+str(round(spread*100,3))+"% net="+str(round(net_spread*100,3))+"%"+
         " profit=$"+str(profit)+
         " PnL=$"+str(round(state.pnl,2))+
         " WR="+str(round(state.win_rate*100,1))+"%")
state.save()
```

# ==================== MAIN LOOP ====================

async def health_loop():
while True:
await asyncio.sleep(60)
equity=CAPITAL+state.pnl
log.info(“HEALTH equity=$”+str(round(equity,2))+
“ pnl=$”+str(round(state.pnl,2))+
“ daily=$”+str(round(state.daily_pnl,2))+
“ trades=”+str(state.trades)+
“ WR=”+str(round(state.win_rate*100,1))+”%”+
“ DD=”+str(round(state.drawdown*100,1))+”%”)

async def main_loop(session):
tick=0
while True:
try:
tick+=1
state.check_reset()
if state.is_halted():
await asyncio.sleep(60);continue

```
        for symbol in PAIRS:
            ticker=await get_ticker(session,symbol)
            if not ticker:continue
            spread=calc_spread(ticker)

            if should_enter(ticker,symbol):
                if PAPER:
                    paper_trade(symbol,ticker)
                else:
                    log.warning("LIVE: pendiente para "+symbol)

        if tick%12==0:
            log.info("Tick#"+str(tick)+
                     " trades="+str(state.trades)+
                     " pnl=$"+str(round(state.pnl,2))+
                     " daily=$"+str(round(state.daily_pnl,2)))

        await asyncio.sleep(REBALANCE_INTERVAL)

    except asyncio.CancelledError:raise
    except Exception as e:
        log.error("Loop error:"+str(e));await asyncio.sleep(10)
```

async def main():
log.info(”=”*55)
log.info(”  BITGET MARKET MAKER BOT v1”)
log.info(”  Capital:$”+str(CAPITAL)+” Paper:”+str(PAPER))
log.info(”  Pares:”+str(PAIRS))
log.info(”=”*55)
state.load()
connector=aiohttp.TCPConnector(limit=10,ttl_dns_cache=300,family=2)
async with aiohttp.ClientSession(connector=connector) as session:
ht=asyncio.create_task(health_loop())
try:await main_loop(session)
finally:ht.cancel();state.save();log.info(“Bot detenido.”)

asyncio.run(main())