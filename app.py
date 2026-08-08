import streamlit as st
import pandas as pd, numpy as np, akshare as ak
from datetime import datetime,timedelta

st.set_page_config(page_title="A股短线模型 V4",page_icon="📈",layout="centered")
st.markdown("""<style>.block-container{padding-top:1rem;max-width:820px}.box{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;margin:8px 0}.big{font-size:1.3rem;font-weight:700}[data-testid="stMetricValue"]{font-size:1.4rem}</style>""",unsafe_allow_html=True)

POS=["中标","签订","合同","回购","增持","预增","扭亏","分红","重大项目","战略合作","获批","订单","业绩增长"]
NEG=["减持","解禁","立案","调查","处罚","诉讼","亏损","预亏","退市","风险提示","终止","违约","冻结","质押风险","问询函"]
SEVERE=["立案","调查","处罚","退市","重大诉讼","预亏","风险提示"]

def rsi(s,n=14):
 d=s.diff();u=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean();dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
 return 100-100/(1+u/dn.replace(0,np.nan))

@st.cache_data(ttl=600,show_spinner=False)
def hist(code,years=4):
 e=datetime.now().strftime("%Y%m%d");s=(datetime.now()-timedelta(days=365*years+100)).strftime("%Y%m%d")
 x=ak.stock_zh_a_hist(symbol=code,period="daily",start_date=s,end_date=e,adjust="qfq")
 if x is None or x.empty:return None
 x["日期"]=pd.to_datetime(x["日期"]);return x.sort_values("日期").reset_index(drop=True)

@st.cache_data(ttl=300,show_spinner=False)
def news(code):
 try:
  n=ak.stock_news_em(symbol=code)
  if n is None or n.empty:return pd.DataFrame()
  return n.head(30)
 except:return pd.DataFrame()

def feat(x):
 x=x.copy();c=x["收盘"].astype(float);h=x["最高"].astype(float);l=x["最低"].astype(float);o=x["开盘"].astype(float);v=x["成交量"].astype(float)
 for n in [5,10,20,30,60]:x[f"MA{n}"]=c.rolling(n).mean()
 x["RSI"]=rsi(c);e12=c.ewm(span=12,adjust=False).mean();e26=c.ewm(span=26,adjust=False).mean();x["DIF"]=e12-e26;x["DEA"]=x["DIF"].ewm(span=9,adjust=False).mean();x["MACDH"]=x["DIF"]-x["DEA"]
 p=c.shift();tr=pd.concat([(h-l).abs(),(h-p).abs(),(l-p).abs()],axis=1).max(axis=1);x["ATR"]=tr.rolling(14).mean()
 x["VR5"]=v/v.rolling(5).mean();rng=(h-l).replace(0,np.nan);x["LOWER"]=(np.minimum(o,c)-l)/rng;x["HIGH20"]=h.rolling(20).max();x["LOW20"]=l.rolling(20).min();x["POS20"]=(c-x["LOW20"])/(x["HIGH20"]-x["LOW20"]).replace(0,np.nan)
 return x

def tech(z):
 c=float(z["收盘"]);s=50;why=[]
 if c>=z.MA20:s+=8;why.append("站上MA20")
 else:s-=8;why.append("位于MA20下方")
 if z.MA5>=z.MA10>=z.MA20:s+=10;why.append("均线偏多")
 if .55<=z.VR5<=1.45:s+=7;why.append("量能未过热")
 elif z.VR5>2:s-=7;why.append("明显放量，注意分歧")
 if 38<=z.RSI<=68:s+=7;why.append("RSI健康")
 elif z.RSI>75:s-=10;why.append("RSI过热")
 if z.MACDH>0:s+=6;why.append("MACD动能偏多")
 if z.LOWER>.35:s+=6;why.append("下影承接")
 return int(np.clip(s,0,100)),why

def levels(z):
 c=float(z["收盘"]);a=max(float(z.ATR),c*.008);sup=max([p for p in [float(z.MA10),float(z.MA20),c-1.2*a] if p<c*1.02])
 lo=max(sup,c-.8*a);hi=min(c+.15*a,lo+.55*a);bo=max(float(z.HIGH20),c+.7*a);sl=min(lo-.9*a,c-1.45*a);t1=max(c+1.7*a,hi+(hi-sl)*1.6);t2=max(c+2.8*a,hi+(hi-sl)*2.4)
 return sup,lo,hi,bo,sl,t1,t2,(t1-hi)/(hi-sl)

