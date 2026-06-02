#!/usr/bin/env python3
"""
美股代币持仓日报生成器
- 获取底层真实股价（通过yfinance）
- 计算持仓收益（USD + CNY）
- 生成含图表的PDF报告
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import pandas as pd
import requests

# ============================================================
# 配置
# ============================================================
PORTFOLIO_FILE = "/workspace/portfolio.json"
REPORTS_DIR = "/workspace/reports"
CHARTS_DIR = "/data/user/work/charts"

USD_TO_CNY = 6.88  # 固定汇率
ALPHA_VANTAGE_API_KEY = "NIAF8WS3CEY258E1"  # Alpha Vantage API Key

# 代币 -> 底层股票映射
TOKEN_STOCK_MAP = {
    "GOOGLon": "GOOGL",
    "MUon": "MU",
    "AMDOn": "AMD",
    "AMATon": "AMAT",
    "MCDon": "MCD",
}

# 代币 -> 中文名映射
TOKEN_CN_NAMES = {
    "GOOGLon": "谷歌",
    "MUon": "美光科技",
    "AMDOn": "AMD",
    "AMATon": "应用材料",
    "MCDon": "麦当劳",
}

# ============================================================
# 中文字体配置
# ============================================================
def setup_cjk_font():
    """配置matplotlib中文字体"""
    font_names = ['Noto Sans CJK SC', 'WenQuanYi Zen Hei', 'Droid Sans Fallback']
    available = [f.name for f in fm.fontManager.ttflist]
    for name in font_names:
        if name in available:
            plt.rcParams['font.sans-serif'] = [name] + plt.rcParams['font.sans-serif']
            plt.rcParams['axes.unicode_minus'] = False
            return name
    # fallback
    plt.rcParams['axes.unicode_minus'] = False
    return None

# ============================================================
# 数据获取
# ============================================================
def load_portfolio(filepath):
    """加载持仓数据"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def fetch_with_retry(url, headers, max_retries=3, timeout=15):
    """带重试的HTTP请求"""
    import time
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200 and len(resp.text.strip()) > 10:
                return resp
            print(f"   重试 {attempt+1}/{max_retries}: HTTP {resp.status_code}")
        except Exception as e:
            print(f"   重试 {attempt+1}/{max_retries}: {type(e).__name__}")
        if attempt < max_retries - 1:
            time.sleep(2)
    return None

def get_stock_prices(holdings):
    """
    获取所有底层股票的当前价格和前一日收盘价
    使用 Stooq.com 免费 CSV API（无需API Key）
    返回: {token_name: {current_price, prev_close, change_pct}}
    """
    import csv
    import io
    import time

    results = {}

    # 构建所有需要的股票代码
    stooq_symbols = []
    for h in holdings:
        stock = TOKEN_STOCK_MAP.get(h['token_name'])
        if stock:
            stooq_symbols.append(f"{stock}.us")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    # 1. 批量获取当前报价
    symbol_str = " ".join(stooq_symbols)
    url_quote = f"https://stooq.com/q/l/?s={symbol_str}&f=sd2t2ohlcv&e=csv"
    print(f"   获取实时报价: {symbol_str}")
    resp_quote = fetch_with_retry(url_quote, headers, max_retries=3)
    
    quote_data = {}
    if resp_quote and resp_quote.status_code == 200:
        quote_lines = resp_quote.text.strip().split('\n')
        for line in quote_lines:
            parts = line.split(',')
            if len(parts) >= 7:
                sym = parts[0]
                try:
                    close_price = float(parts[6])  # Close是第7列
                    quote_data[sym] = close_price
                except:
                    pass

    time.sleep(0.5)

    # 2. 批量获取前一日收盘价
    url_prev = f"https://stooq.com/q/l/?s={symbol_str}&f=sp&e=csv"
    resp_prev = fetch_with_retry(url_prev, headers, max_retries=3)
    
    prev_prices = {}
    if resp_prev and resp_prev.status_code == 200:
        prev_lines = resp_prev.text.strip().split('\n')
        for line in prev_lines:
            parts = line.split(',')
            if len(parts) == 2:
                try:
                    prev_prices[parts[0]] = float(parts[1])
                except:
                    pass

    # 3. 整合数据
    for h in holdings:
        token = h['token_name']
        stock = TOKEN_STOCK_MAP.get(token)
        if not stock:
            continue

        stooq_sym = f"{stock}.US"
        try:
            if stooq_sym in quote_data:
                current_price = quote_data[stooq_sym]
            else:
                print(f"   ⚠️ {stock} 无实时报价")
                continue

            if stooq_sym in prev_prices:
                prev_close = prev_prices[stooq_sym]
            else:
                prev_close = current_price

            change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close != 0 else 0

            results[token] = {
                'current_price': current_price,
                'prev_close': prev_close,
                'change_pct': change_pct,
                'stock': stock,
            }
            print(f"✅ {token} ({stock}): 当前 ${current_price:.2f}, 前收 ${prev_close:.2f}, 当日 {change_pct:+.2f}%")

        except Exception as e:
            print(f"❌ 获取 {token} ({stock}) 价格失败: {e}")

    return results


def get_stock_history_from_alpha_vantage(stock_symbol, start_date, end_date):
    """
    使用 Alpha Vantage API 获取股票历史数据
    返回: [(date_str, close_price), ...] 按日期升序排列
    """
    import time

    url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={stock_symbol}&apikey={ALPHA_VANTAGE_API_KEY}"

    try:
        print(f"   从 Alpha Vantage 获取 {stock_symbol} 历史数据...")
        r = requests.get(url, timeout=30)
        data = r.json()

        if 'Time Series (Daily)' not in data:
            if 'Note' in data:
                print(f"   ⚠️ Alpha Vantage API限制: {data['Note']}")
            elif 'Information' in data:
                print(f"   ⚠️ Alpha Vantage: {data['Information']}")
            else:
                print(f"   ⚠️ Alpha Vantage 返回错误: {data}")
            return None

        time_series = data['Time Series (Daily)']
        history = []

        for date_str, values in time_series.items():
            # 只取在日期范围内的数据
            if start_date <= date_str <= end_date:
                close_price = float(values['4. close'])
                history.append((date_str, close_price))

        # 按日期升序排列
        history.sort(key=lambda x: x[0])

        print(f"   ✅ 获取到 {len(history)} 天历史数据 ({history[0][0]} ~ {history[-1][0]})")

        # Alpha Vantage 免费版限速：每分钟最多5次调用
        time.sleep(13)  # 等待13秒，确保不超过限速

        return history

    except Exception as e:
        print(f"   ❌ 获取 {stock_symbol} 历史数据失败: {e}")
        return None

