import streamlit as st
import pandas as pd, numpy as np, akshare as ak
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import requests,time,random,re

st.set_page_config(page_title="A股短线模型 V6.7",page_icon="📈",layout="centered")
st.markdown("""<style>.block-container{padding-top:1rem;max-width:860px}.box{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;margin:8px 0}.big{font-size:1.3rem;font-weight:700}[data-testid="stMetricValue"]{font-size:1.2rem}</style>""",unsafe_allow_html=True)
POS=["中标","签订","合同","回购","增持","预增","扭亏","分红","重大项目","战略合作","获批","订单","业绩增长"]
NEG=["减持","解禁","立案","调查","处罚","诉讼","亏损","预亏","退市","风险提示","终止","违约","冻结","问询函"]
SEV=["立案","调查","处罚","退市","重大诉讼","预亏","风险提示"]

def retry(fn,n=3):
    err=None
    for i in range(n):
        try:return fn()
        except Exception as e:
            err=e
            if i<n-1:time.sleep(.8+i+random.random())
    raise err

def symbol(code):
    return ("sh"+code) if code.startswith(("5","6","9")) else ("sz"+code)

@st.cache_data(ttl=600,show_spinner=False)
def hist_em(code):
    e=datetime.now().strftime("%Y%m%d");s=(datetime.now()-timedelta(days=1600)).strftime("%Y%m%d")
    x=retry(lambda:ak.stock_zh_a_hist(symbol=code,period="daily",start_date=s,end_date=e,adjust="qfq"))
    if x is None or x.empty:raise RuntimeError("东方财富历史行情为空")
    x["日期"]=pd.to_datetime(x["日期"])
    return x.sort_values("日期").reset_index(drop=True)

@st.cache_data(ttl=600,show_spinner=False)
def hist_sina(code):
    x=retry(lambda:ak.stock_zh_a_daily(symbol=symbol(code),adjust="qfq")).reset_index()
    x=x.rename(columns={"date":"日期","open":"开盘","high":"最高","low":"最低","close":"收盘","volume":"成交量"})
    need=["日期","开盘","最高","最低","收盘","成交量"]
    if not all(c in x for c in need):raise RuntimeError("新浪历史字段异常")
    x["日期"]=pd.to_datetime(x["日期"])
    return x[x["日期"]>=pd.Timestamp.now()-pd.Timedelta(days=1600)][need].sort_values("日期").reset_index(drop=True)

def get_hist(code):
    errs=[]
    for fn,name in [(hist_em,"东方财富"),(hist_sina,"新浪备用")]:
        try:return fn(code),name,errs
        except Exception as e:errs.append(f"{name}: {e}")
    return None,None,errs

# V5.2: 不再请求东方财富“全市场实时列表”
@st.cache_data(ttl=60,show_spinner=False)
def quote_sina(code):
    url=f"https://hq.sinajs.cn/list={symbol(code)}"
    headers={"Referer":"https://finance.sina.com.cn/","User-Agent":"Mozilla/5.0"}
    r=retry(lambda:requests.get(url,headers=headers,timeout=6),2);r.raise_for_status()
    r.encoding="gbk";txt=r.text
    m=re.search(r'="(.*)"',txt)
    if not m:raise RuntimeError("新浪实时返回格式异常")
    a=m.group(1).split(",")
    if len(a)<10 or not a[0]:raise RuntimeError("新浪实时行情为空")
    return {"source":"新浪实时","name":a[0],"open":float(a[1]),"preclose":float(a[2]),"price":float(a[3]),
            "high":float(a[4]),"low":float(a[5]),"volume":float(a[8]),"amount":float(a[9])}

@st.cache_data(ttl=60,show_spinner=False)
def quote_tencent(code):
    url=f"https://qt.gtimg.cn/q={symbol(code)}"
    headers={"User-Agent":"Mozilla/5.0","Referer":"https://gu.qq.com/"}
    r=retry(lambda:requests.get(url,headers=headers,timeout=6),2);r.raise_for_status()
    r.encoding="gbk";txt=r.text
    m=re.search(r'="(.*)"',txt)
    if not m:raise RuntimeError("腾讯实时返回格式异常")
    a=m.group(1).split("~")
    if len(a)<6:raise RuntimeError("腾讯实时字段不足")
    return {"source":"腾讯实时","name":a[1],"price":float(a[3]),"preclose":float(a[4]),"open":float(a[5])}



def board_name(code):
    code=str(code).zfill(6)
    if code.startswith(("4","8")): return "北交所"
    if code.startswith("688"): return "科创板"
    if code.startswith("300"): return "创业板"
    if code.startswith(("600","601","603","605","000","001","002","003")): return "沪深主板"
    return "其他A股"

@st.cache_data(ttl=180,show_spinner=False)
def market_snapshot():
    """全A快照只做市场初筛；完整模型仍在点入个股后运行，避免把快照分冒充完整模型分。"""
    x=retry(lambda:ak.stock_zh_a_spot_em(),2)
    if x is None or x.empty: raise RuntimeError("全A市场快照暂不可用")
    ren={"代码":"代码","名称":"名称","最新价":"现价","涨跌幅":"涨跌幅","成交额":"成交额",
         "换手率":"换手率","量比":"量比","市盈率-动态":"市盈率","总市值":"总市值"}
    cols=[c for c in ren if c in x.columns]
    y=x[cols].rename(columns=ren).copy()
    y["代码"]=y["代码"].astype(str).str.zfill(6)
    y["交易板块"]=y["代码"].map(board_name)
    for c in ["现价","涨跌幅","成交额","换手率","量比","市盈率","总市值"]:
        if c in y:y[c]=pd.to_numeric(y[c],errors="coerce")
    # 只用于全市场排序的初筛分：流动性+活跃度+当日强弱；绝不替代完整模型综合评分。
    pct=y.get("涨跌幅",pd.Series(0,index=y.index)).clip(-10,10)
    turn=y.get("换手率",pd.Series(0,index=y.index)).clip(0,20)
    vr=y.get("量比",pd.Series(1,index=y.index)).clip(.2,5)
    amt=y.get("成交额",pd.Series(0,index=y.index)).fillna(0)
    liq=np.log10(amt.clip(lower=1))
    y["市场初筛分"]=(50 + pct*1.8 + np.minimum(turn,8)*1.2 + (vr-1)*5 + (liq-8)*4).clip(0,100).round().astype("Int64")
    return y.sort_values(["市场初筛分","成交额"],ascending=False).reset_index(drop=True)

@st.cache_data(ttl=60*60*12,show_spinner=False)
def representative_universe():
    """不请求全A快照；各代表性市场池独立获取，失败池直接跳过。"""
    pools=[("沪深核心","000300",12),("中盘","000905",10),("小盘","000852",10),
           ("创业板","399006",8),("科创板","000688",8)]
    frames=[];errors=[]
    for label,idx,quota in pools:
        got=None
        for fn_name in ("index_stock_cons","index_stock_cons_csindex"):
            fn=getattr(ak,fn_name,None)
            if fn is None: continue
            try:
                z=retry(lambda fn=fn,idx=idx: fn(symbol=idx),2)
                if z is not None and not z.empty: got=z.copy();break
            except Exception as e: errors.append(f"{label}:{str(e)[:60]}")
        if got is None or got.empty: continue
        code_col=next((c for c in ["品种代码","成分券代码","证券代码","代码"] if c in got.columns),None)
        name_col=next((c for c in ["品种名称","成分券名称","证券简称","名称"] if c in got.columns),None)
        if code_col is None: continue
        z=pd.DataFrame()
        z["代码"]=got[code_col].astype(str).str.extract(r"(\d{6})",expand=False)
        z["名称"]=got[name_col].astype(str) if name_col else z["代码"]
        z=z.dropna(subset=["代码"]).drop_duplicates("代码").head(quota)
        z["候选来源"]=label;z["交易板块"]=z["代码"].map(board_name)
        frames.append(z)
    if not frames: raise RuntimeError("代表性指数候选池均暂不可用")
    out=pd.concat(frames,ignore_index=True).drop_duplicates("代码").reset_index(drop=True)
    out.attrs["source_errors"]=errors
    return out

def quick_candidate_metrics(raw):
    x=feat(raw).reset_index(drop=True);c=float(x.Close.iloc[-1])
    ret5=(c/float(x.Close.iloc[-6])-1)*100 if len(x)>=6 else 0
    ret20=(c/float(x.Close.iloc[-21])-1)*100 if len(x)>=21 else 0
    v5=float(x.Volume.tail(5).mean());v20=float(x.Volume.tail(20).mean()) if len(x)>=20 else v5
    vr=v5/v20 if v20>0 else 1;ma20=float(x.Close.tail(20).mean())
    pos=(c/ma20-1)*100 if ma20>0 else 0
    q=50+np.clip(ret5,-8,8)*2.2+np.clip(ret20,-15,15)*.6+np.clip(vr-1,-1,2)*8+np.clip(pos,-8,8)
    return float(np.clip(q,0,100)),ret5,ret20,vr

def get_quotes(code):
    out=[];errs=[]
    for fn in (quote_sina,quote_tencent):
        try:out.append(fn(code))
        except Exception as e:errs.append(str(e))
    return out,errs

@st.cache_data(ttl=600,show_spinner=False)
def turn_hist(code):
    e=datetime.now().strftime("%Y%m%d");s=(datetime.now()-timedelta(days=120)).strftime("%Y%m%d")
    x=retry(lambda:ak.stock_zh_a_hist(symbol=code,period="daily",start_date=s,end_date=e,adjust=""),2)
    if x is None or x.empty or "换手率" not in x.columns:raise RuntimeError("东方财富历史换手率不可用")
    x["日期"]=pd.to_datetime(x["日期"]);x["换手率"]=pd.to_numeric(x["换手率"],errors="coerce")
    return x[["日期","换手率"]].dropna().sort_values("日期")

@st.cache_data(ttl=3600,show_spinner=False)
def float_shares_em(code):
    # 备用计算通道：尝试从个股信息中拿流通股
    x=retry(lambda:ak.stock_individual_info_em(symbol=code),2)
    if x is None or x.empty:raise RuntimeError("个股信息为空")
    itemcol="item" if "item" in x.columns else x.columns[0]
    valcol="value" if "value" in x.columns else x.columns[1]
    for _,r in x.iterrows():
        k=str(r[itemcol])
        if "流通股" in k:
            v=pd.to_numeric(r[valcol],errors="coerce")
            if pd.notna(v) and float(v)>0:return float(v)
    raise RuntimeError("未找到流通股字段")

@st.cache_data(ttl=300,show_spinner=False)
def get_news(code):
    try:
        x=retry(lambda:ak.stock_news_em(symbol=code),2)
        return (x.head(30) if x is not None else pd.DataFrame()),None
    except Exception as e:return pd.DataFrame(),str(e)

def rsi(s,n=14):
    d=s.diff();u=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean();dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+u/dn.replace(0,np.nan))

def feat(x):
    x=x.copy();c=x["收盘"].astype(float);h=x["最高"].astype(float);l=x["最低"].astype(float);o=x["开盘"].astype(float);v=x["成交量"].astype(float)
    for n in [5,10,20,30,60]:x[f"MA{n}"]=c.rolling(n).mean()
    x["SLOPE20"]=x.MA20/x.MA20.shift(3)-1;x["RSI"]=rsi(c)
    e12=c.ewm(span=12,adjust=False).mean();e26=c.ewm(span=26,adjust=False).mean();dif=e12-e26;x["MACDH"]=dif-dif.ewm(span=9,adjust=False).mean()
    p=c.shift();tr=pd.concat([(h-l).abs(),(h-p).abs(),(l-p).abs()],axis=1).max(axis=1);x["ATR"]=tr.rolling(14).mean()
    x["VR20"]=v/v.rolling(20).mean();rng=(h-l).replace(0,np.nan);x["LOWER"]=(np.minimum(o,c)-l)/rng;x["UPPER"]=(h-np.maximum(o,c))/rng
    x["HIGH20"]=h.rolling(20).max();x["LOW20"]=l.rolling(20).min();x["HIGH60"]=h.rolling(60).max();x["LOW60"]=l.rolling(60).min()
    x["POS20"]=(c-x.LOW20)/(x.HIGH20-x.LOW20).replace(0,np.nan)
    return x