def news_score(n):
 if n.empty:return 50,False,[],[]
 # 兼容不同字段名
 titlecol=next((c for c in n.columns if "标题" in str(c) or str(c).lower()=="title"),n.columns[0])
 texts=n[titlecol].astype(str).tolist();score=50;pos=[];neg=[];severe=False
 for i,t in enumerate(texts):
  w=max(.25,1-i/35)
  pp=[k for k in POS if k in t];nn=[k for k in NEG if k in t]
  if pp:score+=min(6,2*len(pp))*w;pos.append(t)
  if nn:score-=min(9,3*len(nn))*w;neg.append(t)
  if any(k in t for k in SEVERE):severe=True
 return int(np.clip(score,0,100)),severe,pos[:5],neg[:5]

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
 return len(r),(r[:,0]>=.03).mean(),(r[:,0]>=.05).mean(),(r[:,1]>=.05).mean(),(r[:,2]>0).mean(),r[:,2].mean(),r[:,3].mean()

st.title("📈 A股短线模型 V4")
st.caption("技术面 + 最新公开消息 + 买卖点 + 历史盈利统计")
code=st.text_input("输入6位A股代码",placeholder="例如：000001",max_chars=6)
if st.button("开始分析",type="primary",use_container_width=True):
 if not(code.isdigit() and len(code)==6):st.error("请输入正确的6位代码")
 else:
  try:
   with st.spinner("获取行情与最新公开消息…"):
    x=feat(hist(code));z=x.iloc[-1];ts,why=tech(z);n=news(code);ns,severe,pos,neg=news_score(n);sim=similar(x)
    sup,lo,hi,bo,sl,t1,t2,rr=levels(z);combined=round(ts*.75+ns*.25)
    if severe:combined=min(combined,55)
    c=float(z["收盘"])
    if severe:act="🔴 消息风险：暂停技术买点"
    elif c<=sl:act="🔴 风险/止损区"
    elif combined>=72 and lo<=c<=hi and z.VR5<=1.5:act="🟢 回踩买点候选"
    elif combined>=75 and c>=bo*.995 and z.VR5>=1.2:act="🟢 突破买点候选"
    elif c<lo:act="🟡 等企稳"
    else:act="🟡 等待/观察"
   st.markdown(f'<div class="box"><div class="big">{act}</div>综合评分 {combined}/100</div>',unsafe_allow_html=True)
   a,b,c1=st.columns(3);a.metric("技术",f"{ts}/100");b.metric("消息",f"{ns}/100");c1.metric("综合",f"{combined}/100")
   st.write("### 买卖点")
   st.write(f"回踩买区 **¥{lo:.2f}–¥{hi:.2f}** ｜ 突破确认约 **¥{bo:.2f}**")
   st.write(f"支撑 ¥{sup:.2f} ｜ **止损/无效 ¥{sl:.2f}** ｜ 目标1 ¥{t1:.2f} ｜ 目标2 ¥{t2:.2f} ｜ 盈亏比 {rr:.2f}")
   st.write("### 历史盈利统计")
   if sim:
    N,p33,p35,p55,w5,av5,dd=sim;a,b,c2=st.columns(3);a.metric("3日摸到+3%",f"{p33*100:.1f}%");b.metric("3日摸到+5%",f"{p35*100:.1f}%");c2.metric("5日摸到+5%",f"{p55*100:.1f}%")
    st.caption(f"历史相似样本 {N} 个｜5日收涨率 {w5*100:.1f}%｜平均5日收益 {av5*100:+.2f}%｜平均5日最低回撤 {dd*100:.2f}%")
   else:st.info("相似样本不足，暂不显示概率。")
   st.write("### 最新消息面")
   if n.empty:st.info("当前公开新闻接口未返回数据；消息分保持中性，不冒充实时完整公告。")
   else:
    if severe:st.error("检测到风险关键词。请打开原始消息核实；关键词识别可能误判。")
    titlecol=next((cc for cc in n.columns if "标题" in str(cc) or str(cc).lower()=="title"),n.columns[0])
    datecol=next((cc for cc in n.columns if "时间" in str(cc) or "日期" in str(cc)),None)
    for _,r in n.head(10).iterrows():
     prefix=f"{r[datecol]} · " if datecol else ""
     st.write("• "+prefix+str(r[titlecol]))
   st.write("### 技术信号")
   for q in why:st.write("• "+q)
   st.line_chart(x.tail(80).set_index("日期")[["收盘","MA5","MA10","MA20","MA30"]])
   st.warning("V4不是交易所级实时资讯终端。新闻来自公开接口，可能延迟、遗漏或误分类；重大消息必须核对交易所公告/公司原文。历史概率是相似技术状态统计，不代表未来真实胜率。")
  except Exception as e:st.error(f"分析失败：{e}")
