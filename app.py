import streamlit as st
import pandas as pd
import numpy as np
import akshare as ak
from datetime import datetime, timedelta
import time
import random

st.set_page_config(page_title="A股短线模型 V4.1", page_icon="📈", layout="centered")
st.markdown("""
<style>
.block-container{padding-top:1rem;max-width:820px}
.box{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;margin:8px 0}
.big{font-size:1.3rem;font-weight:700}
[data-testid="stMetricValue"]{font-size:1.4rem}
</style>""", unsafe_allow_html=True)

POS=["中标","签订","合同","回购","增持","预增","扭亏","分红","重大项目","战略合作","获批","订单","业绩增长"]
NEG=["减持","解禁","立案","调查","处罚","诉讼","亏损","预亏","退市","风险提示","终止","违约","冻结","质押风险","问询函"]
SEVERE=["立案","调查","处罚","退市","重大诉讼","预亏","风险提示"]

def retry_call(fn, tries=3):
    last=None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last=e
            if i < tries-1:
                time.sleep(1.0*(i+1)+random.random())
    raise last

def rsi(s,n=14):
    d=s.diff()
    u=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean()
    dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+u/dn.replace(0,np.nan))

@st.cache_data(ttl=600, show_spinner=False)
def hist_primary(code, years=4):
    e=datetime.now().strftime("%Y%m%d")
    s=(datetime.now()-timedelta(days=365*years+100)).strftime("%Y%m%d")
    def call():
        return ak.stock_zh_a_hist(symbol=code,period="daily",start_date=s,end_date=e,adjust="qfq")
    x=retry_call(call,3)
    if x is None or x.empty: raise RuntimeError("主行情接口返回空数据")
    x["日期"]=pd.to_datetime(x["日期"])
    return x.sort_values("日期").reset_index(drop=True)

@st.cache_data(ttl=600, show_spinner=False)
def hist_backup(code, years=4):
    # 新浪日线作为备用；字段转换为主程序统一格式。
    def call():
        return ak.stock_zh_a_daily(symbol=("sh"+code if code.startswith(("5","6","9")) else "sz"+code), adjust="qfq")
    x=retry_call(call,3)
    if x is None or x.empty: raise RuntimeError("备用行情接口返回空数据")
    x=x.reset_index()
    ren={"date":"日期","open":"开盘","high":"最高","low":"最低","close":"收盘","volume":"成交量"}
    x=x.rename(columns=ren)
    need=["日期","开盘","最高","最低","收盘","成交量"]
    if not all(c in x.columns for c in need): raise RuntimeError("备用接口字段异常")
    x["日期"]=pd.to_datetime(x["日期"])
    cutoff=pd.Timestamp.now()-pd.Timedelta(days=365*years+100)
    return x[x["日期"]>=cutoff][need].sort_values("日期").reset_index(drop=True)

def get_hist(code):
    errors=[]
    try:
        return hist_primary(code), "东方财富", errors
    except Exception as e:
        errors.append("主接口："+str(e))
    try:
        return hist_backup(code), "新浪备用", errors
    except Exception as e:
        errors.append("备用接口："+str(e))
    return None, None, errors

@st.cache_data(ttl=300, show_spinner=False)
def get_news(code):
    try:
        def call(): return ak.stock_news_em(symbol=code)
        n=retry_call(call,2)
        if n is None: return pd.DataFrame(), "新闻接口无返回"
        return n.head(30), None
    except Exception as e:
        return pd.DataFrame(), str(e)

def feat(x):
    x=x.copy()
    c=x["收盘"].astype(float);h=x["最高"].astype(float);l=x["最低"].astype(float)
    o=x["开盘"].astype(float);v=x["成交量"].astype(float)
    for n in [5,10,20,30,60]: x[f"MA{n}"]=c.rolling(n).mean()
    x["RSI"]=rsi(c)
    e12=c.ewm(span=12,adjust=False).mean();e26=c.ewm(span=26,adjust=False).mean()
    x["DIF"]=e12-e26;x["DEA"]=x["DIF"].ewm(span=9,adjust=False).mean();x["MACDH"]=x["DIF"]-x["DEA"]
    p=c.shift()
    tr=pd.concat([(h-l).abs(),(h-p).abs(),(l-p).abs()],axis=1).max(axis=1)
    x["ATR"]=tr.rolling(14).mean()
    x["VR5"]=v/v.rolling(5).mean()
    rng=(h-l).replace(0,np.nan)
    x["LOWER"]=(np.minimum(o,c)-l)/rng
    x["HIGH20"]=h.rolling(20).max();x["LOW20"]=l.rolling(20).min()
    x["POS20"]=(c-x["LOW20"])/(x["HIGH20"]-x["LOW20"]).replace(0,np.nan)
    return x