def similar(x, lookback=15, max_samples=60, min_gap=12):
    """
    V5.4 形态相似：
    比较最近 lookback 根K线的标准化价格路径、成交量路径、MA结构、
    RSI/MACD变化、实体/上下影和波动率。
    候选案例之间至少间隔 min_gap 个交易日，避免连续日期重复计数。
    """
    idx=len(x)-1
    if idx < lookback+180:return None

    def vector(df, end):
        w=df.iloc[end-lookback+1:end+1].copy()
        if len(w)!=lookback:return None
        close=w["收盘"].astype(float).to_numpy()
        high=w["最高"].astype(float).to_numpy()
        low=w["最低"].astype(float).to_numpy()
        op=w["开盘"].astype(float).to_numpy()
        vol=w["成交量"].astype(float).to_numpy()
        if np.any(~np.isfinite(close)) or close[0]<=0:return None

        # 价格路径：以窗口首日归一化
        price=close/close[0]-1
        # 日收益路径
        ret=np.r_[0, close[1:]/close[:-1]-1]
        # 成交量路径：相对窗口均量并截断异常值
        vm=np.nanmean(vol)
        volp=np.clip(vol/vm if vm>0 else np.ones_like(vol),0,4)
        # K线实体与上下影
        rng=np.maximum(high-low,1e-8)
        body=(close-op)/np.maximum(op,1e-8)
        lower=(np.minimum(op,close)-low)/rng
        upper=(high-np.maximum(op,close))/rng
        # 均线位置路径
        ma5=w["MA5"].astype(float).to_numpy()
        ma10=w["MA10"].astype(float).to_numpy()
        ma20=w["MA20"].astype(float).to_numpy()
        ma5p=close/ma5-1;ma10p=close/ma10-1;ma20p=close/ma20-1
        # 动量路径标准化
        rsi_p=(w["RSI"].astype(float).to_numpy()-50)/25
        atr=w["ATR"].astype(float).to_numpy()
        mac=np.divide(w["MACDH"].astype(float).to_numpy(),atr,out=np.zeros(lookback),where=np.isfinite(atr)&(atr!=0))
        # 波动率
        vola=np.nanstd(ret[1:]) if lookback>2 else 0

        parts=[
            price*3.0, ret*8.0, volp*.65,
            body*6.0, lower*.55, upper*.55,
            ma5p*4.0, ma10p*4.0, ma20p*5.0,
            rsi_p*.55, np.clip(mac,-3,3)*.5,
            np.array([vola*12])
        ]
        v=np.concatenate(parts)
        return np.nan_to_num(v,nan=0,posinf=3,neginf=-3)

    cur=vector(x,idx)
    if cur is None:return None

    candidates=[]
    # 必须留出未来5日用于验证
    for j in range(lookback+60,idx-5):
        v=vector(x,j)
        if v is None:continue
        dist=float(np.sqrt(np.mean((v-cur)**2)))
        candidates.append((dist,j))
    candidates.sort(key=lambda q:q[0])

    # 去重：相邻日期属于同一段行情，只保留更相似的一次
    picked=[]
    for dist,j in candidates:
        if all(abs(j-k)>=min_gap for _,k in picked):
            picked.append((dist,j))
        if len(picked)>=max_samples:break

    rec=[]
    for dist,j in picked:
        b=float(x.loc[j,"收盘"])
        f3=x.iloc[j+1:j+4];f5=x.iloc[j+1:j+6]
        if len(f5)<5 or b<=0:continue
        r3=float(f3.iloc[-1]["收盘"]/b-1)
        r5=float(f5.iloc[-1]["收盘"]/b-1)
        rec.append({
            "idx":j,"dist":dist,
            "date":pd.Timestamp(x.loc[j,"日期"]),
            "r3":r3,
            "r3max":float(f3["最高"].max()/b-1),
            "r5max":float(f5["最高"].max()/b-1),
            "r5":r5,
            "dd":float(f5["最低"].min()/b-1)
        })
    if len(rec)<20:return None

    rr=np.array([q["r5"] for q in rec],float)
    r3=np.array([q["r3"] for q in rec],float)
    wins=rr[rr>0];losses=rr[rr<=0]
    dists=np.array([q["dist"] for q in rec],float)
    # 相似度只用于解释，不假装为严格概率：距离越小越接近100
    simscore=float(100/(1+np.median(dists)*2.5))
    q25,q50,q75=np.quantile(rr,[.25,.5,.75])
    return {
        "n":len(rec),
        "p33":float(np.mean([q["r3max"]>=.03 for q in rec])),
        "p35":float(np.mean([q["r3max"]>=.05 for q in rec])),
        "p55":float(np.mean([q["r5max"]>=.05 for q in rec])),
        "win":float((rr>0).mean()),
        "win3":float((r3>0).mean()),
        "avg":float(rr.mean()),
        "avg3":float(r3.mean()),
        "maxup":float(np.mean([q["r5max"] for q in rec])),
        "avg_win":float(wins.mean()) if len(wins) else 0.0,
        "avg_loss":float(losses.mean()) if len(losses) else 0.0,
        "q25":float(q25),"q50":float(q50),"q75":float(q75),
        "dd":float(np.mean([q["dd"] for q in rec])),
        "similarity":simscore,
        "cases":rec[:8],
        "all_cases":rec,
        "lookback":lookback,
        "min_gap":min_gap
    }

def wilson_interval(wins,n,z=1.96):
    if n<=0:return (np.nan,np.nan)
    p=wins/n
    den=1+z*z/n
    center=(p+z*z/(2*n))/den
    half=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return max(0,center-half),min(1,center+half)

def bayes_win_rate(wins,n,prior_strength=20,prior_mean=.50):
    # 温和先验：等价于约20个50/50样本，防止小样本胜率虚高
    a=prior_mean*prior_strength+wins
    b=(1-prior_mean)*prior_strength+(n-wins)
    return a/(a+b)

def winrate_reliability(sim):
    """
    V5.9 胜率真实性：
    1) 原始独立形态样本
    2) Beta-Binomial 贝叶斯收缩
    3) Wilson 95%置信区间/保守下限
    4) 按时间顺序做早期70% / 后期30%样本外检查
    注意：这是相似案例的时间留出检验，不是机器学习模型训练。
    """
    if not sim or not sim.get("all_cases"):return None
    cases=sorted(sim["all_cases"],key=lambda q:q["date"])
    n=len(cases)
    wins=sum(q["r5"]>0 for q in cases)
    raw=wins/n
    bayes=bayes_win_rate(wins,n)
    lo,hi=wilson_interval(wins,n)

    cut=max(1,int(n*.70))
    train=cases[:cut]
    test=cases[cut:]
    def stats(arr):
        if not arr:return None
        rr=np.array([q["r5"] for q in arr],float)
        w=rr[rr>0];l=rr[rr<=0]
        return {
            "n":len(arr),"win":float((rr>0).mean()),
            "avg":float(rr.mean()),
            "avg_win":float(w.mean()) if len(w) else 0.0,
            "avg_loss":float(l.mean()) if len(l) else 0.0
        }
    tr=stats(train);te=stats(test)

    # 可信度不是收益评分：只评估样本量、区间宽度、样本外稳定性
    width=hi-lo
    conf=45.0
    conf+=np.clip((n-25)/75*25,0,25)
    conf+=np.clip((.35-width)/.25*20,-10,20)
    if tr and te:
        conf+=np.clip((.15-abs(tr["win"]-te["win"]))/.15*10,-10,10)
    conf=float(np.clip(conf,0,100))

    # 用样本外结果作为更严格的可交易统计；样本外太少则不强行给结论
    enough_oos=bool(te and te["n"]>=12)
    conservative=min(bayes,lo)  # 真正用于风控展示的保守胜率
    return {"n":n,"wins":wins,"raw":raw,"bayes":bayes,"lo":lo,"hi":hi,
            "conservative":conservative,"train":tr,"test":te,
            "enough_oos":enough_oos,"confidence":conf}


def path_trade_stats(sim,take_profit,stop_loss):
    if not sim or not sim.get("all_cases") or take_profit<=0 or stop_loss<=0:return None
    wins=losses=unresolved=0
    for q in sim["all_cases"]:
        tp=q["r5max"]>=take_profit
        sl=q["dd"]<=-stop_loss
        # 日K无法知道同日先后；两者都碰到时保守按止损。
        if tp and sl: losses+=1
        elif tp: wins+=1
        elif sl: losses+=1
        else: unresolved+=1
    resolved=wins+losses
    return {"wins":wins,"losses":losses,"unresolved":unresolved,"resolved":resolved,
            "win":wins/resolved if resolved else np.nan}

def historical_score_25(sim,reli,path,take_profit=None,stop_loss=None):
    if not sim or not reli:return {"points":0.0,"score100":0,"detail":[]}
    d=[]
    if path and path["resolved"]>=10 and np.isfinite(path["win"]):
        p1=float(np.clip(4+(path["win"]-.5)/.15*4,0,8))
    else:p1=2.0
    d.append(("5日TP/SL先后胜率",p1,8))

    ev=float(sim["avg"])
    if (path and path["resolved"] and np.isfinite(path["win"])
        and take_profit is not None and stop_loss is not None):
        path_ev=path["win"]*take_profit-(1-path["win"])*stop_loss
        ev=.60*path_ev+.40*ev
    p2=float(np.clip(3+ev/.02*3,0,6))
    d.append(("5日平均收益",p2,6))

    p3=float(np.clip(1.5+(sim["win3"]-.5)/.18+sim["avg3"]/.02*.5,0,3))
    d.append(("3日启动速度",p3,3))

    up=max(float(sim.get("maxup",sim.get("r5max",0))),0);down=abs(min(float(sim["dd"]),0))
    pr=up/max(down,1e-6)
    p4=float(np.clip(1.5+(pr-1)*.75,0,3))
    d.append(("5日上行/回撤",p4,3))

    p5=1.0
    if reli["enough_oos"] and reli["test"] and reli["train"]:
        te,tr=reli["test"],reli["train"]
        p5=1.5 + (.75 if te["avg"]>0 else -.5) + (.75 if abs(te["win"]-tr["win"])<=.10 else 0)
    d.append(("样本外稳定",float(np.clip(p5,0,3)),3))

    p6=float(2*(.45*np.clip((reli["n"]-15)/85,0,1)+.55*np.clip(reli["confidence"]/100,0,1)))
    d.append(("样本/可信度",p6,2))
    pts=sum(v for _,v,_ in d)
    return {"points":pts,"score100":int(round(np.clip(pts/25*100,0,100))),"detail":d}

def oos_grade_v633(reli):
    """
    样本外验证从“一票否决”改成统计强弱分级。
    同时看：样本量、胜率、平均收益、中位数近似、训练/测试稳定性、可信度。
    不因轻微负收益直接判死刑；明显负期望且样本充分仍可强否决。
    """
    if not reli or not reli.get("enough_oos") or not reli.get("test"):
        return {"level":"⚪ 样本不足","score":45,"hard_veto":False,
                "reason":"样本外案例不足，不能证明有效，也不能仅凭这一项否决。"}

    te=reli["test"]; tr=reli.get("train")
    n=int(te.get("n",0))
    win=float(te.get("win",0.5))
    avg=float(te.get("avg",0))
    # 当前统计对象没有逐笔中位数缓存时，用胜负均值构造保守的中心收益近似；
    # 后续有逐笔测试样本时可直接替换为真实median。
    aw=float(te.get("avg_win",0)); al=float(te.get("avg_loss",0))
    center=win*aw+(1-win)*al
    conf=float(reli.get("confidence",50))/100

    s=50.0
    s += np.clip(avg/.02*18,-22,22)
    s += np.clip((win-.50)/.15*12,-14,14)
    s += np.clip(center/.02*6,-7,7)
    if tr:
        gap=abs(win-float(tr.get("win",win)))
        s += 5 if gap<=.08 else (2 if gap<=.15 else -5)
        agap=abs(avg-float(tr.get("avg",avg)))
        s += 4 if agap<=.015 else (1 if agap<=.03 else -4)
    s += np.clip((n-12)/38*5,0,5)
    s = 50 + (s-50)*(0.65+0.35*conf)
    s=float(np.clip(s,0,100))

    # 只有“明显负期望 + 较低胜率 + 足够样本”才强否决。
    hard = bool(n>=18 and avg<=-.012 and win<.42 and conf>=.55)
    if hard:
        level="🔴 明显负优势"
        reason=f"样本外{n}例，平均5日{avg*100:+.2f}%，胜率{win*100:.1f}%，负期望较明显。"
    elif s>=72:
        level="🟢 强通过"
        reason=f"样本外{n}例，收益、胜率和稳定性共同支持正优势。"
    elif s>=58:
        level="🟢 通过"
        reason=f"样本外{n}例整体偏正，但优势强度仍需结合交易可信度。"
    elif s>=43:
        level="🟡 中性"
        reason=f"样本外{n}例未显示足够强的正/负优势，不应单独决定交易。"
    elif s>=28:
        level="🟠 偏弱"
        reason=f"样本外{n}例偏弱，但尚未达到高置信度强否决条件。"
    else:
        level="🔴 弱通过/不建议"
        reason=f"样本外{n}例表现较差，应显著降低交易可信度。"
    return {"level":level,"score":int(round(s)),"hard_veto":hard,"reason":reason,
            "n":n,"win":win,"avg":avg,"center":center}

