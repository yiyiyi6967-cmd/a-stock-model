# A股短线模型 V4.1

针对 Streamlit Cloud 上 RemoteDisconnected 问题的容错更新。

改进：
- 东方财富行情失败自动重试3次
- 自动切换新浪备用日线源
- 新闻接口独立重试
- 新闻失败不再让整个分析失败
- 消息不可用时按中性50分处理并明确提示
- 页面显示当前使用的行情数据源
- 可展开查看行情接口错误

## 从V4更新
GitHub原仓库中替换 app.py 和 requirements.txt，然后 Commit changes。
原 Streamlit URL 不变。通常会自动重新部署。
