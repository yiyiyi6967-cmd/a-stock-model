import streamlit as st
import pandas as pd, numpy as np, akshare as ak
from datetime import datetime,timedelta
import time,random

st.set_page_config(page_title="A股短线模型 V5.1",page_icon="📈",layout="centered")
st.markdown("""<style>.block-container{padding-top:1rem;max-width:860px}.box{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;margin:8px 0}.big{font-size:1.3rem;font-weight:700}[data-testid="stMetricValue"]{font-size:1.2rem}</style>""",unsafe_allow_html=True)
POS=["中标","签订","合同","回购","增持","预增","扭亏","分红","重大项目","战略合作","获批","订单","业绩增长"]
NEG=["减持","解禁","立案","调查","处罚","诉讼","亏损","预亏","退市","风险提示","终止","违约","冻结","问询函"];SEV=["立案","调查","处罚","退市","重大诉讼","预亏","风险提示"]

def retry(fn,n=3):
 e=None
 for i in range(n):
  try:return fn()
  except Exception as ex:
   e=ex
   if i<n-1:time.sleep(i+1+random.random())
 raise e

@st.cache_data(ttl=600,show_spinner=False)
def em_hist(code):
 e=datetime.now().strftime("%Y%m%d");s=(datetime.now()-timedelta(days=1600)).strftime("%Y%m%d")
 x=retry(lambda:ak.stock_zh_a_hist(symbol=code,period="daily",start_date=s,end_date=e,adjust="qfq"))
 if x is None or x.empty:raise RuntimeError("东方财富历史行情为空")
 x["日期"]=pd.to_datetime(x["日期"]);return x.sort_values("日期").reset_index(drop=True)

@st.cache_data(ttl=600,show_spinner=False)
def sina_hist(code):
 sym=("sh"+code if code.startswith(("5","6","9")) else "sz"+code)
 x=retry(lambda:ak.stock_zh_a_daily(symbol=sym,adjust="qfq")).reset_index()
 x=x.rename(columns={"date":"日期","open":"开盘","high":"最高","low":"最低","close":"收盘","volume":"成交量"})
 need=["日期","开盘","最高","最低","收盘","成交量"]
 if not all(c in x for c in need):raise RuntimeError("新浪字段异常")
 x["日期"]=pd.to_datetime(x["日期"]);return x[x["日期"]>=pd.Timestamp.now()-pd.Timedelta(days=1600)][need].sort_values("日期").reset_index(drop=True)

@st.cache_data(ttl=120,show_spinner=False)
def em_spot(code):
 # 全市场快照包含换手率；失败不拖死历史分析
 x=retry(lambda:ak.stock_zh_a_spot_em(),2)
 row=x[x["代码"].astype(str).str.zfill(6)==code]
 if row.empty:raise RuntimeError("实时快照未找到代码")
 r=row.iloc[0]
 def num(k):
  try:return float(pd.to_numeric(r.get(k,np.nan),errors="coerce"))
  except:return np.nan
 return {"price":num("最新价"),"turn":num("换手率"),"pct":num("涨跌幅"),"amount":num("成交额"),"time":"当前公开快照"}

@st.cache_data(ttl=600,show_spinner=False)
def em_turn_hist(code):
 # 单独再取一次未复权历史字段，仅用于换手率序列；避免备用K线缺换手率
 e=datetime.now().strftime("%Y%m%d");s=(datetime.now()-timedelta(days=120)).strftime("%Y%m%d")
 x=retry(lambda:ak.stock_zh_a_hist(symbol=code,period="daily",start_date=s,end_date=e,adjust=""),2)
 if x is None or x.empty or "换手率" not in x.columns:raise RuntimeError("历史换手率不可用")
 x["日期"]=pd.to_datetime(x["日期"]);x["换手率"]=pd.to_numeric(x["换手率"],errors="coerce")
 return x[["日期","换手率"]].dropna().sort_values("日期")

@st.cache_data(ttl=300,show_spinner=False)
def get_news(code):
 try:
  x=retry(lambda:ak.stock_news_em(symbol=code),2);return (x.head(30) if x is not None else pd.DataFrame()),None
 except Exception as e:return pd.DataFrame(),str(e)

