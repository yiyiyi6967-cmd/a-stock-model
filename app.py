import streamlit as st
import pandas as pd, numpy as np, akshare as ak
from datetime import datetime,timedelta
import time,random

st.set_page_config(page_title="A股短线模型 V5",page_icon="📈",layout="centered")
st.markdown("""<style>.block-container{padding-top:1rem;max-width:850px}.box{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;margin:8px 0}.big{font-size:1.3rem;font-weight:700}[data-testid="stMetricValue"]{font-size:1.25rem}</style>""",unsafe_allow_html=True)

POS=["中标","签订","合同","回购","增持","预增","扭亏","分红","重大项目","战略合作","获批","订单","业绩增长"]
NEG=["减持","解禁","立案","调查","处罚","诉讼","亏损","预亏","退市","风险提示","终止","违约","冻结","问询函"]
SEV=["立案","调查","处罚","退市","重大诉讼","预亏","风险提示"]

def retry(fn,n=3):
 e=None
 for i in range(n):
  try:return fn()
  except Exception as ex:
   e=ex
   if i<n-1:time.sleep(i+1+random.random())
 raise e

@st.cache_data(ttl=600,show_spinner=False)
def hist1(code):
 e=datetime.now().strftime("%Y%m%d");s=(datetime.now()-timedelta(days=1600)).strftime("%Y%m%d")
 x=retry(lambda:ak.stock_zh_a_hist(symbol=code,period="daily",start_date=s,end_date=e,adjust="qfq"))
 if x is None or x.empty:raise RuntimeError("空数据")
 x["日期"]=pd.to_datetime(x["日期"]);return x.sort_values("日期").reset_index(drop=True)

@st.cache_data(ttl=600,show_spinner=False)
def hist2(code):
 sym=("sh"+code if code.startswith(("5","6","9")) else "sz"+code)
 x=retry(lambda:ak.stock_zh_a_daily(symbol=sym,adjust="qfq")).reset_index()
 x=x.rename(columns={"date":"日期","open":"开盘","high":"最高","low":"最低","close":"收盘","volume":"成交量"})
 need=["日期","开盘","最高","最低","收盘","成交量"]
 if not all(c in x for c in need):raise RuntimeError("字段异常")
 x["日期"]=pd.to_datetime(x["日期"]);return x[x["日期"]>=pd.Timestamp.now()-pd.Timedelta(days=1600)][need].sort_values("日期").reset_index(drop=True)

def gethist(code):
 errs=[]
 for fn,name in [(hist1,"东方财富"),(hist2,"新浪备用")]:
  try:return fn(code),name,errs
  except Exception as e:errs.append(f"{name}:{e}")
 return None,None,errs

@st.cache_data(ttl=300,show_spinner=False)
def news(code):
 try:
  x=retry(lambda:ak.stock_news_em(symbol=code),2)
  return (x.head(30) if x is not None else pd.DataFrame()),None
 except Exception as e:return pd.DataFrame(),str(e)

def rsi(s,n=14):
 d=s.diff();u=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean();dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
 return 100-100/(1+u/dn.replace(0,np.nan))