# ============================================================
# 收益计算
# ============================================================
def calculate_portfolio(holdings, price_data):
    """计算持仓收益"""
    total_cost_usd = 0
    total_value_usd = 0
    total_prev_value_usd = 0
    details = []

    for h in holdings:
        token = h['token_name']
        qty = h['quantity']
        cost_usdt = h['cost_usdt']
        buy_date = h['buy_date']

        if token not in price_data:
            continue

        pd_data = price_data[token]
        current_price = pd_data['current_price']
        prev_close = pd_data['prev_close']
        change_pct = pd_data['change_pct']

        # 当前市值（USD）
        value_usd = qty * current_price
        # 成本（USDT，按1:1等同于USD）
        cost_per_token_usd = cost_usdt / qty

        # 持有以来盈亏
        pnl_usd = value_usd - cost_usdt
        pnl_pct = (pnl_usd / cost_usdt * 100) if cost_usdt != 0 else 0

        # 当日盈亏
        prev_value_usd = qty * prev_close
        daily_pnl_usd = value_usd - prev_value_usd
        daily_pnl_pct = change_pct

        # 持有天数
        buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
        hold_days = (datetime.now() - buy_dt).days

        # 折算人民币
        value_cny = value_usd * USD_TO_CNY
        cost_cny = cost_usdt * USD_TO_CNY
        pnl_cny = pnl_usd * USD_TO_CNY
        daily_pnl_cny = daily_pnl_usd * USD_TO_CNY

        detail = {
            'token': token,
            'cn_name': TOKEN_CN_NAMES.get(token, token),
            'stock': pd_data['stock'],
            'buy_date': buy_date,
            'quantity': qty,
            'cost_usdt': cost_usdt,
            'cost_per_token_usd': cost_per_token_usd,
            'current_price': current_price,
            'value_usd': value_usd,
            'value_cny': value_cny,
            'cost_cny': cost_cny,
            'pnl_usd': pnl_usd,
            'pnl_cny': pnl_cny,
            'pnl_pct': pnl_pct,
            'daily_pnl_usd': daily_pnl_usd,
            'daily_pnl_cny': daily_pnl_cny,
            'daily_pnl_pct': daily_pnl_pct,
            'hold_days': hold_days,
            'prev_close': prev_close,
        }
        details.append(detail)

        total_cost_usd += cost_usdt
        total_value_usd += value_usd
        total_prev_value_usd += prev_value_usd

    total_pnl_usd = total_value_usd - total_cost_usd
    total_pnl_cny = total_pnl_usd * USD_TO_CNY
    total_pnl_pct = (total_pnl_usd / total_cost_usd * 100) if total_cost_usd != 0 else 0

    total_daily_pnl_usd = total_value_usd - total_prev_value_usd
    total_daily_pnl_cny = total_daily_pnl_usd * USD_TO_CNY
    total_daily_pnl_pct = (total_daily_pnl_usd / total_prev_value_usd * 100) if total_prev_value_usd != 0 else 0

    summary = {
        'total_cost_usd': total_cost_usd,
        'total_cost_cny': total_cost_usd * USD_TO_CNY,
        'total_value_usd': total_value_usd,
        'total_value_cny': total_value_usd * USD_TO_CNY,
        'total_pnl_usd': total_pnl_usd,
        'total_pnl_cny': total_pnl_cny,
        'total_pnl_pct': total_pnl_pct,
        'total_daily_pnl_usd': total_daily_pnl_usd,
        'total_daily_pnl_cny': total_daily_pnl_cny,
        'total_daily_pnl_pct': total_daily_pnl_pct,
    }

    return summary, details

