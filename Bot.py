  
import asyncio,aiohttp,json,time,logging,logging.handlers,os,math,hashlib
from datetime import datetime,timezone
import config

CAPITAL=1000.0
PAPER=True
MAX_DD=0.10
DAILY_LOSS=0.05
MAX_EXPOSURE=0.75
MIN_PROFIT=12.0
KELLY_FRACTION=0.25
TICK_INTERVAL=5
MARKET_RELOAD=30
BLACKLIST_TTL=14400
STATE_FILE=”/root/bot_state.json”
LOG_FILE=”/root/bot.log”
FEES={“crypto”:0.018,“sport”:0.0075,“sports”:0.0075,“geo”:0.0,“politics”:0.01,“default”:0.01}
GAMMA_URL=“https://gamma-api.polymarket.com/markets?limit=500&active=true&closed=false”

def setup_logging():
fmt=”%(asctime)s [%(levelname)s] %(message)s”
h1=logging.StreamHandler()
h2=logging.handlers.RotatingFileHandler(LOG_FILE,maxBytes=5000000,backupCount=3)
logging.basicConfig(level=logging.INFO,format=fmt,datefmt=”%Y-%m-%d %H:%M:%S”,handlers=[h1,h2])
return logging.getLogger(“polybot”)

log=setup_logging()

class BotState:
def **init**(self):
self.capital=CAPITAL;self.pnl=0.0;self.daily_pnl=0.0;self.peak=CAPITAL
self.wins=0;self.losses=0;self.trades=0
self.positions={};self.blacklist={};self.pnl_history=[]
self.price_history={}
self.daily_reset_day=datetime.now(timezone.utc).date().isoformat()

```
def save(self):
    try:
        data={"capital":self.capital,"pnl":self.pnl,"daily_pnl":self.daily_pnl,
              "peak":self.peak,"wins":self.wins,"losses":self.losses,"trades":self.trades,
              "blacklist":self.blacklist,"pnl_history":self.pnl_history[-500:],
              "daily_reset_day":self.daily_reset_day}
        json.dump(data,open(STATE_FILE,"w"),indent=2)
    except Exception as e:
        log.warning("Save error:"+str(e))

def load(self):
    if not os.path.exists(STATE_FILE):return
    try:
        data=json.load(open(STATE_FILE))
        self.capital=data.get("capital",CAPITAL)
        self.pnl=data.get("pnl",0.0)
        self.peak=data.get("peak",CAPITAL)
        self.wins=data.get("wins",0)
        self.losses=data.get("losses",0)
        self.trades=data.get("trades",0)
        self.blacklist=data.get("blacklist",{})
        self.pnl_history=data.get("pnl_history",[])
        today=datetime.now(timezone.utc).date().isoformat()
        if data.get("daily_reset_day","")==today:
            self.daily_pnl=data.get("daily_pnl",0.0)
        else:
            self.daily_pnl=0.0
        self.daily_reset_day=today
        log.info("Estado cargado: trades="+str(self.trades)+" pnl="+str(self.pnl))
    except Exception as e:
        log.warning("Load error:"+str(e))

def check_daily_reset(self):
    today=datetime.now(timezone.utc).date().isoformat()
    if today!=self.daily_reset_day:
        log.info("Reset diario. PnL ayer:"+str(round(self.daily_pnl,2)))
        self.daily_pnl=0.0;self.daily_reset_day=today;self.save()

def is_halted(self):
    if self.daily_pnl<-(CAPITAL*DAILY_LOSS):
        log.warning("HALT: daily loss");return True
    dd=(self.peak-(CAPITAL+self.pnl))/self.peak if self.peak>0 else 0
    if dd>=MAX_DD:
        log.warning("HALT: drawdown "+str(round(dd*100,1))+"%");return True
    return False

@property
def win_rate(self):
    t=self.wins+self.losses;return self.wins/t if t>0 else 0.75

@property
def drawdown(self):
    if self.peak<=0:return 0.0
    return (self.peak-(CAPITAL+self.pnl))/self.peak

@property
def sharpe(self):
    if len(self.pnl_history)<10:return 0.0
    n=len(self.pnl_history);mean=sum(self.pnl_history)/n
    var=sum((x-mean)**2 for x in self.pnl_history)/n
    std=math.sqrt(var) if var>0 else 1e-9
    return (mean/std)*math.sqrt(252)

def max_positions(self):
    return min(12,max(1,int((CAPITAL+self.pnl)*MAX_EXPOSURE/20)))
```

state=BotState()

def get_fee(cat):
c=str(cat).lower()
for k,v in FEES.items():
if k in c:return v
return FEES[“default”]

def get_threshold(cat):
return 1.0-get_fee(cat)-0.005

def days_until(market):
s=market.get(“endDateIso”,””) or market.get(“endDate”,””)
if not s:return 30.0
try:
end=datetime.fromisoformat(s.replace(“Z”,”+00:00”))
d=(end-datetime.now(timezone.utc)).total_seconds()/86400
return max(0.1,d)
except:
return 30.0

def kelly_bet(edge,cat):
fee=get_fee(cat);net=edge-fee
if net<=0:return 0.0
n=state.wins+state.losses;wr=(0.75*20+state.wins)/(20+n)
f=max(0.0,((wr*(1+net)-1)/net))*KELLY_FRACTION
f=max(0.02,min(f,0.15))
return round((CAPITAL+state.pnl)*f,2)

def score_arb(net,end_days):
return net/(max(end_days,1)**0.6)

async def fetch_markets(session):
try:
headers={“User-Agent”:“Mozilla/5.0”,“Accept”:“application/json”}
async with session.get(GAMMA_URL,headers=headers,timeout=aiohttp.ClientTimeout(total=20)) as r:
if r.status!=200:
log.error(“Gamma status:”+str(r.status));return []
data=await r.json()
log.info(“Mercados Gamma:”+str(len(data)))
return data
except Exception as e:
log.error(“Gamma error:”+str(e));return []

