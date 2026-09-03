import os
import time
import threading
import requests
import logging
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

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

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
        "/help - 显示帮助"
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
        "3️⃣ 查询结果仅供参考"
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
    phone_numbers = [num.strip() for num in text.splitlines() if num.strip()]

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
    # 从环境变量读取 Token
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

    if not BOT_TOKEN:
        print("❌ 错误：未设置 TELEGRAM_BOT_TOKEN 环境变量")
        print("请在 Render 的 Environment Variables 中设置")
        return

    # Render Web Service 要求监听 $PORT，否则判定部署超时
    start_health_server()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    app.add_error_handler(error_handler)

    print("🤖 Bot 已启动，正在监听消息...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
