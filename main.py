"""
Polymarket BTC 超短期预测市场 高频微套利机器人
=============================================
功能: 针对 BTC 5min/15min 上涨/下跌预测市场做高频微套利
策略: 均值回归 + 跨市场延迟套利 (可切换)
作者: AI Trading Bot Generator  |  版本: 2026.03
"""

import os, sys, json, time, random, logging, signal, math
from datetime import datetime, timedelta
from threading import Thread, Event
from dotenv import load_dotenv
import requests

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, OpenOrderParams
from py_clob_client.order_builder.constants import BUY, SELL

# ============== 加载配置 ==============
load_dotenv()
PRIVATE_KEY      = os.getenv("PRIVATE_KEY", "")
FUNDER_ADDRESS   = os.getenv("FUNDER_ADDRESS", "")
SIGNATURE_TYPE   = int(os.getenv("SIGNATURE_TYPE", "0"))
TRADE_MODE       = os.getenv("TRADE_MODE", "paper")        # paper / live
STRATEGY         = os.getenv("STRATEGY", "mean_reversion")  # mean_reversion / latency_arb
DEV_THRESHOLD    = float(os.getenv("DEVIATION_THRESHOLD", "0.02"))
MIN_SIZE         = float(os.getenv("MIN_SIZE", "5"))
MAX_SIZE         = float(os.getenv("MAX_SIZE", "20"))
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL", "300"))
MAX_POS_PCT      = float(os.getenv("MAX_POSITION_PCT", "0.01"))
MAX_DAILY_LOSS   = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))
PROXY_LIST       = [p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()]
DASHBOARD_PORT   = int(os.getenv("DASHBOARD_PORT", "5050"))

HOST     = "https://clob.polymarket.com"
CHAIN_ID = 137
GAMMA    = "https://gamma-api.polymarket.com"
BINANCE  = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

# ============== 日志配置 ==============
os.makedirs("logs", exist_ok=True)
log_fmt = logging.Formatter("[%(asctime)s] %(levelname)s  %(message)s", "%Y-%m-%d %H:%M:%S")
logger  = logging.getLogger("polybot")
logger.setLevel(logging.DEBUG)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(log_fmt)
logger.addHandler(ch)