def features(x):
 x=x.copy();c=x["收盘"].astype(float);h=x["最高"].astype(float);l=x["最低"].astype(float);o=x["开盘"].astype(float);v=x["成交量"].astype(float)
 for n in [5,10,20,30,60]:
  x[f"MA{n}"]=c.rolling(n).mean()
  if n in [5,10,20]:x[f"SLOPE{n}"]=x[f"MA{n}"]/x[f"MA{n}"].shift(3)-1
 x["RSI"]=rsi(c);e12=c.ewm(span=12,adjust=False).mean();e26=c.ewm(span=26,adjust=False).mean();x["DIF"]=e12-e26;x["DEA"]=x.DIF.ewm(span=9,adjust=False).mean();x["MACDH"]=x.DIF-x.DEA
 p=c.shift();tr=pd.concat([(h-l).abs(),(h-p).abs(),(l-p).abs()],axis=1).max(axis=1);x["ATR"]=tr.rolling(14).mean()
 up=h.diff();dn=-l.diff();plus=np.where((up>dn)&(up>0),up,0);minus=np.where((dn>up)&(dn>0),dn,0)
 atr=x.ATR.replace(0,np.nan);pdi=100*pd.Series(plus,index=x.index).rolling(14).mean()/atr;mdi=100*pd.Series(minus,index=x.index).rolling(14).mean()/atr
 dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan);x["ADX"]=dx.rolling(14).mean()
 x["VOLMA5"]=v.rolling(5).mean();x["VOLMA20"]=v.rolling(20).mean();x["VR5"]=v/x.VOLMA5;x["VR20"]=v/x.VOLMA20
 rng=(h-l).replace(0,np.nan);x["LOWER"]=(np.minimum(o,c)-l)/rng;x["UPPER"]=(h-np.maximum(o,c))/rng;x["BODY"]=(c-o)/o
 x["HIGH20"]=h.rolling(20).max();x["LOW20"]=l.rolling(20).min();x["HIGH60"]=h.rolling(60).max();x["LOW60"]=l.rolling(60).min()
 x["POS20"]=(c-x.LOW20)/(x.HIGH20-x.LOW20).replace(0,np.nan)
 # 换手率：主源若有，备用源可能没有
 if "换手率" in x.columns:x["TURN"]=pd.to_numeric(x["换手率"],errors="coerce")
 else:x["TURN"]=np.nan
 x["TURN20"]=x.TURN.rolling(20).mean()
 return x

def patterns(x):
 z=x.iloc[-1];p=x.iloc[-2];c=float(z["收盘"]);sig=[];score=50
 # 量价结构
 if z["收盘"]<p["收盘"] and z.VR20<.75:score+=7;sig.append("✓ 缩量回落：抛压较轻")
 if z["收盘"]>p["收盘"] and z.VR20>1.35:score+=7;sig.append("✓ 放量上涨")
 if z["收盘"]<p["收盘"] and z.VR20>1.5:score-=10;sig.append("⚠ 放量下跌")
 if abs(z.BODY)<.008 and z.VR20<.8:score+=4;sig.append("✓ 小实体缩量企稳候选")
 if z.LOWER>.42:score+=6;sig.append("✓ 长下影承接")
 if z.UPPER>.45:score-=5;sig.append("⚠ 长上影抛压")
 if c>=p.HIGH20 and z.VR20>1.25:score+=10;sig.append("✓ 放量突破20日高位")
 # 趋势强度
 if z.SLOPE20>0:score+=6;sig.append("✓ MA20斜率向上")
 else:score-=5;sig.append("⚠ MA20斜率向下")
 if z.ADX>=25: sig.append(f"趋势强度ADX {z.ADX:.1f}")
 # 换手
 if np.isfinite(z.TURN) and np.isfinite(z.TURN20):
  ratio=z.TURN/z.TURN20 if z.TURN20 else np.nan
  if ratio>1.6:sig.append("⚠ 换手明显高于20日均值")
  elif ratio<.75:sig.append("✓ 换手低于20日均值")
 return int(np.clip(score,0,100)),sig

def tech(x):
 z=x.iloc[-1];c=float(z["收盘"]);s=50;why=[]
 if c>=z.MA20:s+=8;why.append("站上MA20")
 else:s-=8;why.append("MA20位于价格上方，属于潜在压力")
 if z.MA5>=z.MA10>=z.MA20:s+=10;why.append("均线多头")
 if 38<=z.RSI<=68:s+=7
 elif z.RSI>75:s-=10;why.append("RSI过热")
 if z.MACDH>0:s+=6
 if z.SLOPE20>0:s+=5
 return int(np.clip(s,0,100)),why