def chip_model(x, turn_hist_df=None, bins=55, days=120):
    """
    估算筹码成本分布，不宣称是真实账户持仓。
    有每日换手率时：用换手衰减估算存量筹码；
    无换手率时：退化为成交量加权成本分布。
    """
    w=x.tail(days).copy()
    if len(w)<40:return None
    prices=((w["最高"].astype(float)+w["最低"].astype(float)+w["收盘"].astype(float))/3).to_numpy()
    vols=w["成交量"].astype(float).to_numpy()
    dates=pd.to_datetime(w["日期"]).dt.normalize()
    turns=None; mode="成交量加权估算"; quality="中"
    if turn_hist_df is not None and len(turn_hist_df):
        tm=turn_hist_df.copy()
        tm["日期"]=pd.to_datetime(tm["日期"]).dt.normalize()
        mp=dict(zip(tm["日期"],pd.to_numeric(tm["换手率"],errors="coerce")))
        arr=np.array([mp.get(d,np.nan) for d in dates],float)
        if np.isfinite(arr).sum()>=min(30,len(w)*.6):
            med=np.nanmedian(arr)
            arr=np.where(np.isfinite(arr),arr,med)
            turns=np.clip(arr/100,0,.35)
            mode="换手衰减筹码估算";quality="较高"

    lo=float(np.nanmin(w["最低"]));hi=float(np.nanmax(w["最高"]))
    if not np.isfinite(lo+hi) or hi<=lo:return None
    edges=np.linspace(lo,hi,bins+1);centers=(edges[:-1]+edges[1:])/2
    mass=np.zeros(bins,float)

    if turns is not None:
        # 每天换手意味着旧筹码按 (1-turnover) 衰减，新成交加入当日成本附近
        for px,vol,to in zip(prices,vols,turns):
            mass*=max(0,1-to)
            idx=int(np.clip(np.searchsorted(edges,px)-1,0,bins-1))
            mass[idx]+=max(to,1e-5)
    else:
        for px,vol in zip(prices,vols):
            idx=int(np.clip(np.searchsorted(edges,px)-1,0,bins-1))
            mass[idx]+=max(vol,0)

    if mass.sum()<=0:return None
    mass/=mass.sum()
    peak=float(centers[np.argmax(mass)])
    mean=float(np.sum(centers*mass))
    cdf=np.cumsum(mass)
    def quant(q):
        return float(centers[min(np.searchsorted(cdf,q),len(centers)-1)])
    c30,c15,c85,c95=quant(.30),quant(.15),quant(.85),quant(.95)
    current=float(x.iloc[-1]["收盘"])
    profit=float(mass[centers<current].sum())
    concentration=float(mass[(centers>=c30)&(centers<=c85)].sum())

    # 上方筹码压力：当前价上方约 0~12% 的筹码占比
    overhead=float(mass[(centers>current)&(centers<=current*1.12)].sum())

    # 潜在兑现区：上方筹码峰 + 技术压力融合
    z=x.iloc[-1];atr=float(z.ATR) if np.isfinite(z.ATR) else current*.02
    candidates=[q for q in [peak,c85,c95,z.MA20,z.MA30,z.MA60,z.HIGH20,z.HIGH60]
                if np.isfinite(q) and q>current]
    first=min(candidates) if candidates else current+1.5*atr
    strong=max(first+0.6*atr, c85 if c85>current else first+1.2*atr)
    zone1=(max(current,first-.25*atr),first+.25*atr)
    zone2=(max(zone1[1],strong-.35*atr),strong+.45*atr)

    # 筹码结构 0-100：不追求高分，只评估当前价相对成本与上方压力
    sc=50.0
    # 当前略高于主成本但不离谱通常较健康；过度远离成本扣分
    rel=current/peak-1 if peak>0 else 0
    if 0<=rel<=.08:sc+=14
    elif -.04<=rel<0:sc+=7
    elif rel>.15:sc-=12
    elif rel<-.08:sc-=10
    sc += np.clip((profit-.5)*22,-11,11)
    sc -= np.clip(overhead/.35*18,0,18)
    # 成本区越窄，筹码越集中；这里只给有限加分，避免重复
    width=(c85-c30)/current if current>0 else .2
    sc += np.clip((.12-width)/.12*8,-8,8)
    return {"score":int(np.clip(sc,0,100)),"peak":peak,"mean":mean,
            "c70lo":c15,"c70hi":c85,"c55lo":c30,"c55hi":c85,
            "profit":profit,"overhead":overhead,"width":width,
            "zone1":zone1,"zone2":zone2,"mode":mode,"quality":quality}

def score(x,sim,n):
    z=x.iloc[-1];p=x.iloc[-2]
    c=float(z["收盘"]);o=float(z["开盘"]);h=float(z["最高"]);l=float(z["最低"])
    pc=float(p["收盘"])
    rng=max(h-l,1e-8)
    lower=float((min(o,c)-l)/rng)
    upper=float((h-max(o,c))/rng)
    close_pos=float((c-l)/rng)
    body=abs(c-o)/rng
    vr=float(z.VR20) if np.isfinite(z.VR20) else 1.0
    ret=float(c/pc-1)
    atr=float(z.ATR) if np.isfinite(z.ATR) and z.ATR>0 else max(c*.02,1e-8)

    # 趋势评分
    t=50.0
    t += np.clip((c/z.MA20-1)/.04*15,-15,15) if np.isfinite(z.MA20) else 0
    t += np.clip(float(z.SLOPE20)/.025*12,-12,12) if np.isfinite(z.SLOPE20) else 0
    t += np.clip((float(z.RSI)-50)/25*10,-10,10) if np.isfinite(z.RSI) else 0
    t += np.clip(float(z.MACDH)/atr*8,-8,8) if np.isfinite(z.MACDH) else 0
    if np.isfinite(z.MA5) and np.isfinite(z.MA10) and np.isfinite(z.MA20):
        t += 7 if z.MA5>=z.MA10>=z.MA20 else (-5 if z.MA5<=z.MA10<=z.MA20 else 0)

    # 支撑 / 压力距离，统一用 ATR 标准化
    supports=[q for q in [z.MA5,z.MA10,z.MA20,z.MA30,z.MA60,z.LOW20] if np.isfinite(q) and q>0]
    resistances=[q for q in [z.MA5,z.MA10,z.MA20,z.MA30,z.MA60,z.HIGH20,z.HIGH60] if np.isfinite(q) and q>0]
    support_dist=min([abs(l-q)/atr for q in supports],default=9.0)
    resistance_dist=min([abs(h-q)/atr for q in resistances],default=9.0)
    near_support=support_dist<=0.55
    near_resistance=resistance_dist<=0.55

    # 当前价格在20/60日区间的位置，用于识别高位派发 vs 低位试盘
    low20=float(z.LOW20) if np.isfinite(z.LOW20) else l
    high20=float(z.HIGH20) if np.isfinite(z.HIGH20) else h
    pos20=float((c-low20)/max(high20-low20,1e-8))
    low60=float(z.LOW60) if np.isfinite(z.LOW60) else low20
    high60=float(z.HIGH60) if np.isfinite(z.HIGH60) else high20
    pos60=float((c-low60)/max(high60-low60,1e-8))

    # -------- 下影承接 0-100 --------
    wick_score=0.0
    if lower>=.30:
        wick_score += np.clip((lower-.30)/.40*35,0,35)
        wick_score += np.clip((close_pos-.55)/.35*25,0,25)
        wick_score += 18 if near_support else 0
        if .75<=vr<=1.8: wick_score += 12
        elif vr<.45: wick_score -= 5
        elif vr>2.2 and ret<0: wick_score -= 18
        if c>=o: wick_score += 7
        if c>=pc*.995: wick_score += 5
    wick_score=float(np.clip(wick_score,0,100))
    if lower<.30: wick_label="无明显长下影"
    elif wick_score>=75: wick_label="强承接"
    elif wick_score>=55: wick_label="疑似承接"
    else: wick_label="仅长下影，承接未确认"

    # -------- 上影抛压 0-100 --------
    pressure=0.0
    if upper>=.25:
        # 上影本身
        pressure += np.clip((upper-.25)/.45*32,0,32)
        # 收盘越靠近全天低位，冲高回落越明显
        pressure += np.clip((.55-close_pos)/.40*24,0,24)
        # 正好打到技术压力
        pressure += 17 if near_resistance else 0
        # 放量长上影比缩量上影更值得警惕
        if 1.20<=vr<=2.20: pressure += np.clip((vr-1.2)/1.0*12+5,5,17)
        elif vr>2.20: pressure += 20
        elif vr<.65: pressure -= 5
        # 高位长上影更偏向兑现/派发风险
        if pos20>=.78: pressure += 10
        if pos60>=.82: pressure += 6
        # 阴线/跌回前收增强抛压
        if c<o: pressure += 6
        if c<pc*.995: pressure += 6

    # 低位试盘修正：低位出现上影，不能机械判为派发
    probe=False
    if upper>=.35 and pos20<=.35 and pos60<=.45 and c>=float(z.MA5)*.985:
        probe=True
        pressure -= 18
        if vr>=1.05: pressure -= 5

    pressure=float(np.clip(pressure,0,100))
    if upper<.25:
        pressure_label="无明显长上影"
    elif probe and pressure<65:
        pressure_label="低位试盘可能"
    elif pressure>=85:
        pressure_label="高位派发风险"
    elif pressure>=70:
        pressure_label="强抛压"
    elif pressure>=50:
        pressure_label="疑似抛压"
    else:
        pressure_label="普通上影，抛压未确认"

    # 量价评分：双向净强度 + 量能关系 + 收盘位置
    s=50.0
    if ret>0:
        s += np.clip((vr-1.0)*16,-10,16)
    elif ret<0:
        s += np.clip((1.0-vr)*15,-18,12)
    s += (wick_score-50)*.20
    s -= (pressure-35)*.23
    s += np.clip((close_pos-.5)*14,-7,7)
    s=float(np.clip(s,0,100))

    sig=[]
    if ret<0 and vr<.75:
        sig.append(f"✓ 缩量回落：20日量比 {vr:.2f}×")
    elif ret>0 and vr>1.35:
        sig.append(f"✓ 放量上涨：20日量比 {vr:.2f}×")
    elif ret<0 and vr>1.50:
        sig.append(f"⚠ 放量下跌：20日量比 {vr:.2f}×")
    else:
        sig.append(f"• 量能中性：20日量比 {vr:.2f}×")

    sig.append(f"• 下影 {lower*100:.0f}% ｜ 承接 {wick_score:.0f}/100 ｜ {wick_label}")
    sig.append(f"• 上影 {upper*100:.0f}% ｜ 抛压 {pressure:.0f}/100 ｜ {pressure_label}")
    if near_support:
        sig.append(f"• 低点距最近技术支撑约 {support_dist:.2f} ATR")
    if near_resistance:
        sig.append(f"• 高点距最近技术压力约 {resistance_dist:.2f} ATR")
    if probe:
        sig.append("• 当前处于相对低位，上影已按“试盘可能”降低派发权重")
    elif pressure>=70:
        sig.append("⚠ 冲高回落与位置/量能共同指向较强抛压，需观察下一交易日确认")

    hs=50 if not sim else int(np.clip(
        50+(sim["win"]-.5)*90+np.clip(sim["avg"]/.025,-1.5,1.5)*22+(sim["p55"]-.30)*18,0,100))
    ns=50;sev=False
    if not n.empty:
        tc=next((q for q in n.columns if "标题" in str(q) or str(q).lower()=="title"),n.columns[0])
        for i,txt in enumerate(n[tc].astype(str)):
            w=max(.25,1-i/35)
            ns+=min(6,2*sum(k in txt for k in POS))*w
            ns-=min(9,3*sum(k in txt for k in NEG))*w
            if any(k in txt for k in SEV):sev=True

    detail={
        "vr20":vr,"ret":ret,"lower":lower,"upper":upper,
        "close_pos":close_pos,"body":body,
        "wick_score":wick_score,"wick_label":wick_label,
        "pressure_score":pressure,"pressure_label":pressure_label,
        "support_dist":support_dist,"resistance_dist":resistance_dist,
        "near_support":near_support,"near_resistance":near_resistance,
        "pos20":pos20,"pos60":pos60,"probe":probe,
        "net_strength":wick_score-pressure
    }
    return int(np.clip(t,0,100)),int(s),hs,int(np.clip(ns,0,100)),sev,sig,detail