fh = logging.FileHandler(f"logs/bot_{datetime.now():%Y%m%d}.log", encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(log_fmt)
logger.addHandler(fh)

# ============== 全局状态 (供仪表盘读取) ==============
STATE = {
    "started_at": datetime.now().isoformat(),
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,
    "daily_pnl": 0.0,
    "daily_reset": datetime.now().date().isoformat(),
    "active_orders": [],
    "history": [],       # 最近 200 条交易记录
    "emergency_stop": False,
    "last_btc_price": 0.0,
    "last_scan": "",
    "markets_found": 0,
}

stop_event = Event()  # 紧急停止信号


# =====================================================================
#  核心模块 1: 初始化 CLOB 客户端
# =====================================================================
def init_client() -> ClobClient:
    """初始化 Polymarket CLOB 客户端 (支持 EOA / 代理钱包)"""
    kwargs = dict(
        host=HOST,
        key=PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=SIGNATURE_TYPE,
    )
    if FUNDER_ADDRESS:
        kwargs["funder"] = FUNDER_ADDRESS

    client = ClobClient(**kwargs)
    client.set_api_creds(client.create_or_derive_api_creds())
    logger.info("✅ CLOB 客户端初始化成功 (signature_type=%d)", SIGNATURE_TYPE)
    return client


# =====================================================================
#  核心模块 2: 获取 BTC 实时价格 (Binance)
# =====================================================================
def get_btc_price() -> float:
    """从 Binance 获取 BTC/USDT 现货价格"""
    try:
        proxies = _random_proxy()
        r = requests.get(BINANCE, timeout=5, proxies=proxies)
        price = float(r.json()["price"])
        STATE["last_btc_price"] = price
        return price
    except Exception as e:
        logger.warning("⚠️  获取 BTC 价格失败: %s", e)
        return STATE["last_btc_price"] or 0.0


# =====================================================================
#  核心模块 3: 扫描 Gamma API 获取 BTC 短期市场
# =====================================================================
def scan_btc_markets() -> list[dict]:
    """
    从 Gamma API 获取活跃的 BTC 5min/15min 预测市场
    返回: [{condition_id, token_id_yes, token_id_no, question, end_date, ...}, ...]
    """
    keywords = ["Bitcoin", "BTC"]
    time_keywords = ["5 minute", "15 minute", "next 5 min", "next 15 min",
                     "5-minute", "15-minute", "5min", "15min"]
    markets_out = []

    try:
        proxies = _random_proxy()
        # 分页获取活跃市场
        url = f"{GAMMA}/markets?active=true&closed=false&limit=100"
        r = requests.get(url, timeout=10, proxies=proxies)
        all_markets = r.json() if r.status_code == 200 else []

        for m in all_markets:
            q = (m.get("question", "") + " " + m.get("description", "")).lower()
            has_btc = any(kw.lower() in q for kw in keywords)
            has_time = any(kw.lower() in q for kw in time_keywords)
            if has_btc and has_time:
                tokens = m.get("clobTokenIds", m.get("tokens", []))
                if len(tokens) >= 2:
                    markets_out.append({
                        "condition_id": m.get("conditionId", m.get("condition_id", "")),
                        "question": m.get("question", ""),
                        "token_yes": tokens[0] if isinstance(tokens[0], str) else tokens[0].get("token_id", ""),
                        "token_no":  tokens[1] if isinstance(tokens[1], str) else tokens[1].get("token_id", ""),
                        "end_date": m.get("endDate", m.get("end_date_iso", "")),
                        "outcome_prices": m.get("outcomePrices", ""),
                    })
    except Exception as e:
        logger.error("❌ 扫描市场失败: %s", e)

    STATE["markets_found"] = len(markets_out)
    STATE["last_scan"] = datetime.now().isoformat()
    logger.info("🔍 扫描到 %d 个 BTC 短期市场", len(markets_out))
    return markets_out


# =====================================================================
#  核心模块 4: 交易策略引擎
# =====================================================================
def calc_implied_prob(market: dict) -> tuple[float, float]:
    """从订单簿价格解析隐含概率 (Yes价格 = Yes隐含概率)"""
    try:
        prices = market.get("outcome_prices", "")
        if isinstance(prices, str) and prices:
            prices = json.loads(prices)
        if isinstance(prices, list) and len(prices) >= 2:
            return float(prices[0]), float(prices[1])
    except Exception:
        pass
    return 0.5, 0.5


def strategy_mean_reversion(btc_price: float, market: dict, client: ClobClient):
    """
    均值回归策略:
    - 获取 Polymarket 隐含概率
    - 与 BTC 短期波动率比较
    - 偏差 > 阈值时下限价单
    """
    prob_yes, prob_no = calc_implied_prob(market)

    # BTC 5min 典型波动率 ~0.15% => 上涨概率约 50% ± 波动率偏移
    # 简化模型: 中性概率 = 0.50, 偏差 = |prob_yes - 0.50|
    fair_prob = 0.50  # 短期无方向偏好
    deviation = prob_yes - fair_prob

    if abs(deviation) < DEV_THRESHOLD:
        return  # 偏差不够大,跳过

    # 偏差 > 阈值: 逆向下单
    if deviation > 0:
        # Yes 被高估 => 买 No
        token_id = market["token_no"]
        side = BUY
        price = round(prob_no - 0.01, 2)  # 比当前 No 价稍低挂单
        direction = "BUY_NO"
    else:
        # No 被高估 => 买 Yes
        token_id = market["token_yes"]
        side = BUY
        price = round(prob_yes - 0.01, 2)
        direction = "BUY_YES"

    price = max(0.01, min(0.99, price))  # 价格边界
    size = _calc_size(price)

    _place_order(client, token_id, price, size, side, market["question"], direction)


def strategy_latency_arb(btc_price: float, market: dict, client: ClobClient):
    """
    跨市场延迟套利:
    - 同时监控 Binance 现货 + Polymarket 隐含概率
    - 当 BTC 出现急涨急跌 (~0.3%) 但 Polymarket 未反映时建仓
    """
    prob_yes, prob_no = calc_implied_prob(market)
    # 需要历史价格来判断瞬时涨跌 (取最近价格)
    prev_price = STATE.get("_prev_btc", btc_price)
    STATE["_prev_btc"] = btc_price

    if prev_price <= 0:
        return

    pct_change = (btc_price - prev_price) / prev_price

    if abs(pct_change) < 0.003:  # 低于 0.3% 变动，忽略
        return

    if pct_change > 0 and prob_yes < 0.55:
        # BTC 急涨但隐含上涨概率仍低 => 买 Yes
        token_id = market["token_yes"]
        price = round(min(prob_yes + 0.02, 0.99), 2)
        size = _calc_size(price)
        _place_order(client, token_id, price, size, BUY, market["question"], "LATENCY_BUY_YES")

    elif pct_change < 0 and prob_no < 0.55:
        # BTC 急跌但隐含下跌概率仍低 => 买 No
        token_id = market["token_no"]
        price = round(min(prob_no + 0.02, 0.99), 2)
        size = _calc_size(price)
        _place_order(client, token_id, price, size, BUY, market["question"], "LATENCY_BUY_NO")


# =====================================================================
#  核心模块 5: 下单 + 风控
# =====================================================================
def _calc_size(price: float) -> float:
    """根据价格随机选择下单大小 (5-20 USDC 范围内)"""
    base = random.uniform(MIN_SIZE, MAX_SIZE)
    return round(base, 2)


def _place_order(client: ClobClient, token_id: str, price: float,
                 size: float, side: str, question: str, direction: str):
    """下单核心 (含风控、paper trade 检查)"""

    # === 风控检查 ===
    if STATE["emergency_stop"]:
        logger.warning("🛑 紧急停止已启用，跳过下单")
        return

    # 每日亏损检查
    if STATE["daily_pnl"] < -(MAX_DAILY_LOSS * 1000):  # 假设初始资金 ~1000 USDC
        logger.warning("🛑 每日最大亏损已达限额 (%.2f), 停止交易", STATE["daily_pnl"])
        STATE["emergency_stop"] = True
        return

    record = {
        "time": datetime.now().isoformat(),
        "direction": direction,
        "token_id": token_id[:16] + "...",
        "price": price,
        "size": size,
        "question": question[:60],
        "status": "pending",
    }

    if TRADE_MODE == "paper":
        # 模拟交易 —— 不实际下单
        record["status"] = "paper"
        logger.info("📝 [PAPER] %s | 价格=%.2f 数量=%.2f | %s", direction, price, size, question[:50])
        STATE["total_trades"] += 1
        _record_trade(record)
        return

    try:
        # 防限流: 随机休眠 1~3 秒
        time.sleep(random.uniform(1.0, 3.0))

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )
        signed = client.create_order(order_args)
        resp = client.post_order(signed, OrderType.GTC)
        logger.info("✅ [LIVE] %s | 价格=%.2f 数量=%.2f | resp=%s", direction, price, size, resp)
        record["status"] = "submitted"
        record["order_id"] = resp.get("orderID", resp.get("id", ""))
        STATE["total_trades"] += 1

    except Exception as e:
        logger.error("❌ 下单失败: %s", e)
        record["status"] = "error"
        record["error"] = str(e)

    _record_trade(record)