def parse_prices(market):
raw=market.get(“outcomePrices”,”[]”)
prices=json.loads(raw) if isinstance(raw,str) else raw
if len(prices)<2:return 0.0,0.0
return float(prices[0] or 0),float(prices[1] or 0)

def parity_arb(markets):
now=time.time()
state.blacklist={k:v for k,v in state.blacklist.items() if now-v<BLACKLIST_TTL}
opps=[]
for m in markets:
cid=m.get(“conditionId”,””) or m.get(“condition_id”,””)
if not cid or cid in state.blacklist:continue
ya,na=parse_prices(m)
if ya<0.002 or na<0.002:continue
total=ya+na
cat=m.get(“category”,“politics”) or “politics”
thr=get_threshold(cat)
if total>=thr:continue
edge=round(thr-total,4)
fee=get_fee(cat)
net=round(edge-fee,4)
if net<=0:continue
bet=kelly_bet(edge,cat)
if bet<1:continue
if bet*net<MIN_PROFIT:continue
days=days_until(m)
sc=score_arb(net,days)
vol=float(m.get(“volume24hr”,0) or 0)
opps.append({
“market”:m,“cid”:cid,“cat”:cat,“edge”:edge,“net_edge”:net,
“bet”:bet,“score”:sc,“ya”:ya,“na”:na,“total”:total,
“exp”:round(bet*net,2),“days”:round(days,1),“vol”:vol
})
opps.sort(key=lambda x:x[“score”],reverse=True)
return opps

def allocate_capital(opps):
equity=(CAPITAL+state.pnl)*MAX_EXPOSURE
used=sum(p.get(“bet”,0) for p in state.positions.values())
free=max(0.0,equity-used)
if free<MIN_PROFIT:return []
for i in range(min(2,len(opps))):
opps[i][“bet”]=min(opps[i][“bet”],(CAPITAL+state.pnl)*0.20,free/max(len(opps),1))
if len(opps)>2:
pool=free*0.30;ts=sum(o[“score”] for o in opps[2:]) or 1
for i in range(2,len(opps)):
opps[i][“bet”]=min(opps[i][“bet”],(opps[i][“score”]/ts)*pool)
return [o for o in opps if o[“bet”]>=1.0]

def execute_trade(opp):
if state.is_halted() or len(state.positions)>=state.max_positions():return
if opp[“cid”] in state.positions:return
bet=opp[“bet”];net=opp[“net_edge”];cat=opp[“cat”]
q=opp[“market”].get(“question”,””)[:60]
state.trades+=1
log.info(“TRADE #”+str(state.trades)+” “+cat.upper()+” edge=”+str(round(opp[“edge”]*100,1))+”% net=”+str(round(net*100,1))+”% $”+str(bet)+” exp=$”+str(opp[“exp”])+” dias=”+str(opp[“days”])+” | “+q)
if PAPER:
h=int(hashlib.md5((opp[“cid”]+str(state.trades)).encode()).hexdigest(),16)
slip=0.001+(h%20)*0.00001
eff=net-slip;win=eff>0
result=round(bet*eff if win else -bet*0.02,2)
state.pnl+=result;state.daily_pnl+=result;state.pnl_history.append(result)
eq=CAPITAL+state.pnl
if eq>state.peak:state.peak=eq
if win:state.wins+=1
else:state.losses+=1
log.info(”   “+(“WIN” if win else “LOSS”)+” $”+str(result)+” PnL=$”+str(round(state.pnl,2))+” WR=”+str(round(state.win_rate*100,1))+”%”)
state.save()
else:
log.warning(“LIVE: pendiente para “+opp[“cid”])
state.blacklist[opp[“cid”]]=time.time();state.save()

async def health_loop():
while True:
await asyncio.sleep(60)
log.info(“HEALTH equity=$”+str(round(CAPITAL+state.pnl,2))+” pnl=$”+str(round(state.pnl,2))+” daily=$”+str(round(state.daily_pnl,2))+” trades=”+str(state.trades)+” WR=”+str(round(state.win_rate*100,1))+”% DD=”+str(round(state.drawdown*100,1))+”% pos=”+str(len(state.positions)))

async def main_loop(session):
markets=[];tick=0
log.info(“Cargando mercados…”)
markets=await fetch_markets(session)
while True:
try:
tick+=1;state.check_daily_reset()
if tick%MARKET_RELOAD==0:
nm=await fetch_markets(session)
if nm:markets=nm
opps=parity_arb(markets)
opps=allocate_capital(opps)
mp=state.max_positions()
for o in opps[:mp]:execute_trade(o)
if tick%10==0:
log.info(“Tick#”+str(tick)+” mercados=”+str(len(markets))+” opps=”+str(len(opps))+” trades=”+str(state.trades)+” pnl=$”+str(round(state.pnl,2)))
await asyncio.sleep(TICK_INTERVAL)
except asyncio.CancelledError:raise
except Exception as e:
log.error(“Loop error:”+str(e));await asyncio.sleep(TICK_INTERVAL)

async def main():
log.info(”=”*55)
log.info(”  POLYMARKET PARITY ARB BOT v15”)
log.info(”  Capital:$”+str(CAPITAL)+” Paper:”+str(PAPER))
log.info(”=”*55)
state.load()
connector=aiohttp.TCPConnector(limit=10,ttl_dns_cache=300,family=2)
async with aiohttp.ClientSession(connector=connector) as session:
ht=asyncio.create_task(health_loop())
try:await main_loop(session)
finally:ht.cancel();state.save();log.info(“Bot detenido.”)

asyncio.run(main())