import streamlit as st
import pandas as pd, numpy as np, akshare as ak
from datetime import datetime,timedelta
import requests,time,random,re

st.set_page_config(page_title="A股短线模型 V5.2",page_icon="📈",layout="centered")
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

def similar(x):
    idx=len(x)-1
    if idx<180:return None
    cur=x.iloc[idx];h=x.iloc[:idx-5].dropna(subset=["MA20","RSI","VR20","MACDH","ATR","POS20"]).copy();cc=h["收盘"].astype(float)
    d=abs((cc/h.MA20-1)-(cur["收盘"]/cur.MA20-1))/.025+abs(h.RSI-cur.RSI)/18+abs(h.VR20-cur.VR20)/.8+abs((h.MACDH/h.ATR.replace(0,np.nan))-(cur.MACDH/cur.ATR))/.7+abs(h.POS20-cur.POS20)/.35
    cand=h.assign(_d=d).replace([np.inf,-np.inf],np.nan).dropna(subset=["_d"]).nsmallest(80,"_d");rec=[]
    for j in cand.index:
        if j+5>=len(x):continue
        b=float(x.loc[j,"收盘"]);f3=x.iloc[j+1:j+4];f5=x.iloc[j+1:j+6]
        rec.append([f3["最高"].max()/b-1,f5["最高"].max()/b-1,f5.iloc[-1]["收盘"]/b-1,f5["最低"].min()/b-1])
    if len(rec)<30:return None
    r=np.array(rec)
    return {"n":len(r),"p33":(r[:,0]>=.03).mean(),"p35":(r[:,0]>=.05).mean(),"p55":(r[:,1]>=.05).mean(),"win":(r[:,2]>0).mean(),"avg":r[:,2].mean(),"dd":r[:,3].mean()}

def score(x,sim,n):
    z=x.iloc[-1];c=float(z["收盘"]);t=50;s=50;sig=[]
    t+=8 if c>=z.MA20 else -8;t+=10 if z.MA5>=z.MA10>=z.MA20 else 0;t+=7 if 38<=z.RSI<=68 else (-10 if z.RSI>75 else 0);t+=6 if z.MACDH>0 else 0;t+=5 if z.SLOPE20>0 else -5
    p=x.iloc[-2]
    if z["收盘"]<p["收盘"] and z.VR20<.75:s+=8;sig.append("✓ 缩量回落")
    if z["收盘"]>p["收盘"] and z.VR20>1.35:s+=7;sig.append("✓ 放量上涨")
    if z["收盘"]<p["收盘"] and z.VR20>1.5:s-=10;sig.append("⚠ 放量下跌")
    if z.LOWER>.42:s+=6;sig.append("✓ 长下影承接")
    if z.UPPER>.45:s-=5;sig.append("⚠ 长上影抛压")
    hs=50 if not sim else int(np.clip(50+(sim["win"]-.5)*60+np.clip(sim["avg"]/.03,-1,1)*20+(sim["p33"]-.4)*25+(sim["p55"]-.3)*20,0,100))
    ns=50;sev=False
    if not n.empty:
        tc=next((q for q in n.columns if "标题" in str(q) or str(q).lower()=="title"),n.columns[0])
        for i,txt in enumerate(n[tc].astype(str)):
            w=max(.25,1-i/35);ns+=min(6,2*sum(k in txt for k in POS))*w;ns-=min(9,3*sum(k in txt for k in NEG))*w
            if any(k in txt for k in SEV):sev=True
    return int(np.clip(t,0,100)),int(np.clip(s,0,100)),hs,int(np.clip(ns,0,100)),sev,sig

def levels(x):
    z=x.iloc[-1];c=float(z["收盘"]);a=max(float(z.ATR),c*.008);mas=[float(z[f"MA{n}"]) for n in [5,10,20,30,60]]
    sp=sorted(set(q for q in mas+[float(z.LOW20),float(z.LOW60)] if np.isfinite(q) and q<c),reverse=True);rp=sorted(set(q for q in mas+[float(z.HIGH20),float(z.HIGH60)] if np.isfinite(q) and q>c))
    s1=sp[0] if sp else c-a;s2=sp[1] if len(sp)>1 else s1-a;r1=rp[0] if rp else c+a;r2=rp[1] if len(rp)>1 else r1+a
    pull=c>=z.MA20 and z.SLOPE20>=0
    if pull:
        center=max(s1,c-.65*a);lo=max(c-a,center-.2*a);hi=min(c+.08*a,center+.2*a)
        if lo>hi:lo,hi=hi,lo
    else:lo=hi=np.nan
    return s1,s2,r1,r2,lo,hi,max(r1,float(z.HIGH20)*.995),(s1-.8*a if pull else c-1.25*a),max(r1,c+1.5*a),max(r2,c+2.4*a),pull

st.title("📈 A股短线模型 V5.2")
st.caption("双实时源校验 · 换手率双通道 · 不再拉取全市场实时列表")
code=st.text_input("输入6位A股代码",placeholder="例如：002159",max_chars=6)

if st.button("开始分析",type="primary",use_container_width=True):
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

                n,nerr=get_news(code);sim=similar(x);ts,ps,hs,ns,sev,sigs=score(x,sim,n)
                total=round(ts*.25+ps*.25+hs*.30+ns*.20)
                if sim and sim["avg"]<=0:total=min(total,64)
                if sim and sim["win"]<.5:total=min(total,66)
                if sev:total=min(total,50)
                s1,s2,r1,r2,lo,hi,bo,sl,t1,t2,pull=levels(x)

                if conflict:act="🔴 双实时源冲突：暂停交易分析"
                elif stale:act="⚠️ 历史行情明显滞后：暂停信号"
                elif sev:act="🔴 消息风险：暂停买点"
                elif close<z.MA20 or z.SLOPE20<0:act="🟡 趋势未确认：等待"
                elif pull and lo<=close<=hi and total>=70:act="🟢 回踩候选"
                elif close>=bo*.995 and z.VR20>=1.2 and total>=72:act="🟢 突破候选"
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
                a,b,c,d=st.columns(4);a.metric("趋势",ts);b.metric("量价",ps);c.metric("历史",hs);d.metric("消息",ns)
                st.write("### 支撑 / 压力")
                st.write(f"第一支撑 **¥{s1:.2f}** ｜ 第二支撑 **¥{s2:.2f}** ｜ 第一压力 **¥{r1:.2f}** ｜ 第二压力 **¥{r2:.2f}**")
                st.write("### 买卖点")
                if conflict or stale:st.write("**数据校验未通过，不生成有效交易建议。**")
                elif pull:st.write(f"回踩候选 **¥{lo:.2f}–¥{hi:.2f}**")
                else:st.write("趋势条件不足，不生成机械回踩买点。")
                st.write(f"突破确认约 **¥{bo:.2f}** ｜ 止损/无效参考 **¥{sl:.2f}** ｜ 目标1 ¥{t1:.2f} ｜ 目标2 ¥{t2:.2f}")
                st.write("### 量价 / K线")
                if sigs:
                    for q in sigs:st.write("• "+q)
                else:st.write("• 暂无突出结构")
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
                st.warning("V5.2使用公开行情接口，不是券商交易接口。实时数据和换手率估算可能存在延迟/口径差异；数据冲突时自动暂停信号。")
            except Exception as e:st.error("计算异常："+str(e))