def get_hist(code):
 errs=[]
 try:return em_hist(code),"东方财富",errs
 except Exception as e:errs.append(str(e))
 try:return sina_hist(code),"新浪备用",errs
 except Exception as e:errs.append(str(e))
 return None,None,errs

def rsi(s,n=14):
 d=s.diff();u=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean();dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
 return 100-100/(1+u/dn.replace(0,np.nan))

def feat(x):
 x=x.copy();c=x["收盘"].astype(float);h=x["最高"].astype(float);l=x["最低"].astype(float);o=x["开盘"].astype(float);v=x["成交量"].astype(float)
 for n in [5,10,20,30,60]:x[f"MA{n}"]=c.rolling(n).mean()
 x["SLOPE20"]=x.MA20/x.MA20.shift(3)-1;x["RSI"]=rsi(c)
 e12=c.ewm(span=12,adjust=False).mean();e26=c.ewm(span=26,adjust=False).mean();x["MACDH"]=(e12-e26)-(e12-e26).ewm(span=9,adjust=False).mean()
 p=c.shift();tr=pd.concat([(h-l).abs(),(h-p).abs(),(l-p).abs()],axis=1).max(axis=1);x["ATR"]=tr.rolling(14).mean()
 x["VOL20"]=v.rolling(20).mean();x["VR20"]=v/x.VOL20;rng=(h-l).replace(0,np.nan);x["LOWER"]=(np.minimum(o,c)-l)/rng;x["UPPER"]=(h-np.maximum(o,c))/rng
 x["HIGH20"]=h.rolling(20).max();x["LOW20"]=l.rolling(20).min();x["HIGH60"]=h.rolling(60).max();x["LOW60"]=l.rolling(60).min();x["POS20"]=(c-x.LOW20)/(x.HIGH20-x.LOW20).replace(0,np.nan)
 return x

def similar(x):
 idx=len(x)-1
 if idx<180:return None
 cur=x.iloc[idx];h=x.iloc[:idx-5].dropna(subset=["MA20","RSI","VR20","MACDH","ATR","POS20"]).copy();cc=h["收盘"].astype(float)
 d=abs((cc/h.MA20-1)-(cur["收盘"]/cur.MA20-1))/.025+abs(h.RSI-cur.RSI)/18+abs(h.VR20-cur.VR20)/.8+abs((h.MACDH/h.ATR.replace(0,np.nan))-(cur.MACDH/cur.ATR))/.7+abs(h.POS20-cur.POS20)/.35
 cand=h.assign(_d=d).replace([np.inf,-np.inf],np.nan).dropna(subset=["_d"]).nsmallest(80,"_d");rec=[]
 for j in cand.index:
  if j+5>=len(x):continue
  b=float(x.loc[j,"收盘"]);f3=x.iloc[j+1:j+4];f5=x.iloc[j+1:j+6];rec.append([f3["最高"].max()/b-1,f3["最高"].max()/b-1>=.05,f5["最高"].max()/b-1,f5.iloc[-1]["收盘"]/b-1,f5["最低"].min()/b-1])
 if len(rec)<30:return None
 r=np.array(rec,float);return {"n":len(r),"p33":(r[:,0]>=.03).mean(),"p35":r[:,1].mean(),"p55":(r[:,2]>=.05).mean(),"win":(r[:,3]>0).mean(),"avg":r[:,3].mean(),"dd":r[:,4].mean()}

def scores(x,sim,n):
 z=x.iloc[-1];c=float(z["收盘"]);tech=50
 tech+=8 if c>=z.MA20 else -8;tech+=10 if z.MA5>=z.MA10>=z.MA20 else 0;tech+=7 if 38<=z.RSI<=68 else (-10 if z.RSI>75 else 0);tech+=6 if z.MACDH>0 else 0;tech+=5 if z.SLOPE20>0 else -5
 struct=50;sig=[]
 if z["收盘"]<x.iloc[-2]["收盘"] and z.VR20<.75:struct+=8;sig.append("✓ 缩量回落")
 if z["收盘"]>x.iloc[-2]["收盘"] and z.VR20>1.35:struct+=7;sig.append("✓ 放量上涨")
 if z["收盘"]<x.iloc[-2]["收盘"] and z.VR20>1.5:struct-=10;sig.append("⚠ 放量下跌")
 if z.LOWER>.42:struct+=6;sig.append("✓ 长下影承接")
 if z.UPPER>.45:struct-=5;sig.append("⚠ 长上影抛压")
 hs=50 if not sim else int(np.clip(50+(sim["win"]-.5)*60+np.clip(sim["avg"]/.03,-1,1)*20+(sim["p33"]-.4)*25+(sim["p55"]-.3)*20,0,100))
 ns=50;sev=False
 if not n.empty:
  tc=next((q for q in n.columns if "标题" in str(q) or str(q).lower()=="title"),n.columns[0])
  for i,t in enumerate(n[tc].astype(str)):
   w=max(.25,1-i/35);ns+=min(6,2*sum(k in t for k in POS))*w;ns-=min(9,3*sum(k in t for k in NEG))*w
   if any(k in t for k in SEV):sev=True
 return int(np.clip(tech,0,100)),int(np.clip(struct,0,100)),hs,int(np.clip(ns,0,100)),sev,sig