def similar(x):
 idx=len(x)-1
 if idx<180:return None
 cur=x.iloc[idx];h=x.iloc[:idx-5].dropna(subset=["MA20","RSI","VR5","MACDH","ATR","POS20"]).copy()
 cc=h["收盘"].astype(float)
 d=abs((cc/h.MA20-1)-(cur["收盘"]/cur.MA20-1))/.025+abs(h.RSI-cur.RSI)/18+abs(h.VR5-cur.VR5)/.8+abs((h.MACDH/h.ATR.replace(0,np.nan))-(cur.MACDH/cur.ATR))/.7+abs(h.POS20-cur.POS20)/.35
 cand=h.assign(_d=d).replace([np.inf,-np.inf],np.nan).dropna(subset=["_d"]).nsmallest(80,"_d");rec=[]
 for j in cand.index:
  if j+5>=len(x):continue
  b=float(x.loc[j,"收盘"]);f3=x.iloc[j+1:j+4];f5=x.iloc[j+1:j+6];rec.append([f3["最高"].max()/b-1,f5["最高"].max()/b-1,f5.iloc[-1]["收盘"]/b-1,f5["最低"].min()/b-1])
 if len(rec)<30:return None
 r=np.array(rec);return {"n":len(r),"p33":(r[:,0]>=.03).mean(),"p35":(r[:,0]>=.05).mean(),"p55":(r[:,1]>=.05).mean(),"win5":(r[:,2]>0).mean(),"avg5":r[:,2].mean(),"dd":r[:,3].mean()}

def hscore(s):
 if not s:return 50
 return int(np.clip(50+(s["win5"]-.5)*60+np.clip(s["avg5"]/.03,-1,1)*20+(s["p33"]-.4)*25+(s["p55"]-.3)*20,0,100))

def nscore(n):
 if n.empty:return 50,False
 tc=next((c for c in n.columns if "标题" in str(c) or str(c).lower()=="title"),n.columns[0]);s=50;sev=False
 for i,t in enumerate(n[tc].astype(str)):
  w=max(.25,1-i/35);s+=min(6,2*sum(k in t for k in POS))*w;s-=min(9,3*sum(k in t for k in NEG))*w
  if any(k in t for k in SEV):sev=True
 return int(np.clip(s,0,100)),sev

def levels(x):
 z=x.iloc[-1];c=float(z["收盘"]);a=max(float(z.ATR),c*.008)
 # 多级支撑压力：均线 + 20/60日低高 + 最近10日局部极值
 mas=[float(z.MA5),float(z.MA10),float(z.MA20),float(z.MA30)]
 lows=[float(z.LOW20),float(z.LOW60),float(x["最低"].tail(10).min())]
 highs=[float(z.HIGH20),float(z.HIGH60),float(x["最高"].tail(10).max())]
 sp=sorted(set(round(q,4) for q in mas+lows if np.isfinite(q) and q<c),reverse=True)
 rp=sorted(set(round(q,4) for q in mas+highs if np.isfinite(q) and q>c))
 s1=sp[0] if sp else c-a;s2=sp[1] if len(sp)>1 else s1-a
 r1=rp[0] if rp else c+a;r2=rp[1] if len(rp)>1 else r1+a
 pull=(c>=z.MA20 and z.SLOPE20>=0)
 if pull:
  center=max(s1,c-.65*a);lo=max(c-a,center-.2*a);hi=min(c+.08*a,center+.2*a)
  if lo>hi:lo,hi=hi,lo
 else:lo=hi=np.nan
 bo=max(r1,float(z.HIGH20)*.995);sl=(s1-.8*a if pull else c-1.25*a);t1=max(r1,c+1.5*a);t2=max(r2,c+2.4*a)
 return s1,s2,r1,r2,lo,hi,bo,sl,t1,t2,pull

