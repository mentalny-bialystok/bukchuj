# Analysis used in the first real-data review. Run: scripts/download.sh && python3 scripts/analyse.py
from value_engine.backtest import *
from dataclasses import replace
m=load("data/raw")
print("matches",len(m),"books",soft_books(m,0.5))
def row(name,s): print(f"{name:34s} n={s.n:5d} edge={s.avg_edge:+.2%} CLV={s.avg_clv:+.2%} CLVt={s.clv_t:6.2f} beat={s.beat_close:.0%} yield={s.yield_:+.2%} Pt={s.profit_t:5.2f}")
base=StrategyConfig()
print("\n== By soft book (power, edge>=3%, 1x2) ==")
for b in ["Max","Avg","B365","BW","WH","1XB","BF"]:
    try: row(b, run(m,replace(base,soft_book=b)).summary)
    except Exception as e: print(b,"err",e)
print("\n== B365 by threshold ==")
for t in [0.01,0.02,0.03,0.05,0.08]: row(f"B365 min_edge {t}",run(m,replace(base,soft_book="B365",min_edge=t)).summary)
print("\n== Max by devig method ==")
for d in ["multiplicative","power","shin","odds_ratio"]: row(d,run(m,replace(base,devig_method=d)).summary)
print("\n== Over/under 2.5 ==")
for b in ["Max","B365"]: row("OU "+b,run(m,replace(base,soft_book=b,markets=("ou25",))).summary)
r=run(m,replace(base,soft_book="Max"))
for k in ["league","outcome","odds_band"]:
    print("\n== Max by",k,"==")
    for name,s in r.by[k].items(): row(str(name),s)
print("\n== WALK-FORWARD ==")
for b in ["Max","B365"]:
    wf=walk_forward(m,replace(base,soft_book=b),{"min_edge":[0.02,0.03,0.05],"devig_method":["multiplicative","power","shin"]})
    print("\n###",b); print(format_walkforward(wf))
