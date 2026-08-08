import streamlit as st
import pandas as pd, numpy as np, akshare as ak
from datetime import datetime,timedelta
import requests,time,random,re

st.set_page_config(page_title="A股短线模型 V5.4",page_icon="📈",layout="centered")
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
        r5=float(f5.iloc[-1]["收盘"]/b-1)
        rec.append({
            "idx":j,"dist":dist,
            "date":pd.Timestamp(x.loc[j,"日期"]),
            "r3max":float(f3["最高"].max()/b-1),
            "r5max":float(f5["最高"].max()/b-1),
            "r5":r5,
            "dd":float(f5["最低"].min()/b-1)
        })
    if len(rec)<20:return None

    rr=np.array([q["r5"] for q in rec],float)
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
        "avg":float(rr.mean()),
        "avg_win":float(wins.mean()) if len(wins) else 0.0,
        "avg_loss":float(losses.mean()) if len(losses) else 0.0,
        "q25":float(q25),"q50":float(q50),"q75":float(q75),
        "dd":float(np.mean([q["dd"] for q in rec])),
        "similarity":simscore,
        "cases":rec[:8],
        "lookback":lookback,
        "min_gap":min_gap
    }

def score(x,sim,n):
    z=x.iloc[-1];c=float(z["收盘"]);t=50;s=50;sig=[]
    t+=8 if c>=z.MA20 else -8;t+=10 if z.MA5>=z.MA10>=z.MA20 else 0;t+=7 if 38<=z.RSI<=68 else (-10 if z.RSI>75 else 0);t+=6 if z.MACDH>0 else 0;t+=5 if z.SLOPE20>0 else -5
    p=x.iloc[-2]
    if z["收盘"]<p["收盘"] and z.VR20<.75:s+=8;sig.append("✓ 缩量回落")
    if z["收盘"]>p["收盘"] and z.VR20>1.35:s+=7;sig.append("✓ 放量上涨")
    if z["收盘"]<p["收盘"] and z.VR20>1.5:s-=10;sig.append("⚠ 放量下跌")
    if z.LOWER>.42:s+=6;sig.append("✓ 长下影承接")
    if z.UPPER>.45:s-=5;sig.append("⚠ 长上影抛压")
    hs=50 if not sim else int(np.clip(50+(sim["win"]-.5)*90+np.clip(sim["avg"]/.025,-1.5,1.5)*22+(sim["p55"]-.30)*18,0,100))
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

st.title("📈 A股短线模型 V5.4")
st.caption("真实K线形态相似 · 独立样本去重 · 交易期望 · 多源行情校验")
code=st.text_input("输入6位A股代码",placeholder="例如：002159",max_chars=6)
capital=st.number_input("模拟投入金额（元）",min_value=1000.0,max_value=10000000.0,value=10000.0,step=1000.0)

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

                # V5.3 交易计划期望：用相似样本胜率 + 当前计划止盈/止损
                entry=(lo+hi)/2 if pull and np.isfinite(lo) and np.isfinite(hi) else close
                target=t1
                stop=sl
                plan_up=max(target/entry-1,0.0) if entry>0 else 0.0
                plan_down=max(1-stop/entry,0.0) if entry>0 else 0.0
                wr=sim["win"] if sim else np.nan
                plan_ev=(wr*plan_up-(1-wr)*plan_down) if sim else np.nan
                rr=(plan_up/plan_down) if plan_down>0 else np.nan
                expected_yuan=capital*plan_ev if np.isfinite(plan_ev) else np.nan
                win_yuan=capital*plan_up
                loss_yuan=capital*plan_down
                hist_ev_yuan=capital*sim["avg"] if sim else np.nan

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
                    elif sim["n"]<50:
                        invest="🟡 观察"
                        reason="历史相似样本不足50"
                    elif plan_ev<=0:
                        invest="🔴 不值得做"
                        reason="按当前止盈/止损计算为负期望"
                    elif plan_ev>=0.005 and rr>=1.5 and total>=68:
                        invest="🟢 值得考虑"
                        reason="期望值、盈亏比和综合评分同时通过"
                    else:
                        invest="🟡 观察"
                        reason="正期望，但优势尚不足"
                    st.markdown(f'<div class="box"><div class="big">{invest}</div>{reason}</div>',unsafe_allow_html=True)
                    a,b,c=st.columns(3)
                    a.metric("5日收涨概率",f"{wr*100:.1f}%")
                    b.metric("计划盈亏比",f"{rr:.2f}:1" if np.isfinite(rr) else "—")
                    c.metric("计划期望",f"{plan_ev*100:+.2f}%")
                    st.write(f"模拟本金 **¥{capital:,.0f}** ｜ 计划入场参考 **¥{entry:.2f}**")
                    st.write(f"若到目标 **¥{target:.2f}**：约 **+¥{win_yuan:,.0f}**（{plan_up*100:+.2f}%）")
                    st.write(f"若触发止损 **¥{stop:.2f}**：约 **-¥{loss_yuan:,.0f}**（-{plan_down*100:.2f}%）")
                    st.write(f"按当前胜率与止盈/止损计算，单次统计期望约 **{expected_yuan:+,.0f} 元**。")
                    st.caption("计算逻辑：胜率×计划盈利幅度 − 败率×计划亏损幅度。它是历史统计期望，不是未来收益保证。")
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
                st.warning("V5.4使用公开行情接口，不是券商交易接口。实时数据和换手率估算可能存在延迟/口径差异；数据冲突时自动暂停信号。")
            except Exception as e:st.error("计算异常："+str(e))