# ============================================================
# 图表生成
# ============================================================
def generate_charts(details, summary, charts_dir):
    """生成所有图表"""
    os.makedirs(charts_dir, exist_ok=True)
    setup_cjk_font()

    tokens = [d['token'] for d in details]
    cn_names = [d['cn_name'] for d in details]

    # --- 配色方案 ---
    colors_positive = '#22c55e'
    colors_negative = '#ef4444'
    bar_colors = [colors_positive if d['pnl_cny'] >= 0 else colors_negative for d in details]
    daily_colors = [colors_positive if d['daily_pnl_cny'] >= 0 else colors_negative for d in details]

    # --- 图1: 仓位分布横向条形图 ---
    fig, ax = plt.subplots(figsize=(12, 5))
    
    # 按市值排序，从大到小
    sorted_details = sorted(details, key=lambda x: x['value_cny'], reverse=True)
    stock_labels = [d['stock'] for d in sorted_details]
    values_cny = [d['value_cny'] for d in sorted_details]
    total_value = sum(values_cny)
    percentages = [v / total_value * 100 for v in values_cny]
    
    # 颜色方案
    bar_colors_dist = ['#22c55e', '#3b82f6', '#f59e0b', '#8b5cf6'][:len(details)]
    
    # 绘制横向条形图
    bars = ax.barh(range(len(sorted_details)), values_cny, color=bar_colors_dist, 
                   edgecolor='white', linewidth=1, height=0.65)
    ax.set_yticks(range(len(sorted_details)))
    ax.set_yticklabels(stock_labels, fontsize=14, fontweight='bold')
    ax.invert_yaxis()  # 最大值在最上面
    ax.set_xlabel('Market Value (CNY)', fontsize=13, fontweight='bold')
    ax.set_title('Position Distribution (by Market Value)', fontsize=16, fontweight='bold', pad=15)
    
    # 添加数值和百分比标签
    for bar, val, pct in zip(bars, values_cny, percentages):
        xpos = bar.get_width()
        label_text = f'¥{val:,.0f} ({pct:.1f}%)'
        ax.text(xpos + total_value * 0.015, bar.get_y() + bar.get_height()/2,
                label_text, ha='left', va='center', fontsize=13, fontweight='bold')
    
    # 设置x轴范围，为标签留出空间
    ax.set_xlim(0, max(values_cny) * 1.4)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='x', labelsize=12)
    plt.subplots_adjust(left=0.12, bottom=0.15)
    plt.tight_layout()
    pie_path = os.path.join(charts_dir, 'pie_chart.png')
    plt.savefig(pie_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"📊 仓位分布图已保存: {pie_path}")

    # --- 图2: 持有以来盈亏柱状图 ---
    fig, ax = plt.subplots(figsize=(12, 5))
    pnl_values = [d['pnl_cny'] for d in details]
    stock_labels_bar = [d['stock'] for d in details]
    bars = ax.bar(range(len(details)), pnl_values, color=bar_colors, edgecolor='white', linewidth=1, width=0.6)
    ax.set_xticks(range(len(details)))
    ax.set_xticklabels(stock_labels_bar, fontsize=14, fontweight='bold')
    ax.set_ylabel('P&L (CNY)', fontsize=13, fontweight='bold')
    ax.set_title('Total P&L Since Purchase', fontsize=16, fontweight='bold', pad=15)
    ax.axhline(y=0, color='#374151', linewidth=1.5, linestyle='-')
    
    # 添加数值标签
    for bar, val in zip(bars, pnl_values):
        ypos = bar.get_height()
        offset = 25 if val >= 0 else -25
        ax.text(bar.get_x() + bar.get_width()/2, ypos + offset,
                f'¥{val:,.0f}', ha='center', va='bottom' if val >= 0 else 'top',
                fontsize=13, fontweight='bold')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='y', labelsize=12)
    plt.subplots_adjust(bottom=0.15)
    plt.tight_layout()
    pnl_bar_path = os.path.join(charts_dir, 'pnl_bar_chart.png')
    plt.savefig(pnl_bar_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"📊 盈亏柱状图已保存: {pnl_bar_path}")

    # --- 图3: 当日盈亏与涨跌幅横向条形图 ---
    fig, ax = plt.subplots(figsize=(12, 5))
    daily_values = [d['daily_pnl_cny'] for d in details]
    stock_labels = [d['stock'] for d in details]
    bars = ax.barh(range(len(details)), daily_values, color=daily_colors, edgecolor='white', linewidth=1, height=0.65)
    ax.set_yticks(range(len(details)))
    ax.set_yticklabels(stock_labels, fontsize=14, fontweight='bold')
    ax.set_xlabel('Daily P&L (CNY)', fontsize=13, fontweight='bold')
    ax.set_title('Daily P&L & Change %', fontsize=16, fontweight='bold', pad=15)
    ax.axvline(x=0, color='#374151', linewidth=2, linestyle='-')  # 加粗0轴线
    
    # 计算合适的X轴范围，为标签留出足够空间（左右各留20% padding）
    x_min = min(daily_values)
    x_max = max(daily_values)
    x_range = x_max - x_min if x_max != x_min else 100
    padding = x_range * 0.2 if x_range > 0 else 50
    ax.set_xlim(x_min - padding - abs(x_min) * 0.15, x_max + padding + abs(x_max) * 0.15)
    
    for bar, val, det in zip(bars, daily_values, details):
        xpos = bar.get_width()
        label_text = f'¥{val:+.1f} ({det["daily_pnl_pct"]:+.2f}%)'
        
        # 计算标签位置偏移量
        offset = x_range * 0.015 if x_range > 0 else 5
        
        if val >= 0:
            ax.text(xpos + offset, bar.get_y() + bar.get_height()/2,
                    label_text, ha='left', va='center',
                    fontsize=13, fontweight='bold')
        else:
            ax.text(xpos - offset, bar.get_y() + bar.get_height()/2,
                    label_text, ha='right', va='center',
                    fontsize=13, fontweight='bold')
    
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_linewidth(1.5)
    ax.tick_params(axis='x', labelsize=12)
    plt.subplots_adjust(left=0.15, bottom=0.15)
    daily_bar_path = os.path.join(charts_dir, 'daily_bar_chart.png')
    plt.savefig(daily_bar_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"📊 当日盈亏图已保存: {daily_bar_path}")

    # --- 图3.5: 整体持仓每日净收益走势图 ---
    print("   正在计算整体持仓每日净收益...")
    
    # 收集所有股票的历史数据
    all_stock_histories = {}
    earliest_date = None
    
    for d in details:
        stock = d['stock']
        buy_date = d['buy_date']
        cost_price = d['cost_per_token_usd']
        quantity = d['quantity']
        
        if earliest_date is None or buy_date < earliest_date:
            earliest_date = buy_date
        
        # 获取历史数据
        today_str = datetime.now().strftime("%Y-%m-%d")
        history = get_stock_history_from_alpha_vantage(stock, buy_date, today_str)
        
        if history and len(history) > 0:
            # 强制最后一个价格为当前价格
            history[-1] = (history[-1][0], d['current_price'])
            all_stock_histories[stock] = {
                'history': history,
                'cost_price': cost_price,
                'quantity': quantity,
                'buy_date': buy_date,
            }
    
    if all_stock_histories:
        # 构建日期序列（从最早买入日到今天）
        start_dt = datetime.strptime(earliest_date, "%Y-%m-%d")
        end_dt = datetime.now()
        date_range = []
        current_dt = start_dt
        while current_dt <= end_dt:
            date_range.append(current_dt.strftime("%Y-%m-%d"))
            current_dt += timedelta(days=1)
        
        # 计算每天的持仓总市值和总成本
        daily_total_values = []
        daily_total_costs = []
        daily_net_pnl = []
        valid_dates = []
        
        for date_str in date_range:
            total_value = 0
            total_cost = 0
            has_data = False
            
            for stock, info in all_stock_histories.items():
                buy_date = info['buy_date']
                if date_str < buy_date:
                    continue  # 还没买入
                
                # 找到该日期或之前最近的收盘价
                price = None
                for h_date, h_price in info['history']:
                    if h_date <= date_str:
                        price = h_price
                    else:
                        break
                
                if price is not None:
                    total_value += price * info['quantity'] * USD_TO_CNY
                    total_cost += info['cost_price'] * info['quantity'] * USD_TO_CNY
                    has_data = True
            
            if has_data:
                valid_dates.append(date_str)
                daily_total_values.append(total_value)
                daily_total_costs.append(total_cost)
                daily_net_pnl.append(total_value - total_cost)
        
        if len(valid_dates) > 1:
            fig, (ax_val, ax_pnl) = plt.subplots(2, 1, figsize=(12, 10), sharex=True,
                                                    gridspec_kw={'height_ratios': [1.2, 1], 'hspace': 0.12})
            
            dates_dt = [datetime.strptime(d, "%Y-%m-%d") for d in valid_dates]
            
            # 上层：总市值 vs 总成本
            ax_val.fill_between(dates_dt, daily_total_values, alpha=0.15, color='#3b82f6')
            ax_val.plot(dates_dt, daily_total_values, linewidth=2.5, color='#3b82f6', label='Total Value', zorder=3)
            ax_val.plot(dates_dt, daily_total_costs, linewidth=2, color='#f59e0b', linestyle='--', label='Total Cost', zorder=2)
            ax_val.set_ylabel('Value (CNY)', fontsize=13, fontweight='bold')
            ax_val.legend(loc='upper left', fontsize=12, framealpha=0.95)
            ax_val.grid(True, alpha=0.3, linestyle='-', linewidth=0.8)
            ax_val.spines['top'].set_visible(False)
            ax_val.spines['right'].set_visible(False)
            ax_val.tick_params(axis='y', labelsize=11)
            
            # 当前市值标注
            current_val = daily_total_values[-1]
            current_cost = daily_total_costs[-1]
            ax_val.scatter([dates_dt[-1]], [current_val], color='#3b82f6', s=200, zorder=5, edgecolor='white', linewidth=2)
            info_text = f'Now: ¥{current_val:,.0f}\nCost: ¥{current_cost:,.0f}'
            ax_val.text(0.97, 0.97, info_text, transform=ax_val.transAxes, fontsize=12,
                       verticalalignment='top', horizontalalignment='right',
                       bbox=dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.95, edgecolor='#d1d5db', linewidth=2),
                       fontweight='bold')
            ax_val.set_title('Portfolio Total Value vs Cost', fontsize=16, fontweight='bold', pad=15, loc='left')
            
            # 下层：每日净收益
            bar_colors_net = ['#22c55e' if pnl >= 0 else '#ef4444' for pnl in daily_net_pnl]
            ax_pnl.fill_between(dates_dt, daily_net_pnl, alpha=0.3, 
                               where=[p >= 0 for p in daily_net_pnl], color='#22c55e', interpolate=True)
            ax_pnl.fill_between(dates_dt, daily_net_pnl, alpha=0.3, 
                               where=[p < 0 for p in daily_net_pnl], color='#ef4444', interpolate=True)
            ax_pnl.plot(dates_dt, daily_net_pnl, linewidth=2, color='#1f2937', zorder=3)
            ax_pnl.axhline(y=0, color='#374151', linewidth=2.5, linestyle='-', zorder=2)
            ax_pnl.set_ylabel('Net P&L (CNY)', fontsize=13, fontweight='bold')
            ax_pnl.set_xlabel('Date', fontsize=13)
            ax_pnl.grid(True, alpha=0.3, linestyle='-', linewidth=0.8, axis='y')
            ax_pnl.spines['top'].set_visible(False)
            ax_pnl.spines['right'].set_visible(False)
            ax_pnl.tick_params(axis='both', labelsize=11)
            
            # 当前净收益标注
            current_pnl = daily_net_pnl[-1]
            pnl_color = '#22c55e' if current_pnl >= 0 else '#ef4444'
            ax_pnl.scatter([dates_dt[-1]], [current_pnl], color=pnl_color, s=200, zorder=5, edgecolor='white', linewidth=2)
            pnl_sign = '+' if current_pnl >= 0 else ''
            ax_pnl.annotate(f'Net P&L: {pnl_sign}¥{current_pnl:,.0f}', 
                           (dates_dt[-1], current_pnl), 
                           textcoords="offset points", xytext=(15, 0 if current_pnl >= 0 else -20), 
                           fontsize=13, ha='left', fontweight='bold', color=pnl_color,
                           bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.9, edgecolor=pnl_color, linewidth=2))
            
            # X轴日期格式
            ax_pnl.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%d'))
            ax_pnl.xaxis.set_major_locator(plt.matplotlib.dates.DayLocator(interval=max(1, len(dates_dt)//6)))
            for label in ax_pnl.get_xticklabels():
                label.set_rotation(30)
                label.set_horizontalalignment('right')
                label.set_fontsize(11)
            
            ax_pnl.set_title('Daily Net P&L (All Holdings)', fontsize=16, fontweight='bold', pad=15, loc='left')
            
            fig.suptitle('Portfolio Performance Overview', fontsize=18, fontweight='bold', y=0.98)
            plt.tight_layout(rect=[0, 0, 1, 0.96])
            net_pnl_path = os.path.join(charts_dir, 'net_pnl_trend.png')
            plt.savefig(net_pnl_path, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close()
            print(f"📊 整体持仓净收益图已保存: {net_pnl_path}")
        else:
            net_pnl_path = None
            print("   ⚠️ 历史数据不足，跳过整体净收益图")
    else:
        net_pnl_path = None
        print("   ⚠️ 无历史数据，跳过整体净收益图")

    # --- 图4: 各股历史走势图（上下双层卡片结构 - 高清大图）---
    history_paths = []
    
    for i, d in enumerate(details):
        # 创建上下两个子图，共享X轴 - 增高到10x14英寸，确保图表足够高
        fig, (ax_price, ax_pnl) = plt.subplots(2, 1, figsize=(10, 14), sharex=True, 
                                                gridspec_kw={'height_ratios': [1.2, 1], 'hspace': 0.15})
        
        stock = d['stock']
        buy_date = d['buy_date']
        cost_price = d['cost_per_token_usd']  # 成本价
        current_price = d['current_price']  # 使用明细数据中的当前价格，确保一致性
        quantity = d['quantity']
        
        # 计算日期范围
        buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
        today = datetime.now()
        start_date = buy_date
        end_date = today.strftime("%Y-%m-%d")
        
        # 获取真实历史数据
        history = get_stock_history_from_alpha_vantage(stock, start_date, end_date)
        
        if history and len(history) > 0:
            # 使用真实数据
            dates = [datetime.strptime(h[0], "%Y-%m-%d") for h in history]
            prices = [h[1] for h in history]
            
            # 强制将最后一个价格设置为当前价格（确保与表格一致）
            prices[-1] = current_price
            
            # 计算每日盈亏（按成本价计算！）
            pnl_daily = [(p - cost_price) * quantity * USD_TO_CNY for p in prices]
            
            data_source = "[Alpha Vantage]"
        else:
            dates = [buy_dt, today]
            prices = [cost_price, current_price]
            pnl_daily = [0, (current_price - cost_price) * quantity * USD_TO_CNY]
            
            data_source = "[Estimate]"
        
        # ========== 上层：股价走势图 ==========
        color_price = '#3b82f6'
        ax_price.plot(dates, prices, linewidth=3, color=color_price, label='Price', zorder=3)
        ax_price.set_ylabel('Price (USD)', fontsize=14, color=color_price, fontweight='bold')
        ax_price.tick_params(axis='y', labelcolor=color_price, labelsize=12)
        ax_price.grid(True, alpha=0.3, linestyle='-', linewidth=0.8)
        ax_price.spines['top'].set_visible(False)
        ax_price.spines['right'].set_visible(False)
        ax_price.spines['left'].set_color('#6b7280')
        ax_price.spines['bottom'].set_color('#6b7280')
        ax_price.tick_params(axis='both', width=1.5)
        
        # 成本价参考线（橙色虚线）
        ax_price.axhline(y=cost_price, color='#f59e0b', linestyle='--', alpha=0.9, linewidth=2.5, 
                        label=f'Cost ${cost_price:.2f}', zorder=2)
        
        # 标记当前价格点 - 更大更醒目
        price_color = '#22c55e' if prices[-1] >= cost_price else '#ef4444'
        ax_price.scatter([dates[-1]], [prices[-1]], color=price_color, s=250, zorder=5, edgecolor='white', linewidth=3)
        
        # 右上角醒目信息框
        pnl_pct = d['pnl_pct']
        pnl_sign_pct = '+' if pnl_pct >= 0 else ''
        current_pnl = pnl_daily[-1]
        pnl_cny_sign = '+' if current_pnl >= 0 else ''
        info_text = f'Now: ${current_price:.2f}\nCost: ${cost_price:.2f}\nP&L: {pnl_sign_pct}{pnl_pct:.1f}% ({pnl_cny_sign}¥{d["pnl_cny"]:,.0f})'
        ax_price.text(0.97, 0.97, info_text, transform=ax_price.transAxes, fontsize=13,
                     verticalalignment='top', horizontalalignment='right',
                     bbox=dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.95, edgecolor='#d1d5db', linewidth=2),
                     fontweight='bold')
        
        ax_price.legend(loc='upper left', fontsize=12, framealpha=0.95, edgecolor='#d1d5db')
        ax_price.set_title('Stock Price Trend', fontsize=16, fontweight='bold', pad=15, loc='left')
        
        # ========== 下层：持仓盈亏图 ==========
        bar_colors = ['#22c55e' if pnl >= 0 else '#ef4444' for pnl in pnl_daily]
        ax_pnl.bar(dates, pnl_daily, color=bar_colors, alpha=0.8, width=0.8, edgecolor='white', linewidth=0.8)
        ax_pnl.set_ylabel('P&L (CNY)', fontsize=14, fontweight='bold')
        ax_pnl.set_xlabel('Date', fontsize=13)
        
        # 加粗0轴线，必须一眼可见
        ax_pnl.axhline(y=0, color='#1f2937', linewidth=3, linestyle='-', zorder=3)
        ax_pnl.grid(True, alpha=0.3, linestyle='-', linewidth=0.8, axis='y')
        ax_pnl.spines['top'].set_visible(False)
        ax_pnl.spines['right'].set_visible(False)
        ax_pnl.spines['left'].set_color('#6b7280')
        ax_pnl.spines['bottom'].set_color('#6b7280')
        ax_pnl.tick_params(axis='both', width=1.5)
        ax_pnl.tick_params(axis='y', labelsize=12)
        
        # 标记当前盈亏点
        pnl_color = '#22c55e' if current_pnl >= 0 else '#ef4444'
        ax_pnl.scatter([dates[-1]], [current_pnl], color=pnl_color, s=250, zorder=5, edgecolor='white', linewidth=3)
        
        # 当前累计盈亏大标签
        pnl_cny_sign = '+' if current_pnl >= 0 else ''
        ax_pnl.annotate(f'Current P&L: {pnl_cny_sign}¥{current_pnl:,.0f}', 
                       (dates[-1], current_pnl), 
                       textcoords="offset points", xytext=(15, 0 if current_pnl >= 0 else -25), 
                       fontsize=14, ha='left', fontweight='bold', color=pnl_color,
                       bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.9, edgecolor=pnl_color, linewidth=2))
        
        # 设置X轴日期格式
        ax_pnl.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%d'))
        ax_pnl.xaxis.set_major_locator(plt.matplotlib.dates.DayLocator(interval=max(1, len(dates)//5)))
        for label in ax_pnl.get_xticklabels():
            label.set_rotation(30)
            label.set_horizontalalignment('right')
            label.set_fontsize(11)
        
        ax_pnl.set_title('Holding P&L Trend', fontsize=16, fontweight='bold', pad=15, loc='left')
        
        # 统一图表标题
        fig.suptitle(f'{stock} ({d["cn_name"]}) Holding Trend {data_source}', 
                    fontsize=18, fontweight='bold', y=0.98)
        
        plt.tight_layout(rect=[0, 0, 1, 0.96])
        history_path = os.path.join(charts_dir, f"history_{stock}.png")
        plt.savefig(history_path, dpi=150, bbox_inches='tight', facecolor='white')
        plt.close()
        history_paths.append(history_path)
        print(f"📊 {stock} 走势图已保存: {history_path}")

    return {
        'pie': pie_path,
        'pnl_bar': pnl_bar_path,
        'daily_bar': daily_bar_path,
        'net_pnl': net_pnl_path,
        'history': history_paths,
    }

# ============================================================
# PDF报告生成
# ============================================================
def generate_pdf(summary, details, charts, report_path, is_trading_day=True, last_trading_day=None, daily_label="当日涨跌"):
    """生成PDF报告"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import inch, mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.colors import HexColor, white, black
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        Image, KeepTogether, HRFlowable
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import platform

    # 注册中文字体
    font_path = "/workspace/wqy-zenhei.ttf"
    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont("CJK", font_path))
    else:
        print("⚠️ 中文字体文件不存在，使用默认字体")

    font_name = "CJK"
    font_bold = "CJK"

    # 颜色
    PRIMARY = HexColor('#1a365d')
    ACCENT = HexColor('#2b6cb0')
    GREEN = HexColor('#16a34a')
    RED = HexColor('#dc2626')
    GRAY = HexColor('#64748b')
    LIGHT_BG = HexColor('#f1f5f9')
    WHITE = white

    CONTENT_WIDTH = A4[0] - 2 * 0.75 * inch

    # 样式 - 增大字体
    styles = {
        'title': ParagraphStyle('Title', fontName=font_bold, fontSize=26, leading=32,
                                textColor=PRIMARY, alignment=TA_CENTER, spaceAfter=6, wordWrap='CJK'),
        'subtitle': ParagraphStyle('Subtitle', fontName=font_name, fontSize=13, leading=18,
                                   textColor=GRAY, alignment=TA_CENTER, spaceAfter=25, wordWrap='CJK'),
        'h1': ParagraphStyle('H1', fontName=font_bold, fontSize=18, leading=24,
                             textColor=PRIMARY, spaceBefore=20, spaceAfter=10, wordWrap='CJK'),
        'body': ParagraphStyle('Body', fontName=font_name, fontSize=12, leading=18,
                               textColor=HexColor('#334155'), wordWrap='CJK'),
        'caption': ParagraphStyle('Caption', fontName=font_name, fontSize=10, leading=14,
                                  textColor=GRAY, alignment=TA_CENTER, spaceAfter=10, wordWrap='CJK'),
        'alert': ParagraphStyle('Alert', fontName=font_bold, fontSize=12, leading=18,
                                textColor=RED, spaceBefore=5, spaceAfter=5, wordWrap='CJK'),
    }

    # 创建文档
    doc = SimpleDocTemplate(
        report_path, pagesize=A4,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.6*inch, bottomMargin=0.6*inch
    )

    story = []

    # ========== 标题 ==========
    today = datetime.now().strftime("%Y-%m-%d")
    weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    weekday = weekday_names[datetime.now().weekday()]
    story.append(Paragraph(f"美股代币持仓日报", styles['title']))
    
    # 副标题：如果是非交易日，标注数据截至日期
    if is_trading_day:
        subtitle_text = f"{today} {weekday} | 汇率 1 USD = {USD_TO_CNY} CNY"
    else:
        subtitle_text = f"{today} {weekday}（休盘）| 数据截至 {last_trading_day} 收盘 | 汇率 1 USD = {USD_TO_CNY} CNY"
    story.append(Paragraph(subtitle_text, styles['subtitle']))
    story.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=20))

    # ========== 持仓总览 ==========
    story.append(Paragraph("持仓总览", styles['h1']))
    story.append(HRFlowable(width="30%", thickness=1.5, color=ACCENT, spaceAfter=12))

    pnl_sign = "+" if summary['total_pnl_cny'] >= 0 else ""
    daily_sign = "+" if summary['total_daily_pnl_cny'] >= 0 else ""
    
    summary_data = [
        ['总市值 (CNY)', f"¥{summary['total_value_cny']:,.2f}"],
        ['总成本 (CNY)', f"¥{summary['total_cost_cny']:,.2f}"],
        ['总盈亏', f"{pnl_sign}¥{summary['total_pnl_cny']:,.2f} ({pnl_sign}{summary['total_pnl_pct']:.2f}%)"],
        [daily_label, f"{daily_sign}¥{summary['total_daily_pnl_cny']:,.2f} ({daily_sign}{summary['total_daily_pnl_pct']:.2f}%)"],
    ]

    # 根据盈亏正负设置行的文字颜色
    pnl_row_idx = 2 if summary['total_pnl_cny'] < 0 else None
    daily_row_idx = 3 if summary['total_daily_pnl_cny'] < 0 else None
    
    summary_table = Table(summary_data, colWidths=[CONTENT_WIDTH*0.4, CONTENT_WIDTH*0.6])
    table_styles = [
        ('FONTNAME', (0, 0), (0, -1), font_bold),
        ('FONTNAME', (1, 0), (1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 14),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [LIGHT_BG, WHITE]),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e2e8f0')),
        ('LEFTPADDING', (0, 0), (-1, -1), 15),
        ('RIGHTPADDING', (0, 0), (-1, -1), 15),
    ]
    
    # 设置盈亏文字颜色
    if pnl_row_idx is not None:
        table_styles.append(('TEXTCOLOR', (1, pnl_row_idx), (1, pnl_row_idx), RED))
    if daily_row_idx is not None:
        table_styles.append(('TEXTCOLOR', (1, daily_row_idx), (1, daily_row_idx), RED))
    if pnl_row_idx is None:
        table_styles.append(('TEXTCOLOR', (1, 2), (1, 2), GREEN))
    if daily_row_idx is None:
        table_styles.append(('TEXTCOLOR', (1, 3), (1, 3), GREEN))
    
    summary_table.setStyle(TableStyle(table_styles))
    story.append(summary_table)
    story.append(Spacer(1, 16))

    # ========== 个股明细 ==========
    story.append(Paragraph("个股明细", styles['h1']))
    story.append(HRFlowable(width="30%", thickness=1.5, color=ACCENT, spaceAfter=12))

    # 表头
    daily_col_header = daily_label.replace("涨跌", "涨跌%") if "涨跌" in daily_label else daily_label
    headers = ['代币', '股票', '买入日', '持仓量', '成本价($)', '现价($)',
               '市值(CNY)', '持仓盈亏(CNY)', '盈亏%', '当日盈亏(CNY)', daily_col_header]
    col_widths = [52, 42, 52, 48, 50, 50, 60, 58, 42, 62, 50]

    table_data = [headers]
    for d in details:
        pnl_s = "+" if d['pnl_cny'] >= 0 else ""
        daily_s = "+" if d['daily_pnl_cny'] >= 0 else ""
        daily_pct_s = "+" if d['daily_pnl_pct'] >= 0 else ""

        row = [
            f"{d['cn_name']}\n({d['token']})",
            d['stock'],
            d['buy_date'],
            f"{d['quantity']:.5f}",
            f"${d['cost_per_token_usd']:.2f}",
            f"${d['current_price']:.2f}",
            f"¥{d['value_cny']:,.0f}",
            f"{pnl_s}¥{d['pnl_cny']:,.0f}",
            f"{pnl_s}{d['pnl_pct']:.1f}%",
            f"{daily_s}¥{d['daily_pnl_cny']:,.1f}",
            f"{daily_pct_s}{d['daily_pnl_pct']:.2f}%",
        ]
        table_data.append(row)

    # 为个股明细表格设置颜色
    detail_table_styles = [
        # 表头
        ('BACKGROUND', (0, 0), (-1, 0), ACCENT),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), font_bold),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        # 数据行 - 增大字体
        ('FONTNAME', (0, 1), (-1, -1), font_name),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ALIGN', (0, 1), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [LIGHT_BG, WHITE]),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e2e8f0')),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]
    
    # 设置盈亏列和当日涨跌列的颜色
    for row_idx, d in enumerate(details, start=1):
        # 持仓盈亏列(第7列) 和 持仓盈亏%列(第8列)
        if d['pnl_cny'] < 0:
            detail_table_styles.append(('TEXTCOLOR', (7, row_idx), (8, row_idx), RED))
        else:
            detail_table_styles.append(('TEXTCOLOR', (7, row_idx), (8, row_idx), GREEN))
        
        # 当日盈亏(CNY)列(第9列)
        if d['daily_pnl_cny'] < 0:
            detail_table_styles.append(('TEXTCOLOR', (9, row_idx), (9, row_idx), RED))
        else:
            detail_table_styles.append(('TEXTCOLOR', (9, row_idx), (9, row_idx), GREEN))
        
        # 当日涨跌列(第10列)
        if d['daily_pnl_pct'] < 0:
            detail_table_styles.append(('TEXTCOLOR', (10, row_idx), (10, row_idx), RED))
        else:
            detail_table_styles.append(('TEXTCOLOR', (10, row_idx), (10, row_idx), GREEN))
    
    detail_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    detail_table.setStyle(TableStyle(detail_table_styles))
    story.append(detail_table)
    story.append(Spacer(1, 8))
    
    # 添加说明文字
    story.append(Paragraph(
        "说明：成本价按 USDT 计价，报表中按 1 USDT≈1 USD 折算展示。"
        "个股明细金额按展示精度四舍五入，分项合计与总览金额可能存在微小差异。",
        styles['caption']
    ))
    story.append(Spacer(1, 20))

    # ========== 可视化图表 ==========
    story.append(Paragraph("图表总览", styles['h1']))
    story.append(HRFlowable(width="30%", thickness=1.5, color=ACCENT, spaceAfter=12))

    # 仓位分布图 - 单独成块
    if os.path.exists(charts['pie']):
        img = Image(charts['pie'], width=CONTENT_WIDTH*0.9, height=CONTENT_WIDTH*0.38)
        story.append(img)
        story.append(Spacer(1, 15))

    # 盈亏柱状图和当日盈亏图并排展示
    if os.path.exists(charts['pnl_bar']) and os.path.exists(charts['daily_bar']):
        # 使用表格实现并排布局
        chart_width = CONTENT_WIDTH * 0.48
        pnl_img = Image(charts['pnl_bar'], width=chart_width, height=chart_width*0.42)
        daily_img = Image(charts['daily_bar'], width=chart_width, height=chart_width*0.42)
        chart_table = Table([[pnl_img, daily_img]], colWidths=[CONTENT_WIDTH*0.5, CONTENT_WIDTH*0.5])
        chart_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ]))
        story.append(chart_table)
        story.append(Spacer(1, 20))

    # 整体持仓净收益走势图
    if charts.get('net_pnl') and os.path.exists(charts['net_pnl']):
        story.append(Paragraph("整体持仓表现", styles['h1']))
        story.append(Paragraph("上方：总市值 vs 总成本 | 下方：每日净收益走势", styles['body']))
        story.append(HRFlowable(width="30%", thickness=1.5, color=ACCENT, spaceAfter=12))
        img = Image(charts['net_pnl'], width=CONTENT_WIDTH*0.95, height=CONTENT_WIDTH*0.8)
        story.append(img)
        story.append(Spacer(1, 20))

    # ========== 各股持仓走势（每只股票独立页面）==========
    story.append(Paragraph("各股持仓走势", styles['h1']))
    story.append(Paragraph("上方：股价走势与成本线 | 下方：每日持仓盈亏", styles['body']))
    story.append(HRFlowable(width="30%", thickness=1.5, color=ACCENT, spaceAfter=15))

    # 添加每只股票的历史走势图 - 每只股票单独一页
    if 'history' in charts:
        for hist_path in charts['history']:
            if os.path.exists(hist_path):
                stock_code = os.path.basename(hist_path).replace('history_', '').replace('.png', '')
                # 加大图表尺寸，充分利用页面宽度和高度
                img = Image(hist_path, width=CONTENT_WIDTH*0.95, height=CONTENT_WIDTH*1.1)
                story.append(img)
                story.append(Spacer(1, 20))

    # ========== 异动提醒 ==========
    story.append(Paragraph("异动提醒", styles['h1']))
    story.append(HRFlowable(width="30%", thickness=1.5, color=ACCENT, spaceAfter=12))

    alerts = []
    for d in details:
        # 当日异动提醒
        if abs(d['daily_pnl_pct']) >= 3:
            direction = "上涨" if d['daily_pnl_pct'] > 0 else "下跌"
            daily_sign = "+" if d['daily_pnl_cny'] >= 0 else ""
            alerts.append(
                f"[异动] {d['cn_name']} ({d['token']}) 当日{direction} {abs(d['daily_pnl_pct']):.2f}%, "
                f"当日盈亏 {daily_sign}¥{d['daily_pnl_cny']:,.1f}"
            )
        # 累计盈亏提醒
        if abs(d['pnl_pct']) >= 30:
            pnl_sign = "+" if d['pnl_cny'] >= 0 else ""
            pnl_pct_sign = "+" if d['pnl_pct'] >= 0 else ""
            alerts.append(
                f"[提醒] {d['cn_name']} ({d['token']}) 持有以来盈亏 "
                f"{pnl_pct_sign}{d['pnl_pct']:.1f}%, 累计盈亏 {pnl_sign}¥{d['pnl_cny']:,.0f}"
            )

    if alerts:
        for alert in alerts:
            story.append(Paragraph(alert, styles['alert']))
    else:
        story.append(Paragraph("今日无异动（涨跌 < 3% 且持有收益 < 30%）", styles['body']))

    story.append(Spacer(1, 25))
    story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor('#cbd5e1'), spaceAfter=10))
    story.append(Paragraph(
        f"报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"数据来源：Alpha Vantage + Stooq | 汇率：{USD_TO_CNY}",
        styles['caption']
    ))

    # 构建PDF
    doc.build(story)
    print(f"PDF报告已保存: {report_path}")

# ============================================================
# 主流程
# ============================================================
# ============================================================
# 交易日判断
# ============================================================
def get_last_trading_day():
    """
    判断今天是否为美股交易日（周一至周五）。
    返回: (is_trading_day, last_trading_day_str, label)
    - is_trading_day: 今天是否为交易日
    - last_trading_day_str: 最近交易日的日期字符串
    - label: 用于报告显示的标签（如"当日涨跌"或"最近交易日涨跌"）
    """
    today = datetime.now()
    weekday = today.weekday()  # 0=周一, 6=周日
    
    if weekday < 5:  # 周一到周五
        # 美股交易时间：美东 9:30-16:00
        # 北京时间 夏令时 21:30-04:00，冬令时 22:30-05:00
        # 简单判断：如果北京时间是工作日，且在凌晨5点之后，数据应该是今天的
        # 如果在凌晨5点之前，数据可能是前一天的
        if today.hour < 5:
            # 北京时间凌晨0-5点，美股可能还在交易或刚收盘，数据可能是前一天的
            last_day = today - timedelta(days=1)
            # 如果前一天是周末，继续往前找
            while last_day.weekday() >= 5:
                last_day -= timedelta(days=1)
            return (True, last_day.strftime("%Y-%m-%d"), "最近交易日涨跌")
        else:
            return (True, today.strftime("%Y-%m-%d"), "当日涨跌")
    else:
        # 周六或周日，往前找到最近的周五
        last_day = today - timedelta(days=(weekday - 4))  # 周六减1天到周五，周日减2天到周五
        return (False, last_day.strftime("%Y-%m-%d"), "最近交易日涨跌")

def main():
    print("=" * 50)
    print("美股代币持仓日报生成器")
    print("=" * 50)

    # 判断交易日
    is_trading_day, last_trading_day, daily_label = get_last_trading_day()
    if is_trading_day:
        print(f"\n   今天是交易日，显示当日数据")
    else:
        print(f"\n   今天是非交易日（周末），数据截至 {last_trading_day} 收盘")

    # 1. 加载持仓
    print("\n加载持仓数据...")
    portfolio = load_portfolio(PORTFOLIO_FILE)
    holdings = portfolio['holdings']
    print(f"   共 {len(holdings)} 只代币")

    # 2. 获取价格
    print("\n获取股票价格...")
    price_data = get_stock_prices(holdings)
    if not price_data:
        print("未能获取任何价格数据，退出")
        sys.exit(1)

    # 3. 计算收益
    print("\n计算持仓收益...")
    summary, details = calculate_portfolio(holdings, price_data)
    print(f"   总市值: ¥{summary['total_value_cny']:,.2f}")
    print(f"   总盈亏: ¥{summary['total_pnl_cny']:+,.2f} ({summary['total_pnl_pct']:+.2f}%)")
    print(f"   {daily_label}: ¥{summary['total_daily_pnl_cny']:+,.2f} ({summary['total_daily_pnl_pct']:+.2f}%)")

    # 4. 生成图表
    print("\n生成可视化图表...")
    charts = generate_charts(details, summary, CHARTS_DIR)

    # 5. 生成PDF
    print("\n生成PDF报告...")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    report_path = os.path.join(REPORTS_DIR, f"portfolio_{today_str}.pdf")
    generate_pdf(summary, details, charts, report_path, 
                  is_trading_day=is_trading_day, 
                  last_trading_day=last_trading_day, 
                  daily_label=daily_label)

    print("\n" + "=" * 50)
    print(f"报告生成完成！")
    print(f"   📄 {report_path}")
    print("=" * 50)

if __name__ == "__main__":
    main()
