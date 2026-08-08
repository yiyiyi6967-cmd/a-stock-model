import streamlit as st
import pandas as pd, numpy as np, akshare as ak
from datetime import datetime,timedelta
import time, random

st.set_page_config(page_title="A股短线模型 V4.2",page_icon="📈",layout="centered")
st.markdown("""<style>.block-container{padding-top:1rem;max-width:820px}.box{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;margin:8px 0}.big{font-size:1.3rem;font-weight:700}[data-testid="stMetricValue"]{font-size:1.35rem}</style>""",unsafe_allow_html=True)

POS=["中标","签订","合同","回购","增持","预增","扭亏","分红","重大项目","战略合作","获批","订单","业绩增长"]
NEG=["减持","解禁","立案","调查","处罚","诉讼","亏损","预亏","退市","风险提示","终止","违约","冻结","质押风险","问询函"]
SEVERE=["立案","调查","处罚","退市","重大诉讼","预亏","风险提示"]

def retry(fn,n=3):
 e=None
 for i in range(n):
  try:return fn()
  except Exception as ex:
   e=ex
   if i<n-1:time.sleep(i+1+random.random())
 raise e

@st.cache_data(ttl=600,show_spinner=False)
def primary(code):
 end=datetime.now().strftime("%Y%m%d");start=(datetime.now()-timedelta(days=1600)).strftime("%Y%m%d")
 x=retry(lambda:ak.stock_zh_a_hist(symbol=code,period="daily",start_date=start,end_date=end,adjust="qfq"))
 if x is None or x.empty:raise RuntimeError("空数据")
 x["日期"]=pd.to_datetime(x["日期"]);return x.sort_values("日期").reset_index(drop=True)

@st.cache_data(ttl=600,show_spinner=False)
def backup(code):
 sym=("sh"+code if code.startswith(("5","6","9")) else "sz"+code)
 x=retry(lambda:ak.stock_zh_a_daily(symbol=sym,adjust="qfq")).reset_index()
 x=x.rename(columns={"date":"日期","open":"开盘","high":"最高","low":"最低","close":"收盘","volume":"成交量"})
 need=["日期","开盘","最高","最低","收盘","成交量"]
 if not all(c in x for c in need):raise RuntimeError("字段异常")
 x["日期"]=pd.to_datetime(x["日期"])
 return x[x["日期"]>=pd.Timestamp.now()-pd.Timedelta(days=1600)][need].sort_values("日期").reset_index(drop=True)

def get_hist(code):
 errs=[]
 for fn,name in [(primary,"东方财富"),(backup,"新浪备用")]:
  try:return fn(code),name,errs
  except Exception as e:errs.append(f"{name}: {e}")
 return None,None,errs

@st.cache_data(ttl=300,show_spinner=False)
def get_news(code):
 try:
  n=retry(lambda:ak.stock_news_em(symbol=code),2)
  return (n.head(30) if n is not None else pd.DataFrame()),None
 except Exception as e:return pd.DataFrame(),str(e)

def rsi(s,n=14):
 d=s.diff();u=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean();dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
 return 100-100/(1+u/dn.replace(0,np.nan))

def feat(x):
 x=x.copy();c=x["收盘"].astype(float);h=x["最高"].astype(float);l=x["最低"].astype(float);o=x["开盘"].astype(float);v=x["成交量"].astype(float)
 for n in [5,10,20,30,60]:x[f"MA{n}"]=c.rolling(n).mean()
 x["RSI"]=rsi(c);e12=c.ewm(span=12,adjust=False).mean();e26=c.ewm(span=26,adjust=False).mean();x["DIF"]=e12-e26;x["DEA"]=x.DIF.ewm(span=9,adjust=False).mean();x["MACDH"]=x.DIF-x.DEA
 p=c.shift();tr=pd.concat([(h-l).abs(),(h-p).abs(),(l-p).abs()],axis=1).max(axis=1);x["ATR"]=tr.rolling(14).mean()
 x["VR5"]=v/v.rolling(5).mean();rng=(h-l).replace(0,np.nan);x["LOWER"]=(np.minimum(o,c)-l)/rng
 x["HIGH20"]=h.rolling(20).max();x["LOW20"]=l.rolling(20).min();x["POS20"]=(c-x.LOW20)/(x.HIGH20-x.LOW20).replace(0,np.nan)
 return x