def current_opportunity(x, chip, qd, sim, reli, plan_ev, rr):
    """
    当前价格机会度 0-100：
    与“股票质量”分离。重点回答现在这个价格是否有短线性价比。
    不把“套牢盘多”机械等同于“不能买”。
    """
    z=x.iloc[-1]
    c=float(z["收盘"])
    atr=float(z.ATR) if np.isfinite(z.ATR) and z.ATR>0 else max(c*.02,1e-8)
    score=50.0
    reasons=[]
    risks=[]

    # 1) 支撑距离 / 承接 / 抛压
    sd=float(qd.get("support_dist",9))
    if sd<=.25:
        score+=12; reasons.append(f"非常接近技术支撑（{sd:.2f} ATR）")
    elif sd<=.55:
        score+=7; reasons.append(f"接近技术支撑（{sd:.2f} ATR）")
    elif sd>1.2:
        score-=5; risks.append("距离明确技术支撑偏远")

    wick=float(qd.get("wick_score",0))
    pressure=float(qd.get("pressure_score",0))
    score += np.clip((wick-45)*.12,-4,7)
    score -= np.clip((pressure-45)*.14,-3,9)
    if wick>=60: reasons.append(f"下影承接较强（{wick:.0f}/100）")
    if pressure>=70: risks.append(f"上影抛压较强（{pressure:.0f}/100）")

    # 2) 量能：缩量回踩加分，放量下跌扣分
    vr=float(qd.get("vr20",1))
    ret=float(qd.get("ret",0))
    if ret<=0 and .45<=vr<=.85:
        score+=9; reasons.append(f"缩量回落（20日量比 {vr:.2f}×）")
    elif ret<0 and vr>=1.5:
        score-=11; risks.append(f"放量下跌（20日量比 {vr:.2f}×）")
    elif ret>0 and 1.15<=vr<=2.0:
        score+=5; reasons.append("上涨伴随温和放量")

    # 3) 成本位置：低于平均成本可以是反弹性价比，但必须受趋势约束
    if chip:
        mean=float(chip["mean"]); peak=float(chip["peak"])
        discount=(c/mean-1) if mean>0 else 0
        overhead=float(chip["overhead"])
        if -.15<=discount<=-.05:
            score+=8; reasons.append(f"当前价低于估算平均成本 {abs(discount)*100:.1f}%")
        elif discount<-.15:
            score+=3; risks.append("价格大幅低于估算成本，可能属于弱势下跌而非单纯便宜")
        elif discount>.12:
            score-=7; risks.append("当前价明显高于估算平均成本，追高性价比下降")

        # 上方套牢盘是压力，不直接否决底部机会
        if overhead>=.35:
            score-=7; risks.append(f"上方近端筹码压力较大（约{overhead*100:.0f}%）")
        elif overhead<=.15:
            score+=4; reasons.append("近端上方筹码压力较轻")

    # 4) 趋势状态：防止“便宜=机会”
    slope=float(z.SLOPE20) if np.isfinite(z.SLOPE20) else 0
    ma20=float(z.MA20) if np.isfinite(z.MA20) else c
    ma5=float(z.MA5) if np.isfinite(z.MA5) else c
    ma10=float(z.MA10) if np.isfinite(z.MA10) else c
    if slope>0 and c>=ma20:
        score+=9; reasons.append("MA20向上且价格位于MA20上方")
    elif slope<-.008 and c<ma20:
        score-=10; risks.append("MA20仍明显向下，反弹机会需防接飞刀")
    elif slope>=-.003:
        score+=4; reasons.append("MA20斜率接近走平")
    if ma5>=ma10:
        score+=4; reasons.append("短均线结构改善")

    # 5) RSI位置
    rsi=float(z.RSI) if np.isfinite(z.RSI) else 50
    if 32<=rsi<=48:
        score+=5; reasons.append(f"RSI {rsi:.0f}，处于偏低但非极端区域")
    elif rsi<25:
        score-=2; risks.append("RSI极低，需等待止跌确认")
    elif rsi>72:
        score-=7; risks.append("RSI偏高，短线追入风险增加")

    # 6) 历史真实性 + 交易期望，只有限度参与机会分
    if reli:
        if reli["enough_oos"] and reli["test"]:
            if reli["test"]["avg"]>0 and reli["test"]["win"]>=.5:
                score+=7; reasons.append("样本外历史表现为正")
            elif reli["test"]["avg"]<=0:
                score-=8; risks.append("样本外平均收益不为正")
        if reli["confidence"]<45:
            score-=4; risks.append("历史统计可信度偏低")
    if np.isfinite(plan_ev):
        if plan_ev>.008: score+=5
        elif plan_ev<=0: score-=7; risks.append("当前交易计划统计期望不为正")
    if np.isfinite(rr):
        if rr>=2: score+=5; reasons.append(f"计划盈亏比 {rr:.2f}")
        elif rr<1.2: score-=7; risks.append(f"计划盈亏比仅 {rr:.2f}")

    score=float(np.clip(score,0,100))
    if score>=80: label="🟢 强机会候选"
    elif score>=70: label="🟢 有机会，可等待/执行触发条件"
    elif score>=60: label="🟡 有一定机会，适合观察"
    elif score>=50: label="🟡 中性，等待更好价格或确认"
    else: label="🔴 当前机会不足"
    return {"score":int(round(score)),"label":label,
            "reasons":reasons[:6],"risks":risks[:6]}

def trading_confidence(reli, data_quality_ok=True):
    """只回答统计/数据有多可信，不回答涨跌方向。"""
    if not reli:return 30
    c=float(reli["confidence"])
    if reli["n"]<35:c-=10
    if not reli["enough_oos"]:c-=8
    if not data_quality_ok:c-=8
    return int(np.clip(c,0,100))

def chip_pressure(chip):
    """0=上方压力轻，100=上方压力重。与机会度方向分离。"""
    if not chip:return None
    overhead=float(chip["overhead"])
    width=float(chip["width"])
    p=overhead/.40*70
    p+=np.clip((.10-width)/.10*10,-5,10)
    return int(np.clip(p,0,100))

def trade_price_plan(x, opportunity, s1, s2, r1, r2, b1, b2, sl, t1, t2, pull, lo, hi):
    """
    V6.3.5 统一买卖价格计划。
    输出区间而非假装存在唯一精确价格。
    买入区综合支撑、ATR、回踩区和当前价；卖出区综合压力/目标位。
    """
    z=x.iloc[-1]
    c=float(z["收盘"])
    atr=float(z.ATR) if np.isfinite(z.ATR) and z.ATR>0 else max(c*.02, 0.01)

    # 首选回踩买区；没有成熟回踩结构时，用支撑附近的窄区间作为“等待成交区”
    if pull and np.isfinite(lo) and np.isfinite(hi):
        buy_lo=max(0.01,float(lo))
        buy_hi=max(buy_lo,float(hi))
        buy_type="回踩候选买入区"
    else:
        anchor=float(s1) if np.isfinite(s1) and s1>0 else c
        buy_lo=max(0.01, anchor-0.18*atr)
        buy_hi=max(buy_lo, min(c+0.08*atr, anchor+0.22*atr))
        buy_type="支撑附近观察买入区"

    # 不建议为了追涨把买区无限抬到现价上方
    if buy_lo > c*1.025:
        buy_lo=max(0.01,c-0.15*atr)
        buy_hi=c+0.08*atr
        buy_type="当前价附近等待确认区"

    # 第一卖出区优先使用真实上方压力；否则使用模型目标1
    candidates=[v for v in [r1,t1,b1] if np.isfinite(v) and v>max(c,buy_hi)*1.002]
    sell1=min(candidates) if candidates else max(float(t1), c+1.2*atr)
    sell1_lo=max(c, sell1-0.12*atr)
    sell1_hi=sell1+0.12*atr

    # 第二卖出区：更高压力/目标2
    candidates2=[v for v in [r2,t2,b2] if np.isfinite(v) and v>sell1_hi*1.003]
    sell2=min(candidates2) if candidates2 else max(float(t2), sell1_hi+0.8*atr)
    sell2_lo=max(sell1_hi, sell2-0.15*atr)
    sell2_hi=sell2+0.15*atr

    stop=float(sl)
    if not np.isfinite(stop) or stop>=buy_lo:
        stop=max(0.01, min(float(s2) if np.isfinite(s2) else buy_lo-atr, buy_lo-0.55*atr))

    return {
        "buy_lo":buy_lo,"buy_hi":buy_hi,"buy_type":buy_type,
        "sell1_lo":sell1_lo,"sell1_hi":sell1_hi,
        "sell2_lo":sell2_lo,"sell2_hi":sell2_hi,
        "stop":stop
    }

def final_trade_summary(total, opportunity, confidence, reli, net_plan_ev, rr, sev, conflict, stale):
    """首页一句话结论：值得做 / 等待 / 不做。"""
    if conflict or stale:
        return "⛔ 暂不做", "行情数据校验未通过，先不要依据模型交易。"
    if sev:
        return "⛔ 暂不做", "存在严重消息风险。"
    if not reli or reli["n"]<35:
        return "🟡 等待", "历史独立样本不足，胜率可信度不够。"
    if reli["enough_oos"] and reli["test"] and reli["test"]["avg"]<=0:
        return "🔴 不值得做", "样本外统计偏弱，已计入可信度但不因轻微负收益单独一票否决。"
    if np.isfinite(net_plan_ev) and net_plan_ev<=0:
        return "🔴 不值得做", "扣除交易摩擦后统计期望为负。"
    if (total>=65 and opportunity["score"]>=70 and confidence>=55
        and np.isfinite(rr) and rr>=1.5
        and np.isfinite(net_plan_ev) and net_plan_ev>=.004
        and (not reli["enough_oos"] or reli["test"]["win"]>=.50)):
        return "🟢 值得做候选", "质量、当前机会、可信度、盈亏比和净期望同时通过。"
    if opportunity["score"]>=65 and confidence>=45:
        return "🟡 值得观察", "位置开始有性价比，但交易条件尚未全部确认。"
    return "⚪ 暂不做", "当前机会或统计优势不足，等待更好的价格/确认信号。"

def dynamic_total(ts,ps,hs,ns,chip,sim,plan_ev,rr,news_available=True,reli=None):
    """
    V5.9.1 权重体系：
    真实行情/历史统计 80%：
      量价30 + 趋势25 + 历史统计25
    辅助信息 20%：
      筹码8 + 消息7 + 风险收益5

    缺失项从有效权重剔除后重新归一化。
    历史项不再单纯重复奖励技术指标，而是结合
    贝叶斯/Wilson/样本外稳定性修正历史分。
    """
    parts=[]

    # 真实市场 55%
    parts.append(("量价",float(ps),30))
    parts.append(("趋势",float(ts),25))

    # 历史统计 25%：用V5.9真实性系统校正
    if sim is not None:
        hist=float(hs)
        if reli is not None:
            # 可信度决定我们多大程度相信历史分；低可信度向50收缩
            trust=np.clip(reli["confidence"]/100,0,1)
            hist=50+(hist-50)*trust

            # 样本外是主要真实性校验，不直接制造高分
            if reli["enough_oos"] and reli["test"] is not None:
                oos=reli["test"]
                oos_component=50
                oos_component += np.clip((oos["win"]-.50)*70,-20,20)
                oos_component += np.clip(oos["avg"]/.02*20,-20,20)
                oos_component=float(np.clip(oos_component,0,100))
                hist=.60*hist+.40*oos_component

                # 样本外负期望时限制历史项，而不是靠其他指标掩盖
                if oos["avg"]<=0:
                    hist=min(hist,48)

            # Wilson保守下限很低时，限制历史统计项
            if reli["lo"]<.45:
                hist=min(hist,52)

        parts.append(("历史统计",float(np.clip(hist,0,100)),25))

    # 辅助信息 20%
    if chip is not None:
        parts.append(("筹码估算",float(chip["score"]),8))

    if news_available:
        parts.append(("消息",float(ns),7))

    if np.isfinite(plan_ev) and np.isfinite(rr):
        # 交易机会只占5%，避免高盈亏比把差股票硬抬成高分
        rscore=50
        rscore += np.clip(plan_ev/.015*25,-30,30)
        rscore += np.clip((rr-1.5)*12,-18,18)
        parts.append(("风险收益",float(np.clip(rscore,0,100)),5))

    denom=sum(w for _,_,w in parts)
    total=sum(s*w for _,s,w in parts)/denom if denom else 50
    return int(round(np.clip(total,0,100))),parts

