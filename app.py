import streamlit as st
import pandas as pd
import numpy as np
import akshare as ak
from datetime import datetime, timedelta

st.set_page_config(page_title="A股短线模型 V2", page_icon="📈", layout="centered")
st.markdown("""
<style>
.block-container{padding-top:1.2rem;max-width:760px}
[data-testid="stMetricValue"]{font-size:1.55rem}
.card{border:1px solid rgba(128,128,128,.25);border-radius:16px;padding:14px;margin:8px 0}
.buy{font-size:1.25rem;font-weight:700}
.small{opacity:.75;font-size:.9rem}
</style>""", unsafe_allow_html=True)

def rsi(s,n=14):
    d=s.diff()
    up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean()
    dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+up/dn.replace(0,np.nan))

@st.cache_data(ttl=900, show_spinner=False)
def hist(code):
    end=datetime.now().strftime("%Y%m%d")
    start=(datetime.now()-timedelta(days=300)).strftime("%Y%m%d")
    x=ak.stock_zh_a_hist(symbol=code,period="daily",start_date=start,end_date=end,adjust="qfq")
    if x is None or x.empty: return None
    x["日期"]=pd.to_datetime(x["日期"])
    return x

def analyze(x):
    x=x.copy()
    c=x["收盘"].astype(float); h=x["最高"].astype(float); l=x["最低"].astype(float)
    o=x["开盘"].astype(float); v=x["成交量"].astype(float)
    for n in [5,10,20,30]: x[f"MA{n}"]=c.rolling(n).mean()
    x["RSI"]=rsi(c)
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean()
    dif=ema12-ema26; dea=dif.ewm(span=9,adjust=False).mean()
    x["MACD"]=dif-dea
    prev=c.shift()
    tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    x["ATR"]=tr.rolling(14).mean()
    x["VR5"]=v/v.rolling(5).mean()
    rng=(h-l).replace(0,np.nan)
    x["LOWER"]=(np.minimum(o,c)-l)/rng
    x["HIGH20"]=h.rolling(20).max()
    x["LOW20"]=l.rolling(20).min()
    z=x.iloc[-1]
    close=float(z["收盘"]); atr=float(z["ATR"])
    ma10=float(z["MA10"]); ma20=float(z["MA20"])
    support=max([p for p in [ma10,ma20,close-1.2*atr] if p < close*1.02])
    entry_lo=max(support,close-.8*atr)
    entry_hi=min(close+.2*atr,entry_lo+.6*atr)
    stop=min(entry_lo-atr,close-1.5*atr)
    target=max(close+2*atr,entry_hi+(entry_hi-stop)*1.8)

    score=50
    reasons=[]
    if close>=z["MA20"]: score+=8; reasons.append("价格位于MA20上方")
    else: score-=7; reasons.append("价格仍在MA20下方")
    if z["MA5"]>=z["MA10"]: score+=7; reasons.append("短期均线偏强")
    if .55<=z["VR5"]<=1.5: score+=7; reasons.append("成交量未明显过热")
    elif z["VR5"]>2: score-=6; reasons.append("成交量明显放大，注意分歧")
    if 38<=z["RSI"]<=68: score+=7; reasons.append("RSI处于相对健康区域")
    elif z["RSI"]>75: score-=8; reasons.append("RSI偏高，追涨风险增加")
    if z["MACD"]>0: score+=6; reasons.append("MACD动能偏多")
    if z["LOWER"]>.35: score+=5; reasons.append("当日存在较明显下影承接")
    dist_high=close/float(z["HIGH20"])-1
    if dist_high>-0.02: score+=5; reasons.append("接近20日高位")
    score=int(np.clip(score,0,100))
    rr=(target-entry_hi)/(entry_hi-stop) if entry_hi>stop else np.nan
    return z,score,reasons,support,entry_lo,entry_hi,stop,target,rr

st.title("📈 A股短线模型 V2")
st.caption("手机网页 · 量价/均线/动能/支撑压力分析")

code=st.text_input("输入6位股票代码", placeholder="例如：000001", max_chars=6)
go=st.button("开始分析", type="primary", use_container_width=True)

if go:
    if not(code.isdigit() and len(code)==6):
        st.error("请输入正确的6位A股代码。")
    else:
        with st.spinner("正在获取行情并分析…"):
            try:
                x=hist(code)
                if x is None or len(x)<40: raise ValueError("行情不足")
                z,score,reasons,support,elo,ehi,stop,target,rr=analyze(x)
                st.subheader(f"{code} · {z['日期'].date()}")
                a,b,c=st.columns(3)
                a.metric("收盘",f"{z['收盘']:.2f}")
                b.metric("综合评分",f"{score}/100")
                c.metric("量比(5日)",f"{z['VR5']:.2f}")
                st.markdown(f"""<div class="card">
                <div class="buy">参考入场：{elo:.2f} – {ehi:.2f}</div>
                <div>支撑：<b>{support:.2f}</b>　止损：<b>{stop:.2f}</b></div>
                <div>第一目标：<b>{target:.2f}</b>　盈亏比：<b>{rr:.2f}</b></div>
                </div>""",unsafe_allow_html=True)
                st.write("**信号解释**")
                for r in reasons: st.write("• "+r)
                st.write("**最近60个交易日**")
                chart=x.tail(60).set_index("日期")[["收盘","MA5","MA10","MA20","MA30"]]
                st.line_chart(chart)
                st.caption("研究工具，不构成投资建议。支撑、止损和目标位是基于历史波动率的参考值，不代表必然成交或盈利。")
            except Exception as e:
                st.error(f"暂时无法分析：{e}。公开行情接口偶尔会波动，请稍后重试。")

with st.expander("模型规则"):
    st.write("V2手机轻量版使用 MA5/10/20/30、5日量比、RSI14、MACD、ATR、下影线和20日高低点生成结构评分，并用ATR估算入场、止损和第一目标。完整版机器学习扫描模型仍建议在服务器定时训练/运行。")