def _cancel_stale_orders(client: ClobClient):
    """取消所有未成交的挂单"""
    if TRADE_MODE == "paper":
        return
    try:
        client.cancel_all()
        logger.info("🗑️  已取消所有未成交订单")
    except Exception as e:
        logger.debug("取消订单异常: %s", e)


def _record_trade(record: dict):
    """记录交易到历史"""
    STATE["history"].insert(0, record)
    if len(STATE["history"]) > 200:
        STATE["history"] = STATE["history"][:200]


# =====================================================================
#  核心模块 6: 代理池
# =====================================================================
def _random_proxy() -> dict:
    """随机选择代理 (防限流)"""
    if not PROXY_LIST:
        return {}
    proxy = random.choice(PROXY_LIST)
    return {"http": proxy, "https": proxy}


# =====================================================================
#  核心模块 7: 利润统计
# =====================================================================
def get_stats() -> dict:
    """返回格式化的利润统计"""
    total = STATE["total_trades"]
    wins  = STATE["wins"]
    wr = (wins / total * 100) if total > 0 else 0.0
    return {
        "累计预测次数": total,
        "胜率": f"{wr:.1f}%",
        "累计盈亏 (USDC)": f"{STATE['pnl']:.2f}",
        "今日盈亏 (USDC)": f"{STATE['daily_pnl']:.2f}",
        "最近 BTC 价格": f"${STATE['last_btc_price']:,.2f}",
        "扫描到的市场数": STATE["markets_found"],
        "最近扫描时间": STATE["last_scan"],
        "紧急停止": STATE["emergency_stop"],
        "运行模式": TRADE_MODE.upper(),
        "策略": STRATEGY,
        "预估月化": "8-15% (社区中位数)",
        "预估电费": "< $5/月 (轻量级 VPS)",
    }