def trend_engine_v63(x):
    """
    V6.3.4 趋势状态机。
    不只判断均线多空，而是识别：
    上升趋势 / 上升回踩 / 再转强 / 下跌延续 / 下跌减速 / 底部转强 / 震荡。
    评分由四类证据构成：
      方向40、斜率与加速度25、价格结构20、确认15。
    """
    z=x.iloc[-1]
    c=float(z["收盘"])
    def safe(v,default=0):
        return float(v) if np.isfinite(v) else default
    ma5,ma10,ma20,ma30,ma60=[safe(z[k],c) for k in ["MA5","MA10","MA20","MA30","MA60"]]
    rsi=safe(z.RSI,50)
    macdh=safe(z.MACDH,0)

    # MA20 normalized slopes over several horizons: level + acceleration
    m=x["MA20"].astype(float)
    s_now=(m.iloc[-1]/m.iloc[-4]-1) if len(m)>=4 and np.isfinite(m.iloc[-4]) and m.iloc[-4]!=0 else 0
    s_prev=(m.iloc[-4]/m.iloc[-7]-1) if len(m)>=7 and np.isfinite(m.iloc[-7]) and m.iloc[-7]!=0 else s_now
    accel=s_now-s_prev

    # Price structure: compare recent swing windows rather than only current MA.
    hi5=float(x["最高"].tail(5).max()); lo5=float(x["最低"].tail(5).min())
    hi_prev=float(x["最高"].iloc[-10:-5].max()) if len(x)>=10 else hi5
    lo_prev=float(x["最低"].iloc[-10:-5].min()) if len(x)>=10 else lo5
    higher_high=hi5>hi_prev
    higher_low=lo5>lo_prev
    lower_high=hi5<hi_prev
    lower_low=lo5<lo_prev

    # 1. Direction 0-40
    direction=20.0
    if ma5>ma10>ma20: direction+=10
    elif ma5<ma10<ma20: direction-=10
    if c>ma20: direction+=6
    else: direction-=6
    if ma20>ma60: direction+=4
    else: direction-=4
    direction=float(np.clip(direction,0,40))

    # 2. Slope + acceleration 0-25
    slope_score=12.5
    slope_score += np.clip(s_now/.015*7,-7,7)
    slope_score += np.clip(accel/.012*5.5,-5.5,5.5)
    slope_score=float(np.clip(slope_score,0,25))

    # 3. Price structure 0-20
    structure=10.0
    if higher_high:structure+=5
    if higher_low:structure+=5
    if lower_high:structure-=4
    if lower_low:structure-=6
    structure=float(np.clip(structure,0,20))

    # 4. Confirmation 0-15
    confirm=7.5
    if macdh>0:confirm+=3
    else:confirm-=2
    if 45<=rsi<=68:confirm+=2.5
    elif rsi<30:confirm-=1
    elif rsi>75:confirm-=2
    if ma5>ma10:confirm+=2
    else:confirm-=1
    confirm=float(np.clip(confirm,0,15))

    score=int(round(np.clip(direction+slope_score+structure+confirm,0,100)))

    # State machine: state is not simply score bucket.
    if s_now>0 and c>=ma20 and ma5>=ma10 and higher_low:
        if c<hi_prev and c<=ma5*1.015:
            state="🟢 上升趋势回踩"
            desc="中期斜率仍向上，价格回到短期成本附近且低点未破坏，属于顺势回踩候选。"
        elif higher_high:
            state="🟢 上升趋势 / 再转强"
            desc="均线方向、斜率和价格结构同时偏强。"
        else:
            state="🟢 上升趋势"
            desc="价格位于MA20上方且趋势斜率为正，但突破结构仍需继续确认。"
    elif s_now<0 and accel>0 and (higher_low or not lower_low):
        if ma5>=ma10 and macdh>0:
            state="🟠 底部转强候选"
            desc="MA20仍偏下，但下降速度明显减慢，短均线和动能开始改善；属于反转候选，不等于反转已经确认。"
        else:
            state="🟡 下跌趋势减速"
            desc="趋势仍偏弱，但MA20下降速度减慢，价格结构不再明显创新低，需等待进一步确认。"
    elif s_now<0 and accel<=0 and c<ma20 and lower_low:
        state="🔴 下跌趋势延续"
        desc="价格位于MA20下方、斜率仍负且近期低点继续下移，接飞刀风险较高。"
    elif abs(s_now)<.004 and not lower_low:
        state="🟡 震荡 / 筑底观察"
        desc="趋势斜率接近走平，价格结构暂未继续恶化，等待突破或转强确认。"
    else:
        state="⚪ 趋势过渡"
        desc="多空证据混合，尚未形成稳定趋势状态。"

    return {
        "score":score,"state":state,"desc":desc,
        "direction":direction,"slope_score":slope_score,
        "structure":structure,"confirm":confirm,
        "s_now":s_now,"s_prev":s_prev,"accel":accel,
        "higher_high":higher_high,"higher_low":higher_low,
        "lower_high":lower_high,"lower_low":lower_low,
        "close":c,"ma5":ma5,"ma10":ma10,"ma20":ma20,"ma60":ma60,
        "rsi":rsi,"macdh":macdh
    }

def trend_stage(x):
    z=x.iloc[-1]; p=x.iloc[-2]
    c=float(z["收盘"])
    ma5=float(z.MA5); ma10=float(z.MA10); ma20=float(z.MA20); ma30=float(z.MA30)
    slope20=float(z.SLOPE20) if np.isfinite(z.SLOPE20) else 0.0
    slope5=(ma5/float(x.iloc[-4].MA5)-1) if len(x)>=4 and np.isfinite(x.iloc[-4].MA5) and x.iloc[-4].MA5 else 0.0
    rsi_v=float(z.RSI) if np.isfinite(z.RSI) else 50
    mac=float(z.MACDH) if np.isfinite(z.MACDH) else 0
    vr=float(z.VR20) if np.isfinite(z.VR20) else 1

    checks = {
        "收盘站上MA20": c >= ma20,
        "MA20走平/向上": slope20 >= -0.001,
        "MA5拐头向上": slope5 > 0,
        "MA5站上MA10": ma5 >= ma10,
        "MACD动能非负": mac >= 0,
        "RSI站上50": rsi_v >= 50,
    }
    passed=sum(checks.values())

    # 阶段识别：不把“未站上MA20”简单等同于纯下跌
    if c < ma20 and slope20 < -0.006 and ma5 < ma10:
        stage="🔴 下跌趋势"
        desc="中期均线仍明显向下，优先防守。"
    elif c < ma20 and (slope5 > 0 or mac > 0 or rsi_v >= 45):
        stage="🟠 止跌观察"
        desc="短线已有止跌迹象，但尚未重新站稳MA20。"
    elif abs(c/ma20-1) <= .018 and slope20 >= -0.004 and passed >= 3:
        stage="🟡 筑底/临界"
        desc="价格围绕MA20整理，部分转强条件正在形成。"
    elif c >= ma20 and slope20 >= -0.001 and passed >= 4:
        stage="🟢 转强"
        desc="已站上MA20，多项短线条件转好，但仍需突破压力确认。"
    elif c >= ma20 and slope20 > .004 and ma5 >= ma10 >= ma20 and mac > 0:
        stage="🔵 上升趋势"
        desc="均线与动能形成较完整的上升结构。"
    else:
        stage="🟡 震荡/等待"
        desc="多空条件混合，暂未形成清晰趋势。"

    return stage, desc, checks, {
        "close":c,"ma5":ma5,"ma10":ma10,"ma20":ma20,"ma30":ma30,
        "slope20":slope20,"slope5":slope5,"rsi":rsi_v,"macd":mac,"vr20":vr,
        "passed":passed
    }

def levels(x):
    z=x.iloc[-1];c=float(z["收盘"]);a=max(float(z.ATR),c*.008)
    mas=[float(z[f"MA{n}"]) for n in [5,10,20,30,60]]
    sp=sorted(set(q for q in mas+[float(z.LOW20),float(z.LOW60)] if np.isfinite(q) and q<c),reverse=True)
    rp=sorted(set(q for q in mas+[float(z.HIGH20),float(z.HIGH60)] if np.isfinite(q) and q>c))
    s1=sp[0] if sp else c-a
    s2=sp[1] if len(sp)>1 else s1-a
    r1=rp[0] if rp else c+a
    r2=rp[1] if len(rp)>1 else r1+a

    # 回踩条件保留，但趋势解释交给 trend_stage()
    pull=c>=z.MA20 and z.SLOPE20>=0
    if pull:
        center=max(s1,c-.65*a);lo=max(c-a,center-.2*a);hi=min(c+.08*a,center+.2*a)
        if lo>hi:lo,hi=hi,lo
    else:
        lo=hi=np.nan

    # 两级突破：
    # B1 = 最近的第一技术压力，适合观察“初步转强”
    # B2 = 20日结构高点/第二压力，适合确认“结构突破”
    b1=max(r1, c + .15*a)
    structural=float(z.HIGH20) if np.isfinite(z.HIGH20) else r2
    b2=max(r2, structural*.995, b1+.35*a)

    sl=(s1-.8*a if pull else min(s1,c-1.10*a))
    t1=max(r1,c+1.5*a)
    t2=max(r2,c+2.4*a)
    return s1,s2,r1,r2,lo,hi,b1,b2,sl,t1,t2,pull


def eod_scan_date():
    """以上海时间决定应展示的最近一次收盘扫描日期。"""
    now=datetime.now(ZoneInfo("Asia/Shanghai"))
    d=now.date()
    # 周末回退到周五；工作日15:10前回退到前一交易日（节假日由行情日期再次校正）
    if d.weekday()>=5:
        d=d-timedelta(days=d.weekday()-4)
    elif now.hour<15 or (now.hour==15 and now.minute<10):
        d=d-timedelta(days=1)
        while d.weekday()>=5:d-=timedelta(days=1)
    return str(d)

@st.cache_data(ttl=60*60*18,show_spinner=False)
def daily_top20(scan_day):
    """V6.7：代表性市场池 -> K线轻筛 -> 约24只深度评分 -> Top20。"""
    universe=representative_universe()
    light=[];fail=0
    for _,r in universe.iterrows():
        code=str(r["代码"]).zfill(6)
        try:
            raw,hsrc,_=get_hist(code)
            if raw is None or len(raw)<120: continue
            q,r5,r20,vr=quick_candidate_metrics(raw)
            light.append({"代码":code,"名称":r["名称"],"候选来源":r["候选来源"],
                          "交易板块":r["交易板块"],"轻筛机会":q,"_raw":raw,"历史源":hsrc})
        except Exception:
            fail+=1
    if not light:return pd.DataFrame()
    light_df=pd.DataFrame(light).sort_values("轻筛机会",ascending=False)
    picked=[];cnt={}
    for _,r in light_df.iterrows():
        src=r["候选来源"]
        if cnt.get(src,0)>=6:continue
        picked.append(r);cnt[src]=cnt.get(src,0)+1
        if len(picked)>=24:break

    rows=[]
    for r in picked:
        code=str(r["代码"]).zfill(6)
        try:
            x=feat(r["_raw"]).reset_index(drop=True)
            sim=similar(x);reli=winrate_reliability(sim);oos=oos_grade_v633(reli)
            n=pd.DataFrame();chip=chip_model(x,None)
            ts,ps,hs,ns,sev,sigs,qd=score(x,sim,n)
            trend=trend_engine_v63(x);ts=trend["score"]
            s1,s2,r1,r2,lo,hi,b1,b2,sl,t1,t2,pull=levels(x)
            pp=trade_price_plan(x,None,s1,s2,r1,r2,b1,b2,sl,t1,t2,pull,lo,hi)
            entry=(pp["buy_lo"]+pp["buy_hi"])/2;target=(pp["sell1_lo"]+pp["sell1_hi"])/2;stop=pp["stop"]
            up=max(target/entry-1,0) if entry>0 else 0;down=max(1-stop/entry,0) if entry>0 else 0
            wr=(float(reli["test"]["win"]) if reli and reli["enough_oos"] else float(reli["conservative"]) if reli else np.nan)
            ev=(wr*up-(1-wr)*down) if np.isfinite(wr) else np.nan;rr=up/down if down>0 else np.nan
            path=path_trade_stats(sim,up,down);hist25=historical_score_25(sim,reli,path,up,down)
            if sim and reli:hs=hist25["score100"]
            opp=current_opportunity(x,chip,qd,sim,reli,ev,rr);conf=trading_confidence(reli,True)
            total,_=dynamic_total(ts,ps,hs,ns,chip,sim,ev,rr,news_available=False,reli=reli)
            if sev:total=min(total,55)
            rows.append({"代码":code,"名称":r["名称"],"候选来源":r["候选来源"],
                         "交易板块":r["交易板块"],"轻筛机会":round(float(r["轻筛机会"]),1),
                         "综合评分":int(total),"机会分":int(opp),"可信度":int(conf),
                         "趋势状态":trend["state"],"买入区":f'{pp["buy_lo"]:.2f}–{pp["buy_hi"]:.2f}',
                         "止损":round(stop,2),"目标1":f'{pp["sell1_lo"]:.2f}–{pp["sell1_hi"]:.2f}',
                         "目标2":f'{pp["sell2_lo"]:.2f}–{pp["sell2_hi"]:.2f}',
                         "样本外":oos["level"],"历史源":r["历史源"]})
        except Exception: continue
    if not rows:return pd.DataFrame()
    ranked=pd.DataFrame(rows).sort_values(["综合评分","机会分","可信度"],ascending=False)
    selected=[];counts={}
    for _,r in ranked.iterrows():
        src=r["候选来源"]
        if counts.get(src,0)>=6:continue
        selected.append(r);counts[src]=counts.get(src,0)+1
        if len(selected)>=20:break
    out=pd.DataFrame(selected).reset_index(drop=True)
    if out.empty:return out
    out.index=out.index+1
    out.attrs["universe_count"]=len(universe);out.attrs["light_count"]=len(light_df)
    out.attrs["deep_count"]=len(picked);out.attrs["failed_count"]=fail
    return out