def tech(z):
 c=float(z["收盘"]);s=50;why=[]
 if c>=z.MA20:s+=8;why.append("站上MA20")
 else:s-=8;why.append("跌破MA20，MA20转为潜在压力")
 if z.MA5>=z.MA10>=z.MA20:s+=10;why.append("均线多头")
 if .55<=z.VR5<=1.45:s+=7;why.append("量能未过热")
 elif z.VR5>2:s-=7;why.append("明显放量分歧")
 if 38<=z.RSI<=68:s+=7;why.append("RSI健康")
 elif z.RSI>75:s-=10;why.append("RSI过热")
 if z.MACDH>0:s+=6;why.append("MACD动能偏多")
 if z.LOWER>.35:s+=6;why.append("下影承接")
 return int(np.clip(s,0,100)),why

def similar(x):
 idx=len(x)-1
 if idx<180:return None
 cur=x.iloc[idx];h=x.iloc[:idx-5].dropna(subset=["MA20","RSI","VR5","MACDH","ATR","POS20"]).copy()
 if len(h)<80:return None
 cc=h["收盘"].astype(float)
 d=abs((cc/h.MA20-1)-(cur["收盘"]/cur.MA20-1))/.025+abs(h.RSI-cur.RSI)/18+abs(h.VR5-cur.VR5)/.8+abs((h.MACDH/h.ATR.replace(0,np.nan))-(cur.MACDH/cur.ATR))/.7+abs(h.POS20-cur.POS20)/.35
 cand=h.assign(_d=d).replace([np.inf,-np.inf],np.nan).dropna(subset=["_d"]).nsmallest(80,"_d")
 rec=[]
 for j in cand.index:
  if j+5>=len(x):continue
  b=float(x.loc[j,"收盘"]);f3=x.iloc[j+1:j+4];f5=x.iloc[j+1:j+6]
  rec.append([f3["最高"].max()/b-1,f5["最高"].max()/b-1,f5.iloc[-1]["收盘"]/b-1,f5["最低"].min()/b-1])
 if len(rec)<30:return None
 r=np.array(rec)
 return {"n":len(r),"p33":(r[:,0]>=.03).mean(),"p35":(r[:,0]>=.05).mean(),"p55":(r[:,1]>=.05).mean(),"win5":(r[:,2]>0).mean(),"avg5":r[:,2].mean(),"dd":r[:,3].mean()}

def hist_score(s):
 if not s:return 50
 score=50
 score+=(s["win5"]-.5)*60
 score+=np.clip(s["avg5"]/.03,-1,1)*20
 score+=(s["p33"]-.4)*25
 score+=(s["p55"]-.3)*20
 return int(np.clip(score,0,100))

def nscore(n):
 if n.empty:return 50,False
 tc=next((c for c in n.columns if "标题" in str(c) or str(c).lower()=="title"),n.columns[0]);s=50;sev=False
 for i,t in enumerate(n[tc].astype(str)):
  w=max(.25,1-i/35);s+=min(6,2*sum(k in t for k in POS))*w;s-=min(9,3*sum(k in t for k in NEG))*w
  if any(k in t for k in SEVERE):sev=True
 return int(np.clip(s,0,100)),sev

def levels(z):
 c=float(z["收盘"]);a=max(float(z.ATR),c*.008)
 mas=[float(z.MA5),float(z.MA10),float(z.MA20),float(z.MA30)]
 supports=[m for m in mas if np.isfinite(m) and m<=c]
 resist=[m for m in mas if np.isfinite(m) and m>c]
 support=max(supports) if supports else max(float(z.LOW20),c-1.2*a)
 resistance=min(resist) if resist else max(float(z.HIGH20),c+.8*a)
 # 回踩买区只在价格仍站在至少一个短中期均线上时生成
 valid_pullback=bool(supports)
 if valid_pullback:
  center=max(support,c-.65*a);lo=min(center-.18*a,center+.18*a);hi=max(center-.18*a,center+.18*a)
  lo=max(lo,c-1.0*a);hi=min(hi,c+.10*a)
  if lo>hi:lo,hi=hi,lo
 else:lo=hi=np.nan
 breakout=max(resistance,float(z.HIGH20)*.995)
 sl=(support-.8*a) if valid_pullback else c-1.25*a
 t1=max(c+1.5*a,breakout+.7*a);t2=max(c+2.5*a,breakout+1.5*a)
 rr=(t1-hi)/(hi-sl) if valid_pullback and hi>sl else np.nan
 return support,resistance,lo,hi,breakout,sl,t1,t2,rr,valid_pullback