def tech(z):
    c=float(z["收盘"]);s=50;why=[]
    if c>=z.MA20:s+=8;why.append("价格站上MA20")
    else:s-=8;why.append("价格位于MA20下方")
    if z.MA5>=z.MA10>=z.MA20:s+=10;why.append("短中期均线偏多")
    if .55<=z.VR5<=1.45:s+=7;why.append("量能未明显过热")
    elif z.VR5>2:s-=7;why.append("明显放量，注意分歧")
    if 38<=z.RSI<=68:s+=7;why.append("RSI处于健康区域")
    elif z.RSI>75:s-=10;why.append("RSI偏热")
    if z.MACDH>0:s+=6;why.append("MACD动能偏多")
    if z.LOWER>.35:s+=6;why.append("下影线存在承接")
    return int(np.clip(s,0,100)),why

def levels(z):
    c=float(z["收盘"]);a=max(float(z.ATR),c*.008)
    vals=[float(z.MA10),float(z.MA20),c-1.2*a]
    vals=[p for p in vals if np.isfinite(p) and p<c*1.02]
    sup=max(vals) if vals else c-a
    lo=max(sup,c-.8*a);hi=min(c+.15*a,lo+.55*a)
    bo=max(float(z.HIGH20),c+.7*a)
    sl=min(lo-.9*a,c-1.45*a)
    t1=max(c+1.7*a,hi+(hi-sl)*1.6)
    t2=max(c+2.8*a,hi+(hi-sl)*2.4)
    rr=(t1-hi)/(hi-sl) if hi>sl else np.nan
    return sup,lo,hi,bo,sl,t1,t2,rr

def news_score(n):
    if n.empty:return 50,False
    tc=next((c for c in n.columns if "标题" in str(c) or str(c).lower()=="title"),n.columns[0])
    score=50;severe=False
    for i,t in enumerate(n[tc].astype(str).tolist()):
        w=max(.25,1-i/35)
        pp=sum(k in t for k in POS);nn=sum(k in t for k in NEG)
        score+=min(6,2*pp)*w;score-=min(9,3*nn)*w
        if any(k in t for k in SEVERE):severe=True
    return int(np.clip(score,0,100)),severe

def similar(x):
    idx=len(x)-1
    if idx<180:return None
    cur=x.iloc[idx]
    h=x.iloc[:idx-5].dropna(subset=["MA20","RSI","VR5","MACDH","ATR","POS20"]).copy()
    if len(h)<80:return None
    cc=h["收盘"].astype(float)
    d=(abs((cc/h.MA20-1)-(cur["收盘"]/cur.MA20-1))/.025+
       abs(h.RSI-cur.RSI)/18+abs(h.VR5-cur.VR5)/.8+
       abs((h.MACDH/h.ATR.replace(0,np.nan))-(cur.MACDH/cur.ATR))/.7+
       abs(h.POS20-cur.POS20)/.35)
    cand=h.assign(_d=d).replace([np.inf,-np.inf],np.nan).dropna(subset=["_d"]).nsmallest(80,"_d")
    rec=[]
    for j in cand.index:
        if j+5>=len(x):continue
        b=float(x.loc[j,"收盘"]);f3=x.iloc[j+1:j+4];f5=x.iloc[j+1:j+6]
        rec.append([f3["最高"].max()/b-1,f5["最高"].max()/b-1,f5.iloc[-1]["收盘"]/b-1,f5["最低"].min()/b-1])
    if len(rec)<30:return None
    r=np.array(rec)
    return len(r),(r[:,0]>=.03).mean(),(r[:,0]>=.05).mean(),(r[:,1]>=.05).mean(),(r[:,2]>0).mean(),r[:,2].mean(),r[:,3].mean()

st.title("📈 A股短线模型 V4.1")
st.caption("双行情源容错 · 消息独立降级 · 买卖点 · 历史盈利统计")
code=st.text_input("输入6位A股代码",placeholder="例如：600958",max_chars=6)