st.title("📈 A股短线模型 V6.7")
st.caption("分池候选雷达 · 无全A快照 · 约24只深度评分 · 每日Top20")

page=st.radio("模式",["🔭 收盘雷达","🔎 个股分析"],horizontal=True,label_visibility="collapsed")
if "selected_code" not in st.session_state: st.session_state.selected_code="002159"

if page=="🔭 收盘雷达":
    st.subheader("🏆 每日收盘 Top20")
    scan_day=eod_scan_date()
    st.caption(f"收盘扫描日：{scan_day}｜不再请求全A快照：从代表性市场池取少量候选，K线轻筛后仅对约24只运行完整核心模型；单个数据源失败不会拖垮整个榜单。")
    try:
        with st.spinner("首次打开正在扫描候选并计算 Top20，之后打开会直接读取当天缓存…"):
            top20=daily_top20(scan_day)
        if top20 is not None and not top20.empty:
            st.caption(f"候选池 {top20.attrs.get('universe_count','—')} 只 → K线轻筛 {top20.attrs.get('light_count','—')} 只 → 深度评分 {top20.attrs.get('deep_count','—')} 只；失败 {top20.attrs.get('failed_count',0)} 只自动跳过。")
            board_top=st.selectbox("Top20板块",["全部"]+list(top20["交易板块"].dropna().unique()),key="top_board")
            tv=top20 if board_top=="全部" else top20[top20["交易板块"]==board_top]
            evtop=st.dataframe(tv,use_container_width=True,on_select="rerun",selection_mode="single-row",height=430)
            rrsel=evtop.selection.rows if hasattr(evtop,"selection") else []
            if rrsel:
                pick=tv.iloc[rrsel[0]];st.session_state.selected_code=str(pick["代码"]).zfill(6)
                if st.button("打开 Top20 选中股票完整分析",type="primary",use_container_width=True):
                    st.session_state.auto_analyze=True;st.session_state.page_to_analysis=True;st.rerun()
        else: st.info("本次扫描没有得到足够的有效候选。")
    except Exception as e:
        st.warning(f"Top20 自动扫描暂不可用：{e}")

    st.divider()
    st.subheader("板块候选池")
    st.caption("已取消全A市场快照，只浏览代表性市场池，避免一次拉取数千只股票导致远端断开。")
    try:
        uni=representative_universe()
        srcs=["全部"]+list(uni["候选来源"].dropna().unique())
        src=st.selectbox("候选板块/市场池",srcs,key="radar_src")
        q=st.text_input("搜索候选代码/名称",placeholder="例如：600958",key="radar_q")
        view=uni if src=="全部" else uni[uni["候选来源"]==src]
        if q:
            q=q.strip()
            view=view[view["代码"].str.contains(q,case=False,na=False)|view["名称"].astype(str).str.contains(q,case=False,na=False)]
        event=st.dataframe(view[["代码","名称","候选来源","交易板块"]],use_container_width=True,
                           hide_index=True,on_select="rerun",selection_mode="single-row",height=420)
        rows=event.selection.rows if hasattr(event,"selection") else []
        if rows:
            row=view.iloc[rows[0]];st.session_state.selected_code=str(row["代码"]).zfill(6)
            if st.button("打开这只股票完整分析",type="primary",use_container_width=True):
                st.session_state.auto_analyze=True;st.session_state.page_to_analysis=True;st.rerun()
    except Exception as e:
        st.warning(f"候选池暂不可用：{e}")
        st.caption("单股分析仍可正常使用；候选池数据源恢复后会自动恢复。")

if st.session_state.pop("page_to_analysis",False): page="🔎 个股分析"

if page=="🔎 个股分析":
    code=st.text_input("输入6位A股代码",value=st.session_state.selected_code,placeholder="例如：002159",max_chars=6,key="code_input")
    st.session_state.selected_code=code
    st.caption(f"交易板块：{board_name(code) if code.isdigit() and len(code)==6 else '—'}")
    capital=st.number_input("模拟投入金额（元）",min_value=1000.0,max_value=10000000.0,value=10000.0,step=1000.0)
    run_now=st.button("开始分析",type="primary",use_container_width=True) or st.session_state.pop("auto_analyze",False)
else:
    code=st.session_state.selected_code
    capital=10000.0
    run_now=False