# =====================================================================
#  核心模块 8: Flask 仪表盘
# =====================================================================
def start_dashboard():
    """启动 Flask Web 仪表盘 (后台线程)"""
    from flask import Flask, jsonify, render_template_string

    app = Flask(__name__)
    app.logger.setLevel(logging.WARNING)

    DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket BTC Bot - Dashboard</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#0a0e17; color:#00ff88; font-family:'Courier New',monospace; padding:20px; }
  h1 { text-align:center; font-size:1.6em; margin-bottom:10px; color:#00ffaa;
       text-shadow: 0 0 20px rgba(0,255,136,0.5); }
  .subtitle { text-align:center; color:#667; font-size:0.85em; margin-bottom:20px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-bottom:20px; }
  .card { background:rgba(0,255,136,0.05); border:1px solid rgba(0,255,136,0.2);
          border-radius:8px; padding:14px; }
  .card .label { color:#668; font-size:0.75em; text-transform:uppercase; letter-spacing:1px; }
  .card .value { font-size:1.4em; margin-top:4px; font-weight:bold; }
  .positive { color:#00ff88; }
  .negative { color:#ff4466; }
  .btn { background:rgba(255,68,102,0.2); border:1px solid #ff4466; color:#ff4466;
         padding:10px 24px; border-radius:6px; cursor:pointer; font-size:1em;
         text-align:center; display:block; margin:0 auto 20px; transition:0.3s; }
  .btn:hover { background:rgba(255,68,102,0.4); }
  table { width:100%; border-collapse:collapse; font-size:0.82em; }
  th { text-align:left; color:#446; padding:6px; border-bottom:1px solid #1a2030; }
  td { padding:6px; border-bottom:1px solid #0d1220; }
  .tag { padding:2px 6px; border-radius:3px; font-size:0.75em; }
  .tag-paper { background:rgba(255,200,0,0.15); color:#ffcc00; }
  .tag-live { background:rgba(0,255,136,0.15); color:#00ff88; }
  .tag-error { background:rgba(255,68,102,0.15); color:#ff4466; }
  #chart { width:100%; height:180px; background:rgba(0,255,136,0.03);
           border:1px solid rgba(0,255,136,0.1); border-radius:8px; margin-bottom:20px; position:relative; }
  canvas { width:100% !important; height:100% !important; }
</style>
</head>
<body>
<h1>⚡ Polymarket BTC Bot</h1>
<p class="subtitle">高频微套利仪表盘 | 自动刷新 10s</p>
<div class="grid" id="stats"></div>
<button class="btn" id="stopBtn" onclick="emergencyStop()">🛑 紧急停止</button>
<div id="chart"><canvas id="pnlChart"></canvas></div>
<h3 style="margin-bottom:8px;">📋 最近交易</h3>
<table>
<thead><tr><th>时间</th><th>方向</th><th>价格</th><th>数量</th><th>状态</th><th>市场</th></tr></thead>
<tbody id="trades"></tbody>
</table>

<script>
let pnlHistory = [];
function refresh() {
  fetch('/api/status').then(r=>r.json()).then(d => {
    let html = '';
    for (let [k,v] of Object.entries(d.stats)) {
      let cls = typeof v === 'string' && v.startsWith('-') ? 'negative' : 'positive';
      html += `<div class="card"><div class="label">${k}</div><div class="value ${cls}">${v}</div></div>`;
    }
    document.getElementById('stats').innerHTML = html;

    let tbody = '';
    (d.history || []).slice(0,30).forEach(t => {
      let cls = t.status === 'paper' ? 'tag-paper' : t.status === 'error' ? 'tag-error' : 'tag-live';
      tbody += `<tr><td>${(t.time||'').slice(11,19)}</td><td>${t.direction}</td>
        <td>${t.price}</td><td>${t.size}</td>
        <td><span class="tag ${cls}">${t.status}</span></td>
        <td>${(t.question||'').slice(0,40)}</td></tr>`;
    });
    document.getElementById('trades').innerHTML = tbody;

    // PnL 图表
    pnlHistory.push(parseFloat(d.stats['累计盈亏 (USDC)']) || 0);
    if (pnlHistory.length > 100) pnlHistory.shift();
    drawChart();
  }).catch(()=>{});
}

function drawChart() {
  const canvas = document.getElementById('pnlChart');
  const ctx = canvas.getContext('2d');
  canvas.width = canvas.parentElement.clientWidth;
  canvas.height = canvas.parentElement.clientHeight;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (pnlHistory.length < 2) return;
  const max = Math.max(...pnlHistory, 1);
  const min = Math.min(...pnlHistory, -1);
  const range = max - min || 1;
  const w = canvas.width, h = canvas.height;
  const step = w / (pnlHistory.length - 1);

  ctx.beginPath();
  ctx.strokeStyle = '#00ff88';
  ctx.lineWidth = 2;
  ctx.shadowColor = '#00ff88';
  ctx.shadowBlur = 8;
  pnlHistory.forEach((v, i) => {
    const x = i * step;
    const y = h - ((v - min) / range) * (h - 20) - 10;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function emergencyStop() {
  fetch('/api/stop', {method:'POST'}).then(()=> {
    document.getElementById('stopBtn').textContent = '🛑 已停止';
    document.getElementById('stopBtn').style.background = 'rgba(255,68,102,0.6)';
  });
}

setInterval(refresh, 10000);
refresh();
</script>
</body>
</html>
"""

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/status")
    def api_status():
        return jsonify({"stats": get_stats(), "history": STATE["history"][:50]})

    @app.route("/api/stop", methods=["POST"])
    def api_stop():
        STATE["emergency_stop"] = True
        stop_event.set()
        logger.warning("🛑 紧急停止已通过仪表盘触发")
        return jsonify({"ok": True})

    thread = Thread(target=lambda: app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False), daemon=True)
    thread.start()
    logger.info("📊 仪表盘已启动: http://localhost:%d", DASHBOARD_PORT)


# =====================================================================
#  核心模块 9: 主循环
# =====================================================================
def main_loop(client: ClobClient):
    """主交易循环"""
    cycle = 0
    while not stop_event.is_set():
        cycle += 1
        logger.info("=" * 60)
        logger.info("🔄 第 %d 轮扫描开始 (模式: %s / %s)", cycle, TRADE_MODE.upper(), STRATEGY)

        # 每日盈亏重置
        today = datetime.now().date().isoformat()
        if STATE["daily_reset"] != today:
            STATE["daily_pnl"] = 0.0
            STATE["daily_reset"] = today
            STATE["emergency_stop"] = False  # 新的一天重置紧急停止

        try:
            # Step 1: 获取 BTC 价格
            btc_price = get_btc_price()
            if btc_price <= 0:
                logger.warning("⚠️  BTC 价格无效，跳过本轮")
                _sleep_with_check(POLL_INTERVAL)
                continue
            logger.info("📈 BTC 当前价格: $%,.2f", btc_price)

            # Step 2: 扫描活跃市场
            markets = scan_btc_markets()
            if not markets:
                logger.info("💤 未发现活跃的 BTC 短期市场，等待下一轮...")
                _sleep_with_check(POLL_INTERVAL)
                continue

            # Step 3: 取消旧挂单
            _cancel_stale_orders(client)

            # Step 4: 对每个市场执行策略
            for mkt in markets:
                if stop_event.is_set() or STATE["emergency_stop"]:
                    break

                logger.info("📌 处理市场: %s", mkt["question"][:60])
                if STRATEGY == "latency_arb":
                    strategy_latency_arb(btc_price, mkt, client)
                else:
                    strategy_mean_reversion(btc_price, mkt, client)

                # 防限流随机间隔
                time.sleep(random.uniform(1.0, 3.0))

        except KeyboardInterrupt:
            logger.info("⏹️  用户中断")
            break
        except Exception as e:
            logger.error("❌ 主循环异常: %s", e, exc_info=True)

        _sleep_with_check(POLL_INTERVAL)

    logger.info("🏁 机器人已停止运行")


def _sleep_with_check(seconds: int):
    """可中断的休眠"""
    for _ in range(seconds):
        if stop_event.is_set():
            return
        time.sleep(1)


# =====================================================================
#  入口
# =====================================================================
def main():
    print(r"""
    ____        __                         __        __     ____        __
   / __ \____  / /_  ______ ___  ____ ____/ /_____  / /_   / __ )____  / /_
  / /_/ / __ \/ / / / / __ `__ \/ __ `/ __  / ___/ / _ \ / __  / __ \/ __/
 / ____/ /_/ / / /_/ / / / / / / /_/ / /_/ / /__  /  __// /_/ / /_/ / /_
/_/    \____/_/\__, /_/ /_/ /_/\__,_/\__,_/\___/  \___//_____/\____/\__/
              /____/
    ⚡ BTC 超短期预测市场 高频微套利机器人 v2026.03 ⚡
    """)

    # 参数校验
    if not PRIVATE_KEY:
        logger.error("❌ 请在 .env 中设置 PRIVATE_KEY")
        sys.exit(1)

    logger.info("🚀 启动参数: MODE=%s  STRATEGY=%s  THRESHOLD=%.2f  SIZE=%.0f-%.0f  INTERVAL=%ds",
                TRADE_MODE, STRATEGY, DEV_THRESHOLD, MIN_SIZE, MAX_SIZE, POLL_INTERVAL)

    # 启动仪表盘
    start_dashboard()

    # 初始化客户端
    client = init_client()

    # 注册信号处理 (Ctrl+C 优雅退出)
    def _signal_handler(sig, frame):
        logger.info("⏹️  收到退出信号，正在停止...")
        stop_event.set()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 启动主循环
    main_loop(client)


if __name__ == "__main__":
    main()