st.title("📈 A股短线模型 V4.2")
st.caption("修正支撑/压力 · 盈利能力参与评分 · 数据日期检查")
code=st.text_input("输入6位A股代码",placeholder="例如：600958",max_chars=6)
if st.button("开始分析",type="primary",use_container_width=True):
 if not(code.isdigit() and len(code)==6):st.error("请输入正确6位代码")
 else:
  raw,src,errs=get_hist(code)
  if raw is None:
   st.error("行情源均不可用");st.code("\n".join(errs))
  elif len(raw)<200:st.error("历史数据不足")
  else:
   try:
    x=feat(raw).reset_index(drop=True);z=x.iloc[-1];last=pd.Timestamp(z["日期"])
    # 周末/节假日不能简单要求今天有K线；只在数据超过5个自然日时判明显滞后
    age=(pd.Timestamp.now().normalize()-last.normalize()).days
    stale=age>5
    ts,why=tech(z);sim=similar(x);hs=hist_score(sim);n,nerr=get_news(code);ns,sev=nscore(n)
    sup,res,lo,hi,bo,sl,t1,t2,rr,pull=levels(z)
    combined=round(ts*.50+hs*.30+ns*.20)
    if sim and sim["avg5"]<=0:combined=min(combined,64)
    if sim and sim["win5"]<.50:combined=min(combined,66)
    if sev:combined=min(combined,50)
    c=float(z["收盘"])
    if stale:act="⚠️ 行情明显滞后：暂停生成交易信号"
    elif sev:act="🔴 消息风险：暂停技术买点"
    elif c<z.MA20:act="🟡 弱势等待：先重新站回关键均线"
    elif pull and lo<=c<=hi and combined>=70:act="🟢 回踩买点候选"
    elif c>=bo*.995 and z.VR5>=1.2 and combined>=72:act="🟢 突破买点候选"
    elif c<=sl:act="🔴 风险/无效区"
    else:act="🟡 等待/观察"

    st.success(f"数据源：{src}｜行情日期：{last.date()}｜模型收盘：¥{c:.2f}")
    if errs:st.caption("主接口异常时已自动切换备用源。")
    if stale:st.error("行情数据超过5个自然日未更新，本次价格可能滞后，因此不应据此交易。")
    st.markdown(f'<div class="box"><div class="big">{act}</div>综合评分 {combined}/100</div>',unsafe_allow_html=True)
    a,b,cx,d=st.columns(4);a.metric("技术",ts);b.metric("历史盈利",hs);cx.metric("消息",ns);d.metric("综合",combined)

    st.write("### 支撑 / 压力")
    st.write(f"参考支撑 **¥{sup:.2f}** ｜ 近期压力 **¥{res:.2f}**")
    st.write("### 买卖点")
    if pull:st.write(f"回踩候选区 **¥{lo:.2f}–¥{hi:.2f}**")
    else:st.write("**当前价格位于主要均线下方，不生成机械“回踩买点”。先观察重新站回关键均线。**")
    st.write(f"突破确认约 **¥{bo:.2f}** ｜ 止损/无效参考 **¥{sl:.2f}**")
    st.write(f"目标1 ¥{t1:.2f} ｜ 目标2 ¥{t2:.2f}"+(f" ｜ 盈亏比 {rr:.2f}" if np.isfinite(rr) else ""))

    st.write("### 历史盈利能力")
    if sim:
     a,b,cx=st.columns(3);a.metric("3日摸到+3%",f"{sim['p33']*100:.1f}%");b.metric("3日摸到+5%",f"{sim['p35']*100:.1f}%");cx.metric("5日摸到+5%",f"{sim['p55']*100:.1f}%")
     st.caption(f"相似样本 {sim['n']}｜5日收涨率 {sim['win5']*100:.1f}%｜平均5日收益 {sim['avg5']*100:+.2f}%｜平均5日最低回撤 {sim['dd']*100:.2f}%")
     if sim["avg5"]<=0:st.warning("历史相似状态平均5日收益≤0，综合评分已设置上限。")
     if sim["win5"]<.5:st.warning("历史相似状态5日收涨率<50%，综合评分已受到限制。")
    else:st.info("相似样本不足。")

    st.write("### 最新公开消息")
    if n.empty:st.warning("消息接口不可用，消息评分按中性50；不影响技术计算。")
    else:
     if sev:st.error("检测到风险关键词，请核对公告原文。")
     tc=next((q for q in n.columns if "标题" in str(q) or str(q).lower()=="title"),n.columns[0])
     dc=next((q for q in n.columns if "时间" in str(q) or "日期" in str(q)),None)
     for _,r in n.head(8).iterrows():st.write("• "+(f"{r[dc]} · " if dc else "")+str(r[tc]))

    st.write("### 技术信号")
    for q in why:st.write("• "+q)
    st.line_chart(x.tail(80).set_index("日期")[["收盘","MA5","MA10","MA20","MA30"]])
    st.warning("买卖点是技术模型参考，不是确定价格预测。历史相似样本统计不代表未来收益；免费行情和消息源可能延迟。")
   except Exception as e:st.error("计算异常："+str(e))