def levels(x):
 z=x.iloc[-1];c=float(z["收盘"]);a=max(float(z.ATR),c*.008);mas=[float(z.MA5),float(z.MA10),float(z.MA20),float(z.MA30),float(z.MA60)]
 sp=sorted(set(round(q,4) for q in mas+[float(z.LOW20),float(z.LOW60)] if np.isfinite(q) and q<c),reverse=True);rp=sorted(set(round(q,4) for q in mas+[float(z.HIGH20),float(z.HIGH60)] if np.isfinite(q) and q>c))
 s1=sp[0] if sp else c-a;s2=sp[1] if len(sp)>1 else s1-a;r1=rp[0] if rp else c+a;r2=rp[1] if len(rp)>1 else r1+a
 pull=c>=z.MA20 and z.SLOPE20>=0
 if pull:
  center=max(s1,c-.65*a);lo=max(c-a,center-.2*a);hi=min(c+.08*a,center+.2*a)
  if lo>hi:lo,hi=hi,lo
 else:lo=hi=np.nan
 return s1,s2,r1,r2,lo,hi,max(r1,float(z.HIGH20)*.995),(s1-.8*a if pull else c-1.25*a),max(r1,c+1.5*a),max(r2,c+2.4*a),pull

st.title("📈 A股短线模型 V5.1")
st.caption("历史K线 + 独立实时快照 + 独立换手率 + 数据一致性校验")
code=st.text_input("输入6位A股代码",placeholder="例如：002159",max_chars=6)
if st.button("开始分析",type="primary",use_container_width=True):
 if not(code.isdigit() and len(code)==6):st.error("请输入正确6位代码")
 else:
  raw,src,errs=get_hist(code)
  if raw is None:st.error("历史行情源均失败");st.code("\n".join(errs))
  else:
   try:
    x=feat(raw).reset_index(drop=True);z=x.iloc[-1];last=pd.Timestamp(z["日期"]);close=float(z["收盘"])
    # 独立实时快照
    spot=None;spoterr=None
    try:spot=em_spot(code)
    except Exception as e:spoterr=str(e)
    # 独立历史换手率
    th=None;terr=None
    try:th=em_turn_hist(code)
    except Exception as e:terr=str(e)
    # 一致性：快照与最后K线并非盘中必须相等，因此盘中用合理阈值；差异>12%视作明显冲突
    diff=np.nan
    conflict=False
    if spot and np.isfinite(spot["price"]) and close>0:
     diff=abs(spot["price"]/close-1);conflict=diff>.12
    stale=(pd.Timestamp.now().normalize()-last.normalize()).days>5
    n,nerr=get_news(code);sim=similar(x);ts,ps,hs,ns,sev,sigs=scores(x,sim,n)
    total=round(ts*.25+ps*.25+hs*.30+ns*.20)
    if sim and sim["avg"]<=0:total=min(total,64)
    if sim and sim["win"]<.5:total=min(total,66)
    if sev:total=min(total,50)
    s1,s2,r1,r2,lo,hi,bo,sl,t1,t2,pull=levels(x)

    if conflict:act="🔴 数据冲突：暂停交易分析"
    elif stale:act="⚠️ 历史行情滞后：暂停信号"
    elif sev:act="🔴 消息风险：暂停买点"
    elif close<z.MA20 or z.SLOPE20<0:act="🟡 趋势未确认：等待"
    elif pull and lo<=close<=hi and total>=70:act="🟢 回踩候选"
    elif close>=bo*.995 and z.VR20>=1.2 and total>=72:act="🟢 突破候选"
    else:act="🟡 等待/观察"

    st.write("### 数据可信度")
    if conflict:st.error("🔴 低：实时快照与历史K线出现异常大差异")
    elif spot:st.success("🟢 较高：历史K线与独立实时快照均获取成功")
    else:st.warning("🟡 中等：历史K线正常，但实时快照暂不可用")
    st.write(f"历史源 **{src}** ｜ K线日期 **{last.date()}** ｜ K线收盘 **¥{close:.2f}**")
    if spot:
     st.write(f"独立快照 **¥{spot['price']:.2f}** ｜ 涨跌幅 {spot['pct']:.2f}%"+(f" ｜ 与K线差异 {diff*100:.2f}%" if np.isfinite(diff) else ""))
    else:st.caption("实时快照失败："+str(spoterr))

    st.write("### 换手率")
    if spot and np.isfinite(spot["turn"]):
     text=f"当前换手率 **{spot['turn']:.2f}%**"
     if th is not None and len(th):
      m5=th["换手率"].tail(5).mean();m20=th["换手率"].tail(20).mean();ratio=spot["turn"]/m20 if m20 else np.nan
      text+=f" ｜ 5日均值 {m5:.2f}% ｜ 20日均值 {m20:.2f}%"
      if np.isfinite(ratio):text+=f" ｜ 当前/20日 **{ratio:.2f}×**"
     st.write(text)
    elif th is not None and len(th):
     st.write(f"最新历史换手率 **{th.iloc[-1]['换手率']:.2f}%** ｜ 20日均值 {th['换手率'].tail(20).mean():.2f}%")
    else:st.warning("换手率独立接口当前不可用；不会伪造数据。")

    st.markdown(f'<div class="box"><div class="big">{act}</div>综合评分 {total}/100</div>',unsafe_allow_html=True)
    a,b,c,d=st.columns(4);a.metric("趋势",ts);b.metric("量价",ps);c.metric("历史",hs);d.metric("消息",ns)
    st.write("### 支撑 / 压力")
    st.write(f"第一支撑 **¥{s1:.2f}** ｜ 第二支撑 **¥{s2:.2f}** ｜ 第一压力 **¥{r1:.2f}** ｜ 第二压力 **¥{r2:.2f}**")
    st.write("### 买卖点")
    if conflict or stale:st.write("**数据校验未通过，不生成有效交易建议。**")
    elif pull:st.write(f"回踩候选 **¥{lo:.2f}–¥{hi:.2f}**")
    else:st.write("趋势条件不足，不生成机械回踩买点。")
    st.write(f"突破确认约 **¥{bo:.2f}** ｜ 止损/无效参考 **¥{sl:.2f}** ｜ 目标1 ¥{t1:.2f} ｜ 目标2 ¥{t2:.2f}")
    st.write("### 量价 / K线")
    for q in sigs:st.write("• "+q)
    st.write("### 历史盈利能力")
    if sim:
     a,b,c=st.columns(3);a.metric("3日+3%",f"{sim['p33']*100:.1f}%");b.metric("3日+5%",f"{sim['p35']*100:.1f}%");c.metric("5日+5%",f"{sim['p55']*100:.1f}%")
     st.caption(f"样本 {sim['n']}｜5日收涨 {sim['win']*100:.1f}%｜平均5日 {sim['avg']*100:+.2f}%｜平均最低回撤 {sim['dd']*100:.2f}%")
    st.write("### 最新公开消息")
    if n.empty:st.warning("消息接口不可用，消息按中性50。")
    else:
     tc=next((q for q in n.columns if "标题" in str(q) or str(q).lower()=="title"),n.columns[0])
     for t in n[tc].head(8):st.write("• "+str(t))
    st.line_chart(x.tail(80).set_index("日期")[["收盘","MA5","MA10","MA20","MA30","MA60"]])
    st.warning("V5.1不抓取券商App，也不声称交易所级实时。免费公开接口可能延迟；当数据冲突或明显滞后时模型会暂停信号。")
   except Exception as e:st.error("计算异常："+str(e))