if run_now:
    if not(code.isdigit() and len(code)==6):st.error("请输入正确6位代码")
    else:
        raw,hsrc,herrs=get_hist(code)
        if raw is None:st.error("历史行情均失败");st.code("\n".join(herrs))
        else:
            try:
                x=feat(raw).reset_index(drop=True);z=x.iloc[-1];last=pd.Timestamp(z["日期"]);close=float(z["收盘"])
                quotes,qerrs=get_quotes(code)
                # 双实时源一致性
                live_prices=[q["price"] for q in quotes if np.isfinite(q.get("price",np.nan)) and q["price"]>0]
                live=np.median(live_prices) if live_prices else np.nan
                live_diff=(max(live_prices)/min(live_prices)-1) if len(live_prices)>=2 and min(live_prices)>0 else np.nan
                conflict=bool(np.isfinite(live_diff) and live_diff>.01)
                stale=(pd.Timestamp.now().normalize()-last.normalize()).days>5

                # 换手率：优先历史直接字段；当天有实时成交量时，尝试流通股计算
                th=None;terr=None
                try:th=turn_hist(code)
                except Exception as e:terr=str(e)
                calc_turn=np.nan;float_sh=np.nan
                sinaq=next((q for q in quotes if q["source"]=="新浪实时"),None)
                if sinaq and sinaq.get("volume",0)>0:
                    try:
                        float_sh=float_shares_em(code)
                        # 新浪 volume 通常为股数；流通股本同为股数
                        calc_turn=sinaq["volume"]/float_sh*100
                    except Exception:pass

                n,nerr=get_news(code)
                sim=similar(x)
                reli=winrate_reliability(sim)
                oos633=oos_grade_v633(reli)
                chip=chip_model(x,th)
                ts,ps,hs,ns,sev,sigs,qd=score(x,sim,n)
                trend63=trend_engine_v63(x)
                # V6.3.4总评分中的趋势25%使用状态机趋势分，不再使用旧的简单趋势分。
                ts=trend63["score"]
                s1,s2,r1,r2,lo,hi,b1,b2,sl,t1,t2,pull=levels(x)
                stage,stage_desc,tchecks,td=trend_stage(x)

                # V6.3.5：全站只使用一套交易价格计划。
                # 首页、胜率/期望、盈亏比与模拟收益均引用同一个 price_plan，避免目标价不一致。
                price_plan=trade_price_plan(x,None,s1,s2,r1,r2,b1,b2,sl,t1,t2,pull,lo,hi)
                entry=(price_plan["buy_lo"]+price_plan["buy_hi"])/2
                target=(price_plan["sell1_lo"]+price_plan["sell1_hi"])/2
                target2=(price_plan["sell2_lo"]+price_plan["sell2_hi"])/2
                stop=price_plan["stop"]
                plan_up=max(target/entry-1,0.0) if entry>0 else 0.0
                plan_down=max(1-stop/entry,0.0) if entry>0 else 0.0
                # V5.9: 优先使用足量样本外胜率；否则使用保守胜率，不再直接用漂亮的原始胜率
                if reli and reli["enough_oos"]:
                    wr=float(reli["test"]["win"])
                    wr_source="样本外胜率"
                elif reli:
                    wr=float(reli["conservative"])
                    wr_source="保守胜率"
                else:
                    wr=np.nan
                    wr_source="不可用"
                plan_ev=(wr*plan_up-(1-wr)*plan_down) if np.isfinite(wr) else np.nan
                rr=(plan_up/plan_down) if plan_down>0 else np.nan
                path_stats=path_trade_stats(sim,plan_up,plan_down)
                hist25=historical_score_25(sim,reli,path_stats,plan_up,plan_down)
                if sim and reli:
                    hs=hist25["score100"]
                expected_yuan=capital*plan_ev if np.isfinite(plan_ev) else np.nan
                win_yuan=capital*plan_up
                loss_yuan=capital*plan_down
                hist_ev_yuan=capital*sim["avg"] if sim else np.nan
                # 粗略交易摩擦预留：双边佣金/滑点等按0.15%估算，可后续做成用户参数
                friction=0.0015
                net_plan_ev=(plan_ev-friction) if np.isfinite(plan_ev) else np.nan
                net_expected_yuan=capital*net_plan_ev if np.isfinite(net_plan_ev) else np.nan

                opportunity=current_opportunity(x,chip,qd,sim,reli,plan_ev,rr)
                confidence=trading_confidence(reli, data_quality_ok=True)
                chip_press=chip_pressure(chip)

                total,score_parts=dynamic_total(
                    ts,ps,hs,ns,chip,sim,plan_ev,rr,
                    news_available=(not n.empty),
                    reli=reli
                )
                # 风险闸门不是为了压分，而是避免严重风险被高分掩盖
                if sev: total=min(total,55)

                if conflict:act="🔴 双实时源冲突：暂停交易分析"
                elif stale:act="⚠️ 历史行情明显滞后：暂停信号"
                elif sev:act="🔴 消息风险：暂停买点"
                elif close<z.MA20 or z.SLOPE20<0:act="🟡 趋势未确认：等待"
                elif pull and lo<=close<=hi and total>=70:act="🟢 回踩候选"
                elif close>=b2*.995 and z.VR20>=1.2 and total>=72:act="🟢 结构突破候选"
                elif close>=b1*.995 and total>=68:act="🟡 第一压力突破观察"
                else:act="🟡 等待/观察"

                st.write("### 数据可信度")
                if conflict:st.error("🔴 低：新浪与腾讯实时价格差异超过1%")
                elif len(live_prices)>=2:st.success("🟢 高：新浪 + 腾讯实时行情交叉校验通过")
                elif len(live_prices)==1:st.warning("🟡 中高：1个实时源可用，历史K线同时正常")
                else:st.warning("🟡 中等：实时源暂不可用，仅使用历史K线")
                st.write(f"历史源 **{hsrc}** ｜ K线日期 **{last.date()}** ｜ K线收盘 **¥{close:.2f}**")
                for q in quotes:st.write(f"{q['source']}：**¥{q['price']:.2f}**")
                if len(live_prices)>=2:st.caption(f"双实时源价差 {live_diff*100:.3f}%")
                if not quotes and qerrs:st.caption("实时接口："+ "；".join(qerrs[:2]))

                st.write("### 换手率")
                latest_hist=np.nan;m5=np.nan;m20=np.nan
                if th is not None and len(th):
                    latest_hist=float(th.iloc[-1]["换手率"]);m5=th["换手率"].tail(5).mean();m20=th["换手率"].tail(20).mean()
                if np.isfinite(calc_turn):
                    st.write(f"实时估算换手率 **{calc_turn:.2f}%** ｜ 5日均值 {m5:.2f}% ｜ 20日均值 {m20:.2f}%")
                    st.caption("实时估算 = 实时成交股数 ÷ 流通股本；最终以券商/交易所口径为准。")
                elif np.isfinite(latest_hist):
                    st.write(f"最新历史换手率 **{latest_hist:.2f}%** ｜ 5日均值 {m5:.2f}% ｜ 20日均值 {m20:.2f}%")
                    st.caption("实时换手计算通道不可用，已自动降级到最新历史换手率。")
                else:
                    st.warning("换手率两个通道均不可用，本次不使用换手率参与判断。")

                st.markdown(f'<div class="box"><div class="big">{act}</div>综合评分 {total}/100</div>',unsafe_allow_html=True)
                if total>=80:grade="强机会"
                elif total>=70:grade="值得重点关注"
                elif total>=60:grade="有条件参与"
                elif total>=50:grade="观察"
                else:grade="暂时回避"

                final_label,final_reason=final_trade_summary(
                    total,opportunity,confidence,reli,net_plan_ev,rr,sev,conflict,stale
                )

                # V6.3.4 首页只保留交易者最需要的信息
                st.write("## 今日交易总结")
                st.markdown(f'<div class="box"><div class="big">{final_label}</div>{final_reason}</div>',unsafe_allow_html=True)
                a,b,c=st.columns(3)
                a.metric("股票质量",f"{total}/100")
                b.metric("当前机会",f"{opportunity['score']}/100")
                c.metric("交易可信度",f"{confidence}/100")

                if final_label.startswith("🟢") or final_label.startswith("🟡"):
                    st.write(f"**建议买入：¥{price_plan['buy_lo']:.2f}–¥{price_plan['buy_hi']:.2f}**  · {price_plan['buy_type']}")
                    st.write(f"**建议第一卖出：¥{price_plan['sell1_lo']:.2f}–¥{price_plan['sell1_hi']:.2f}**")
                    st.write(f"第二卖出参考：¥{price_plan['sell2_lo']:.2f}–¥{price_plan['sell2_hi']:.2f} ｜ **止损/逻辑失效：¥{price_plan['stop']:.2f}**")
                else:
                    st.write("**当前不生成主动买入价。** 先等待机会度/可信度改善。")
                    st.write(f"若后续条件转强，支撑观察区约 ¥{price_plan['buy_lo']:.2f}–¥{price_plan['buy_hi']:.2f}。")

                st.caption("买卖价是基于支撑、ATR、压力位、历史统计与当前结构生成的计划区间，不是保证成交或保证盈利的精确价位。")

                with st.expander("📌 为什么值得做 / 为什么不做"):
                    st.write(f"**股票质量等级：{grade}**")
                    st.write(f"**当前价格判断：{opportunity['label']}**")
                    if opportunity["reasons"]:
                        st.write("**机会依据**")
                        for q in opportunity["reasons"]: st.write(f"✓ {q}")
                    if opportunity["risks"]:
                        st.write("**主要风险**")
                        for q in opportunity["risks"]: st.write(f"⚠ {q}")

                with st.expander("📊 股票质量评分明细"):
                    cols=st.columns(min(3,len(score_parts)))
                    for i,(name,sc,w) in enumerate(score_parts):
                        cols[i%len(cols)].metric(name,f"{sc:.0f}/100",f"权重{w}")
                    eff=sum(w for _,_,w in score_parts)
                    st.caption(f"有效权重 {eff}/100。标准：量价30 + 趋势25 + 历史统计25 = 真实市场80%；筹码8 + 消息7 + 风险收益5 = 辅助20%。")
                st.write("## 详细分析")
                tab_trend,tab_history,tab_chip,tab_volume,tab_expect,tab_news,tab_all=st.tabs(
                    ["趋势","历史相似","筹码","量价/K线","胜率/期望","消息","全部详情"]
                )
                with tab_trend:
                    st.write(f"### {trend63['state']}")
                    st.write(trend63["desc"])
                    st.write(f"**趋势评分：{trend63['score']}/100**")
                    a,b=st.columns(2)
                    a.metric("方向",f"{trend63['direction']:.1f}/40")
                    b.metric("斜率/加速度",f"{trend63['slope_score']:.1f}/25")
                    a.metric("价格结构",f"{trend63['structure']:.1f}/20")
                    b.metric("趋势确认",f"{trend63['confirm']:.1f}/15")
                    st.write(f"MA20近3日斜率 **{trend63['s_now']*100:+.2f}%** ｜ 前一阶段 **{trend63['s_prev']*100:+.2f}%** ｜ 加速度变化 **{trend63['accel']*100:+.2f}%**")
                    structure_txt=[]
                    if trend63["higher_high"]:structure_txt.append("高点抬高")
                    if trend63["higher_low"]:structure_txt.append("低点抬高")
                    if trend63["lower_high"]:structure_txt.append("高点下移")
                    if trend63["lower_low"]:structure_txt.append("低点下移")
                    st.write("价格结构：" + (" / ".join(structure_txt) if structure_txt else "暂无明确高低点结构"))
                    st.write(f"收盘 ¥{trend63['close']:.2f} ｜ MA5 ¥{trend63['ma5']:.2f} ｜ MA10 ¥{trend63['ma10']:.2f} ｜ MA20 ¥{trend63['ma20']:.2f} ｜ MA60 ¥{trend63['ma60']:.2f}")
                    st.write(f"RSI {trend63['rsi']:.1f} ｜ MACD柱 {trend63['macdh']:+.4f}")
                    st.write(f"支撑 ¥{s1:.2f}/¥{s2:.2f} ｜ 压力 ¥{r1:.2f}/¥{r2:.2f}")
                    st.caption("趋势状态机用于识别趋势阶段，不保证未来方向。尤其“底部转强候选”只表示下降减速并出现确认信号，不等于已经反转。")
                with tab_history:
                    if sim:
                        st.write(f"独立相似案例 **{sim['n']}次** ｜ 3日上涨率 **{sim['win3']*100:.1f}%** ｜ 5日上涨率 **{sim['win']*100:.1f}%**")
                        st.write(f"平均3日 **{sim['avg3']*100:+.2f}%** ｜ 平均5日 **{sim['avg']*100:+.2f}%** ｜ 形态接近度 **{sim['similarity']:.1f}/100**")
                        st.write(f"**历史统计评分：{hist25['points']:.1f}/25**")
                        st.write(f"**样本外验证：{oos633['level']} ｜ {oos633['score']}/100**")
                        st.write(oos633["reason"])
                        st.caption("固定5日为主窗口、3日为启动辅助；历史评分不会根据某只股票哪一天表现最好而临时换周期。")
                        st.caption("V6.3.4：样本外验证改为分级。轻微负收益只降低可信度；只有样本充分、平均收益明显为负、胜率明显偏低且可信度足够时才强否决。")
                        for name,pts,mx in hist25["detail"]:
                            st.write(f"{name}：**{pts:.1f}/{mx}**")
                        if path_stats:
                            st.write(f"5日路径：先止盈 **{path_stats['wins']}** ｜ 先止损 **{path_stats['losses']}** ｜ 未触发 **{path_stats['unresolved']}**")
                            if path_stats["resolved"]:
                                st.write(f"已决案例交易胜率 **{path_stats['win']*100:.1f}%**")
                            st.caption("同一日同时触及止盈和止损时，日K无法判断先后，模型保守按止损先触发。")
                        for q in sim["cases"]:
                            st.write(f"{q['date'].date()} ｜ 3日 {q['r3']*100:+.2f}% ｜ 5日 {q['r5']*100:+.2f}% ｜ 最高 {q['r5max']*100:+.2f}% ｜ 回撤 {q['dd']*100:.2f}%")
                    else:
                        st.warning("独立历史形态样本不足。")
                with tab_chip:
                    if chip:
                        st.write(f"上方筹码压力 **{chip_press}/100** ｜ 主筹码峰 **¥{chip['peak']:.2f}** ｜ 平均成本 **¥{chip['mean']:.2f}**")
                        st.write(f"70%成本区 **¥{chip['c70lo']:.2f}–¥{chip['c70hi']:.2f}** ｜ 近端套牢筹码约 **{chip['overhead']*100:.0f}%**")
                        st.caption("筹码为公开成交/换手数据估算，不代表真实账户持仓。")
                    else:
                        st.warning("筹码样本不足。")
                with tab_volume:
                    st.write(f"20日量比 **{qd['vr20']:.2f}×** ｜ 承接 **{qd['wick_score']:.0f}/100** ｜ 抛压 **{qd['pressure_score']:.0f}/100**")
                    st.write(f"下影占比 {qd['lower']*100:.0f}% ｜ 上影占比 {qd['upper']*100:.0f}% ｜ 净量价强度 {qd['net_strength']:+.0f}")
                    for q in sigs: st.write(q)
                with tab_expect:
                    if reli:
                        st.write(f"5日原始胜率 **{reli['raw']*100:.1f}%** ｜ 贝叶斯 **{reli['bayes']*100:.1f}%** ｜ 保守胜率 **{reli['conservative']*100:.1f}%**")
                        st.write(f"3日上涨率 **{sim['win3']*100:.1f}%**")
                        st.caption("固定5日为主验证窗口，3日只评价启动速度；不根据哪一天表现最好临时挑周期。")
                        st.write(f"95%区间 {reli['lo']*100:.1f}%–{reli['hi']*100:.1f}% ｜ 可信度 {reli['confidence']:.0f}/100")
                    if sim:
                        st.write(f"**统一计划买入区 ¥{price_plan['buy_lo']:.2f}–¥{price_plan['buy_hi']:.2f} ｜ 计算买入价 ¥{entry:.2f}**")
                        st.caption("计算买入价固定取首页建议买入区中值；首页与本页不再使用两套买入逻辑。")
                        st.write(f"第一卖出区 **¥{price_plan['sell1_lo']:.2f}–¥{price_plan['sell1_hi']:.2f}** ｜ 计算目标价 **¥{target:.2f}**：**+{plan_up*100:.2f}%**")
                        st.write(f"第二卖出区 **¥{price_plan['sell2_lo']:.2f}–¥{price_plan['sell2_hi']:.2f}** ｜ 中值 **¥{target2:.2f}**")
                        st.write(f"买入 **¥{entry:.2f}** → 止损 **¥{stop:.2f}**：**-{plan_down*100:.2f}%**")
                        st.write(f"计划盈亏比 **{rr:.2f}:1** ｜ 毛期望 **{plan_ev*100:+.2f}%** ｜ 摩擦后期望 **{net_plan_ev*100:+.2f}%**")
                        st.write(f"模拟本金 ¥{capital:,.0f} ｜ 毛统计期望约 {expected_yuan:+,.0f} 元 ｜ 摩擦后约 {net_expected_yuan:+,.0f} 元")
                with tab_news:
                    if n.empty:
                        st.warning("消息接口不可用，本次消息按中性处理。")
                    else:
                        tc=next((q for q in n.columns if "标题" in str(q) or str(q).lower()=="title"),n.columns[0])
                        for t in n[tc].head(8): st.write("• "+str(t))
                with tab_all:
                    st.write("### 筹码成本 / 上方压力 / 反弹空间")
                    if chip:
                        a,b,c=st.columns(3)
                        a.metric("上方筹码压力",f"{chip_press}/100" if chip_press is not None else "N/A")
                        b.metric("主筹码峰",f"¥{chip['peak']:.2f}")
                        c.metric("估算获利盘",f"{chip['profit']*100:.0f}%")
                        st.write(f"估算平均成本 **¥{chip['mean']:.2f}** ｜ 70%成本区约 **¥{chip['c70lo']:.2f}–¥{chip['c70hi']:.2f}**")
                        st.write(f"当前价上方12%范围内估算套牢/待解套筹码约 **{chip['overhead']*100:.0f}%**")
                        current=float(x.iloc[-1]["收盘"])
                        z1=chip["zone1"]; z2=chip["zone2"]
                        if z1[1] > current:
                            st.write(f"第一上方兑现/解套压力区 **¥{max(current,z1[0]):.2f}–¥{z1[1]:.2f}**")
                        else:
                            st.write("第一兑现区：**当前价下方的历史成本区已不作为上方压力显示**")
                        if z2[1] > current:
                            st.write(f"较强兑现/解套风险参考区 **¥{max(current,z2[0]):.2f}–¥{z2[1]:.2f}**")
                        st.caption(f"筹码模式：{chip['mode']}｜估算精度：{chip['quality']}。这是基于公开成交/换手数据的成本分布估算，不代表真实账户持仓，也不能确定主力会在某个价格出货。")
                    else:
                        st.warning("筹码样本不足，本次不让筹码项参与总分。")

                    st.write("### 支撑 / 压力")
                    st.write(f"第一支撑 **¥{s1:.2f}** ｜ 第二支撑 **¥{s2:.2f}** ｜ 第一压力 **¥{r1:.2f}** ｜ 第二压力 **¥{r2:.2f}**")
                    st.write("### 趋势阶段")
                    st.markdown(f'<div class="box"><div class="big">{stage}</div>{stage_desc}</div>',unsafe_allow_html=True)
                    st.write(f"当前收盘 **¥{td['close']:.2f}** ｜ MA20 **¥{td['ma20']:.2f}** ｜ MA20近3日斜率 **{td['slope20']*100:+.2f}%**")
                    st.write(f"MA5短期斜率 **{td['slope5']*100:+.2f}%** ｜ RSI **{td['rsi']:.1f}**")
                    for name,ok in tchecks.items():
                        st.write(("✅ " if ok else "❌ ")+name)
                    if not tchecks["收盘站上MA20"]:
                        st.caption(f"距离重新站上MA20约 {(td['ma20']/td['close']-1)*100:.2f}%")
                    elif not tchecks["MA20走平/向上"]:
                        st.caption("价格虽在MA20附近/上方，但MA20仍向下，暂不视为完整回踩结构。")

                    st.write("### 买卖点")
                    if conflict or stale:
                        st.write("**数据校验未通过，不生成有效交易建议。**")
                    elif pull:
                        st.write(f"回踩候选 **¥{lo:.2f}–¥{hi:.2f}**")
                    elif stage.startswith("🟠") or stage.startswith("🟡"):
                        st.write("当前属于止跌/筑底阶段，**不生成机械回踩买点**；等待趋势确认。")
                    else:
                        st.write("趋势尚未满足回踩策略条件。")
                    st.write(f"第一压力突破观察 **¥{b1:.2f}** ｜ 结构突破确认 **¥{b2:.2f}**")
                    st.write(f"统一计划：买入 **¥{price_plan['buy_lo']:.2f}–¥{price_plan['buy_hi']:.2f}** ｜ 第一卖出 **¥{price_plan['sell1_lo']:.2f}–¥{price_plan['sell1_hi']:.2f}** ｜ 第二卖出 **¥{price_plan['sell2_lo']:.2f}–¥{price_plan['sell2_hi']:.2f}** ｜ 止损 **¥{price_plan['stop']:.2f}**")
                    st.caption("第一压力突破=短线初步转强；结构突破=突破20日结构高点/更高压力。原始技术目标仍参与统一价格计划计算，但不再作为另一套独立卖出价展示。")
                    st.write("### 量价 / K线真实性")
                    a,b,c=st.columns(3)
                    a.metric("20日量比",f"{qd['vr20']:.2f}×")
                    b.metric("承接强度",f"{qd['wick_score']:.0f}/100")
                    c.metric("抛压强度",f"{qd['pressure_score']:.0f}/100")

                    a,b=st.columns(2)
                    with a:
                        st.write("**下影 / 承接**")
                        st.write(f"下影占比 **{qd['lower']*100:.0f}%**")
                        st.write(f"判断：**{qd['wick_label']}**")
                        if qd["near_support"]:
                            st.write(f"距技术支撑 **{qd['support_dist']:.2f} ATR**")
                    with b:
                        st.write("**上影 / 抛压**")
                        st.write(f"上影占比 **{qd['upper']*100:.0f}%**")
                        st.write(f"判断：**{qd['pressure_label']}**")
                        if qd["near_resistance"]:
                            st.write(f"距技术压力 **{qd['resistance_dist']:.2f} ATR**")

                    net=qd["net_strength"]
                    if net>=30:
                        net_label="🟢 承接明显强于抛压"
                    elif net<=-30:
                        net_label="🔴 抛压明显强于承接"
                    else:
                        net_label="🟡 承接与抛压暂未形成明显优势"
                    st.write(f"**净量价强度：{net:+.0f} → {net_label}**")
                    st.write(f"20日区间位置 **{qd['pos20']*100:.0f}%** ｜ 60日区间位置 **{qd['pos60']*100:.0f}%** ｜ 收盘位置 **{qd['close_pos']*100:.0f}%**")

                    for q in sigs: st.write(q)

                    if qd["pressure_score"]>=70:
                        st.warning("上影抛压较强：如果下一交易日跌破该K线低点或继续放量下跌，可视为抛压进一步确认；若快速收复上影区域，则本次抛压信号减弱。")
                    elif qd["probe"]:
                        st.info("该上影位于相对低位，模型识别为“试盘可能”。需要后续放量突破上影高点才能确认转强，不能仅凭上影判断出货。")

                    st.caption("承接与抛压均为0–100技术强度评分，不等同于主力资金身份判断。长上/下影只是形态，只有结合位置、支撑/压力、量能和收盘结构才升级为有效信号。")
                    st.write("### 胜率真实性")
                    if reli:
                        a,b,c=st.columns(3)
                        a.metric("原始胜率",f"{reli['raw']*100:.1f}%")
                        b.metric("贝叶斯修正",f"{reli['bayes']*100:.1f}%")
                        c.metric("保守胜率",f"{reli['conservative']*100:.1f}%")
                        st.write(f"95% Wilson区间 **{reli['lo']*100:.1f}%–{reli['hi']*100:.1f}%** ｜ 独立案例 **{reli['n']}** ｜ 可信度 **{reli['confidence']:.0f}/100**")
                        tr=reli["train"];te=reli["test"]
                        if tr:
                            st.write(f"早期70%样本：{tr['n']}次 ｜ 胜率 **{tr['win']*100:.1f}%** ｜ 平均5日 **{tr['avg']*100:+.2f}%**")
                        if te:
                            st.write(f"后期30%样本外：{te['n']}次 ｜ 胜率 **{te['win']*100:.1f}%** ｜ 平均5日 **{te['avg']*100:+.2f}%**")
                            if te["n"]<12:
                                st.warning("样本外案例不足12次，本次不把样本外胜率作为主要交易胜率。")
                            elif tr and te["avg"]<=0:
                                st.warning("样本外平均收益不为正：历史规律可能不稳定，需降低交易可信度。")
                            elif tr and abs(tr["win"]-te["win"])>.15:
                                st.warning("训练期与样本外胜率差异超过15个百分点，存在明显不稳定/过拟合风险。")
                        st.caption("贝叶斯修正用于压低小样本虚高胜率；Wilson下限用于保守估计。样本外验证按历史时间顺序留出后30%案例，不使用未来数据反推该段结果。")
                    else:
                        st.warning("独立相似案例不足，无法建立胜率真实性统计。")

                    st.write("### 交易期望 / 是否值得参与")
                    if not sim:
                        st.warning("相似样本不足，暂不计算投资期望。")
                    elif conflict or stale:
                        st.error("数据校验未通过，本次不输出“可投资”判断。")
                    else:
                        # Gate: expectancy alone is not enough
                        if sev:
                            invest="⛔ 暂不参与"
                            reason="存在严重消息风险"
                        elif not reli or reli["n"]<35:
                            invest="🟡 观察"
                            reason="独立历史样本不足，胜率可信度有限"
                        elif reli["enough_oos"] and reli["test"]["avg"]<=0:
                            invest="🔴 不值得做"
                            reason="样本外平均收益不为正"
                        elif np.isfinite(net_plan_ev) and net_plan_ev<=0:
                            invest="🔴 不值得做"
                            reason="扣除交易摩擦后的计划期望为负"
                        elif (np.isfinite(net_plan_ev) and net_plan_ev>=0.004 and rr>=1.5
                              and total>=65 and opportunity["score"]>=70
                              and reli["confidence"]>=55
                              and (not reli["enough_oos"] or reli["test"]["win"]>=.50)):
                            invest="🟢 值得考虑"
                            reason="当前机会、股票质量、盈亏比、净期望和胜率可信度同时通过"
                        else:
                            invest="🟡 观察"
                            reason="存在统计优势，但尚未同时通过全部交易门槛"
                        st.markdown(f'<div class="box"><div class="big">{invest}</div>{reason}</div>',unsafe_allow_html=True)
                        a,b,c=st.columns(3)
                        a.metric("5日收涨概率",f"{wr*100:.1f}%")
                        b.metric("计划盈亏比",f"{rr:.2f}:1" if np.isfinite(rr) else "—")
                        c.metric("计划期望",f"{plan_ev*100:+.2f}%")
                        st.write(f"模拟本金 **¥{capital:,.0f}** ｜ 计划入场参考 **¥{entry:.2f}**")
                        st.write(f"若到目标 **¥{target:.2f}**：约 **+¥{win_yuan:,.0f}**（{plan_up*100:+.2f}%）")
                        st.write(f"若触发止损 **¥{stop:.2f}**：约 **-¥{loss_yuan:,.0f}**（-{plan_down*100:.2f}%）")
                        st.write(f"本次采用 **{wr_source} {wr*100:.1f}%** 计算交易计划。")
                        st.write(f"毛统计期望约 **{expected_yuan:+,.0f} 元** ｜ 预留0.15%交易摩擦后约 **{net_expected_yuan:+,.0f} 元**")
                        st.caption("计算逻辑：胜率×计划盈利幅度 − 败率×计划亏损幅度 − 交易摩擦。统计期望只描述大量同类交易的历史统计优势，不保证本次盈利。")
                        st.write("**相似样本真实5日表现**")
                        st.write(f"上涨样本平均 **{sim['avg_win']*100:+.2f}%** ｜ 下跌样本平均 **{sim['avg_loss']*100:.2f}%** ｜ 全样本平均 **{sim['avg']*100:+.2f}%**")
                        st.write(f"25%分位 **{sim['q25']*100:+.2f}%** ｜ 中位数 **{sim['q50']*100:+.2f}%** ｜ 75%分位 **{sim['q75']*100:+.2f}%**")
                        st.caption(f"若直接按历史5日平均收益折算，¥{capital:,.0f} 的统计期望约 {hist_ev_yuan:+,.0f} 元。未计佣金、滑点及实际成交偏差。")

                    st.write("### 历史K线形态相似")
                    if sim:
                        st.write(f"比较最近 **{sim['lookback']}根K线**：价格路径、成交量路径、MA5/10/20结构、RSI/MACD变化、实体/上下影与波动率。")
                        st.write(f"独立相似案例 **{sim['n']}次** ｜ 案例间至少间隔 **{sim['min_gap']}个交易日** ｜ 形态接近度参考 **{sim['similarity']:.1f}/100**")
                        upn=round(sim["win"]*sim["n"]);downn=sim["n"]-upn
                        a,b,c=st.columns(3)
                        a.metric("5日上涨",f"{upn}/{sim['n']}")
                        b.metric("5日上涨率",f"{sim['win']*100:.1f}%")
                        c.metric("5日平均",f"{sim['avg']*100:+.2f}%")
                        st.write(f"上涨案例平均 **{sim['avg_win']*100:+.2f}%** ｜ 下跌案例平均 **{sim['avg_loss']*100:.2f}%**")
                        st.write(f"25%分位 **{sim['q25']*100:+.2f}%** ｜ 中位数 **{sim['q50']*100:+.2f}%** ｜ 75%分位 **{sim['q75']*100:+.2f}%**")
                        st.write(f"3日摸到+3%：**{sim['p33']*100:.1f}%** ｜ 3日摸到+5%：**{sim['p35']*100:.1f}%** ｜ 5日摸到+5%：**{sim['p55']*100:.1f}%**")
                        with st.expander("查看最相似的历史案例"):
                            for q in sim["cases"]:
                                st.write(f"{q['date'].date()} ｜ 5日收盘 {q['r5']*100:+.2f}% ｜ 5日最高 {q['r5max']*100:+.2f}% ｜ 最大回撤 {q['dd']*100:.2f}%")
                        st.caption("形态接近度是模型内部距离的可读化指标，不代表上涨概率。历史案例仅用于统计研究。")
                    else:
                        st.warning("独立历史形态样本不足，暂不输出历史胜率。")
                    st.write("### 最新公开消息")
                    if n.empty:st.warning("消息接口不可用，消息按中性50。")
                    else:
                        tc=next((q for q in n.columns if "标题" in str(q) or str(q).lower()=="title"),n.columns[0])
                        for t in n[tc].head(8):st.write("• "+str(t))
                    st.line_chart(x.tail(80).set_index("日期")[["收盘","MA5","MA10","MA20","MA30","MA60"]])
                    st.warning("V6.3.4使用公开行情接口，不是券商交易接口。实时数据和换手率估算可能存在延迟/口径差异；数据冲突时自动暂停信号。")

            except Exception as e:st.error("计算异常："+str(e))