st.title("📈 A股短线模型 V5")
st.caption("量价结构 · K线 · 趋势强度 · 换手 · 多级支撑压力 · 历史盈利 · 消息")
code=st.text_input("输入6位A股代码",placeholder="例如：600958",max_chars=6)
if st.button("开始分析",type="primary",use_container_width=True):
 if not(code.isdigit() and len(code)==6):st.error("请输入正确6位代码")
 else:
  raw,src,errs=gethist(code)
  if raw is None:st.error("行情源均不可用");st.code("\n".join(errs))
  elif len(raw)<200:st.error("历史行情不足")
  else:
   try:
    x=features(raw).reset_index(drop=True);z=x.iloc[-1];last=pd.Timestamp(z["日期"]);age=(pd.Timestamp.now().normalize()-last.normalize()).days;stale=age>5
    ts,twhy=tech(x);ps,signals=patterns(x);sim=similar(x);hs=hscore(sim);n,nerr=news(code);ns,sev=nscore(n)
    s1,s2,r1,r2,lo,hi,bo,sl,t1,t2,pull=levels(x)
    # V5：技术基础25 + 结构25 + 历史30 + 消息20
    total=round(ts*.25+ps*.25+hs*.30+ns*.20)
    if sim and sim["avg5"]<=0:total=min(total,64)
    if sim and sim["win5"]<.5:total=min(total,66)
    if sev:total=min(total,50)
    c=float(z["收盘"])
    if stale:act="⚠️ 行情滞后：暂停信号"
    elif sev:act="🔴 消息风险：暂停买点"
    elif c<z.MA20 or z.SLOPE20<0:act="🟡 趋势未确认：等待"
    elif pull and lo<=c<=hi and total>=70 and z.VR20<=1.5:act="🟢 缩量/正常量回踩候选"
    elif c>=bo*.995 and z.VR20>=1.2 and total>=72:act="🟢 放量突破候选"
    elif c<=sl:act="🔴 风险/无效区"
    else:act="🟡 等待/观察"

    st.success(f"数据源 {src}｜行情 {last.date()}｜收盘 ¥{c:.2f}")
    st.markdown(f'<div class="box"><div class="big">{act}</div>综合评分 {total}/100</div>',unsafe_allow_html=True)
    a,b,cx,d=st.columns(4);a.metric("趋势",ts);b.metric("量价结构",ps);cx.metric("历史盈利",hs);d.metric("消息",ns)

    st.write("### 多级支撑 / 压力")
    st.write(f"强/近支撑 **¥{s1:.2f}** ｜ 次支撑 **¥{s2:.2f}**")
    st.write(f"第一压力 **¥{r1:.2f}** ｜ 第二压力 **¥{r2:.2f}**")
    st.write("### 买卖点")
    if pull:st.write(f"回踩候选 **¥{lo:.2f}–¥{hi:.2f}**")
    else:st.write("当前趋势条件不足，**不生成机械回踩买点**；优先等待MA20转强/重新站稳。")
    st.write(f"突破确认约 **¥{bo:.2f}** ｜ 止损/无效参考 **¥{sl:.2f}** ｜ 目标1 ¥{t1:.2f} ｜ 目标2 ¥{t2:.2f}")

    st.write("### 量价 / K线结构")
    if signals:
     for q in signals:st.write("• "+q)
    else:st.write("• 暂无特别突出的量价结构")
    if np.isfinite(z.TURN):st.write(f"• 换手率 {z.TURN:.2f}%｜20日均值 {z.TURN20:.2f}%")
    else:st.caption("当前备用数据源未提供换手率，换手模块自动跳过。")

    st.write("### 历史盈利能力")
    if sim:
     a,b,cx=st.columns(3);a.metric("3日+3%",f"{sim['p33']*100:.1f}%");b.metric("3日+5%",f"{sim['p35']*100:.1f}%");cx.metric("5日+5%",f"{sim['p55']*100:.1f}%")
     st.caption(f"相似样本 {sim['n']}｜5日收涨率 {sim['win5']*100:.1f}%｜平均5日 {sim['avg5']*100:+.2f}%｜平均最低回撤 {sim['dd']*100:.2f}%")
    st.write("### 最新公开消息")
    if n.empty:st.warning("消息接口不可用，消息按中性50，不阻断技术分析。")
    else:
     if sev:st.error("检测到风险关键词，请核对公告原文。")
     tc=next((q for q in n.columns if "标题" in str(q) or str(q).lower()=="title"),n.columns[0])
     for t in n[tc].head(8):st.write("• "+str(t))
    st.write("### 趋势图")
    st.line_chart(x.tail(80).set_index("日期")[["收盘","MA5","MA10","MA20","MA30"]])
    st.warning("V5为短线研究辅助工具。技术信号、历史相似统计和公开新闻均不能保证未来收益；免费数据源可能延迟。")
   except Exception as e:st.error("计算异常："+str(e))