if st.button("开始分析",type="primary",use_container_width=True):
    if not(code.isdigit() and len(code)==6):
        st.error("请输入正确的6位A股代码")
    else:
        with st.spinner("正在连接行情数据…"):
            raw,source,herrors=get_hist(code)
        if raw is None:
            st.error("两个行情接口都暂时无法连接，因此无法进行技术分析。")
            with st.expander("查看接口错误"):
                for e in herrors: st.code(e)
            st.info("这通常是免费公开接口或云服务器网络限制，不是股票代码错误。稍后重试即可。")
        elif len(raw)<200:
            st.error("可用历史行情不足200个交易日，暂不计算。")
        else:
            try:
                x=feat(raw).reset_index(drop=True);z=x.iloc[-1]
                ts,why=tech(z)
                sup,lo,hi,bo,sl,t1,t2,rr=levels(z)
                sim=similar(x)

                # 新闻完全独立：失败不会阻断技术分析
                with st.spinner("正在获取公开消息…"):
                    n,nerr=get_news(code)
                ns,severe=news_score(n)
                combined=round(ts*.75+ns*.25)
                if severe:combined=min(combined,55)

                c=float(z["收盘"])
                if severe:act="🔴 消息风险：暂停技术买点"
                elif c<=sl:act="🔴 风险/止损区"
                elif combined>=72 and lo<=c<=hi and z.VR5<=1.5:act="🟢 回踩买点候选"
                elif combined>=75 and c>=bo*.995 and z.VR5>=1.2:act="🟢 突破买点候选"
                elif c<lo:act="🟡 等企稳"
                else:act="🟡 等待/观察"

                st.success(f"行情连接成功 · 数据源：{source}")
                if herrors:
                    st.caption("主数据源曾连接失败，系统已自动切换备用源。")

                st.markdown(f'<div class="box"><div class="big">{act}</div>综合评分 {combined}/100</div>',unsafe_allow_html=True)
                a,b,c1=st.columns(3)
                a.metric("技术",f"{ts}/100");b.metric("消息",f"{ns}/100");c1.metric("综合",f"{combined}/100")

                st.write("### 买卖点")
                st.write(f"回踩买区 **¥{lo:.2f}–¥{hi:.2f}** ｜ 突破确认约 **¥{bo:.2f}**")
                st.write(f"支撑 ¥{sup:.2f} ｜ **止损/无效 ¥{sl:.2f}**")
                st.write(f"目标1 ¥{t1:.2f} ｜ 目标2 ¥{t2:.2f} ｜ 盈亏比 {rr:.2f}")

                st.write("### 历史盈利统计")
                if sim:
                    N,p33,p35,p55,w5,av5,dd=sim
                    a,b,c2=st.columns(3)
                    a.metric("3日摸到+3%",f"{p33*100:.1f}%")
                    b.metric("3日摸到+5%",f"{p35*100:.1f}%")
                    c2.metric("5日摸到+5%",f"{p55*100:.1f}%")
                    st.caption(f"历史相似样本 {N} 个｜5日收涨率 {w5*100:.1f}%｜平均5日收益 {av5*100:+.2f}%｜平均5日最低回撤 {dd*100:.2f}%")
                else:
                    st.info("相似历史样本不足，暂不显示概率。")

                st.write("### 最新公开消息")
                if n.empty:
                    st.warning("新闻接口当前不可用，但技术分析已正常完成。消息评分暂按中性50分处理。")
                    if nerr: st.caption("新闻接口错误："+nerr)
                else:
                    if severe:st.error("检测到风险关键词，请核对公告原文；关键词可能误判。")
                    tc=next((cc for cc in n.columns if "标题" in str(cc) or str(cc).lower()=="title"),n.columns[0])
                    dc=next((cc for cc in n.columns if "时间" in str(cc) or "日期" in str(cc)),None)
                    for _,r in n.head(10).iterrows():
                        pre=f"{r[dc]} · " if dc else ""
                        st.write("• "+pre+str(r[tc]))

                st.write("### 技术信号")
                for q in why:st.write("• "+q)
                st.line_chart(x.tail(80).set_index("日期")[["收盘","MA5","MA10","MA20","MA30"]])
                st.warning("免费公开行情/新闻接口可能延迟或中断。V4.1会自动重试并切换备用行情源；新闻失败不会再导致整个分析失败。历史统计不代表未来收益。")
            except Exception as e:
                st.error("行情已获取，但计算过程中出现异常："+str(e))
