# ⚡ Polymarket BTC 高频微套利机器人

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-green?logo=python" />
  <img src="https://img.shields.io/badge/Polymarket-CLOB_SDK-blueviolet" />
  <img src="https://img.shields.io/badge/Chain-Polygon_(137)-8247E5?logo=polygon" />
  <img src="https://img.shields.io/badge/License-MIT-yellow" />
</p>

针对 Polymarket **BTC 超短期预测市场**（5 分钟 / 15 分钟 上涨/下跌区间）的自动化高频微套利机器人。

> **⚠️ 风险警告**: 92%+ 的量化交易用户长期亏损。本项目仅供学习和小额测试，请勿投入无法承受损失的资金。

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🔗 **官方 SDK** | 基于 `py-clob-client` 官方 Python SDK |
| 📡 **市场发现** | 自动从 Gamma API 扫描活跃 BTC 5min/15min 市场 |
| 📈 **双策略** | 均值回归 + 跨市场延迟套利，可一键切换 |
| 📝 **Paper Trading** | 默认模拟模式，验证策略后再上线 |
| 🖥️ **Web 仪表盘** | Flask 绿屏实时仪表盘 (PnL 曲线 + 交易记录) |
| 🛡️ **风控系统** | 单笔 ≤ 1%、每日最大亏损 5%、紧急停止按钮 |
| 🌐 **防限流** | 代理池 + 随机 sleep 1-3s + 多实例部署 |
| 📊 **利润统计** | 累计盈亏、胜率、交易次数实时统计 |
| 📋 **双日志** | 控制台 + 文件日志同步记录 |

---

## 📁 项目结构

```
.
├── main.py            # 核心交易机器人 (~300行, 核心逻辑 ~70行)
├── requirements.txt   # Python 依赖
├── .env.example       # 环境变量模板
├── .env               # 你的实际配置 (自行创建, 不进入版本控制)
├── logs/              # 运行日志 (自动生成)
└── README.md
```

---

## 🚀 快速开始

### 1. 前置条件

- Python 3.9+
- Polygon 钱包 (MetaMask) + USDC.e 余额
- 少量 POL 作为 Gas 费

### 2. 安装

```bash
git clone https://github.com/ningfeiyu/Polymbot.git
cd Polymbot

python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
```

编辑 `.env`，填入你的钱包信息：

```env
PRIVATE_KEY=你的Polygon钱包私钥
FUNDER_ADDRESS=0x你的钱包地址
SIGNATURE_TYPE=0          # 0=EOA(MetaMask), 1=Email/Magic
TRADE_MODE=paper          # 先用 paper 模式测试!
STRATEGY=mean_reversion   # 或 latency_arb
```

### 4. 运行

```bash
python main.py
```

打开浏览器访问 `http://localhost:5050` 查看实时仪表盘。

---

## 🎯 交易策略

### 均值回归 (`mean_reversion`)

1. 获取 Polymarket 隐含概率（Yes/No 价格）
2. 与 BTC 短期中性概率 (50%) 对比
3. 偏差 > 阈值时逆向下限价单
4. 自动取消未成交订单

### 延迟套利 (`latency_arb`)

1. 实时监控 Binance BTC 现货价格变动
2. 当 BTC 出现急涨/急跌 (>0.3%) 但 Polymarket 未反映时
3. 快速买入被低估的 Yes/No 头寸

---

## 🛡️ 风控机制

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MAX_POSITION_PCT` | 1% | 单笔不超过账户余额的 1% |
| `MAX_DAILY_LOSS_PCT` | 5% | 每日亏损超 5% 自动停止 |
| `MIN_SIZE` / `MAX_SIZE` | 5 / 20 USDC | 单笔金额范围 |
| 紧急停止 | 仪表盘按钮 | 一键停止所有交易 |

---

## 🌐 多设备部署

**代理池方式:**
```env
PROXY_LIST=http://user:pass@ip1:8080,http://user:pass@ip2:8080
```

**多 VPS 分流:**
```bash
# VPS-1: 均值回归
STRATEGY=mean_reversion  POLL_INTERVAL=300

# VPS-2: 延迟套利
STRATEGY=latency_arb     POLL_INTERVAL=60
```

---

## 📊 预期表现

> 基于社区公开数据，仅供参考

| 指标 | 保守 | 中位数 | 乐观 |
|------|------|--------|------|
| 月化收益 | 3-5% | 8-15% | 20%+ |
| 胜率 | 48% | 52-55% | 60%+ |
| 最大回撤 | -15% | -8% | -3% |
| 电费成本 | ~$3/月 | ~$5/月 | ~$10/月 |

---

## ❗ 常见问题

| 错误 | 解决 |
|------|------|
| `L2_AUTH_FAILED` | 检查 `PRIVATE_KEY` 是否正确 |
| `INSUFFICIENT_BALANCE` | 充值 USDC.e |
| `429 / RATE_LIMITED` | 增大 `POLL_INTERVAL` 或添加代理 |
| `INVALID_SIGNATURE` | 检查 `SIGNATURE_TYPE` (EOA=0) |
| 扫描到 0 个市场 | 等待新 BTC 短期市场创建 |

---

## ⚠️ 免责声明

- 本项目仅供 **教育和研究目的**
- **不构成投资建议**，使用者需自行承担所有风险
- 92%+ 的量化交易者长期亏损，请仅使用可承受损失的小额资金
- Polymarket 在某些地区可能存在合规限制，请自行确认
- 作者不对任何资金损失负责

---

## 📜 License

MIT License
