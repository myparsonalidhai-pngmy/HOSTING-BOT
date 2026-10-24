# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import sys

os.environ["PYTHONIOENCODING"] = "utf-8"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except:
    pass
    
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import atexit
import requests
import hashlib
import mimetypes
import struct

# --- Flask Keep Alive ---
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "bot is running...."

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive server started.")
# --- End Flask Keep Alive ---

# --- Configuration ---
TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("Set BOT_TOKEN in the hosting platform environment variables.")
OWNER_ID = 8769604379
ADMIN_ID = 8769604379
YOUR_USERNAME = '@GOJOTEAMV1'
UPDATE_CHANNEL = ''
FORCE_JOIN_CHANNELS = {
"":"𝐉𝐎𝐈𝐍",
}

# Folder setup - using absolute paths
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

# File upload limits
FREE_USER_LIMIT = 150
SUBSCRIBED_USER_LIMIT = 350
ADMIN_LIMIT = 500
OWNER_LIMIT = float('inf')

# Create necessary directories
os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

# Initialize bot
bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {}
user_subscriptions = {}
user_files = {}  # Structure: {user_id: [(folder_name, file_name, file_type), ...]}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
file_db = {}

# Pending uploads - stores files waiting for folder name
pending_uploads = {}  # {user_id: {'file_name':..., 'file_type':..., 'content':..., 'message':..., 'is_zip': bool}}

banned_users = set()
banned_usernames = set()

# --- Malware Detection Configuration ---
MALWARE_SIGNATURES = [
    b'MZ', b'\x7fELF', b'\xfe\xed\xfa', b'\xce\xfa\xed\xfe', b'PK', b'Rar!',
]

ENCRYPTED_FILE_INDICATORS = [b'openssl', b'encrypted', b'cipher', b'DES', b'RSA', b'GPG', b'PGP']

SUSPICIOUS_KEYWORDS = [b'ransomware', b'trojan', b'virus', b'malware', b'backdoor', b'exploit', b'payload', b'botnet', b'keylogger', b'rootkit']

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Command Button Layouts ---
COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["📤 Send Command", "📞 Contact Owner"]
]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["💳 Subscriptions", "📢 Broadcast"],
    ["🔒 Lock Bot", "🟢 Running All Code"],
    ["📤 Send Command", "👑 Admin Panel"],
    ["📞 Contact Owner"]
]

def send_force_join_msg(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch, name in FORCE_JOIN_CHANNELS.items():
        if ch:
            markup.add(types.InlineKeyboardButton(text=name, url=f"https://t.me/{ch.replace('@', '')}"))
    markup.add(types.InlineKeyboardButton("✅ Joined All", callback_data="force_join_check"))
    bot.send_message(chat_id, "𝐉𝐎𝐈𝐍 𝐀𝐋𝐋 𝐂𝐇𝐀𝐍𝐍𝐄𝐋 𝐓𝐎 𝐔𝐒𝐄 𝐌𝐄 🤍🌙:", reply_markup=markup)

def is_user_joined_all(user_id):
    try:
        for ch in FORCE_JOIN_CHANNELS.keys():
            if not ch:
                continue
            member = bot.get_chat_member(ch, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        return True
    except Exception as e:
        logger.warning(f"Force join check error for {user_id}: {e}")
        return True  # Allow if channel check fails (e.g., bot not admin)

# --- Database Setup ---
def init_db():
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        # NEW: user_files table now has folder_name and unique per (user_id, folder_name, file_name)
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, folder_name TEXT, file_name TEXT, file_type TEXT,
                      PRIMARY KEY (user_id, folder_name, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}", exc_info=True)

def load_data():
    logger.info("Loading data from database...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()

        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                logger.warning(f"⚠️ Invalid expiry date format for user {user_id}: {expiry}.")

        # NEW: load with folder_name
        c.execute('SELECT user_id, folder_name, file_name, file_type FROM user_files')
        for user_id, folder_name, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((folder_name, file_name, file_type))

        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())

        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())

        conn.close()
        logger.info(f"Data loaded: {len(active_users)} users, {len(user_subscriptions)} subs, {len(admin_ids)} admins.")
    except Exception as e:
        logger.error(f"❌ Error loading data: {e}", exc_info=True)

init_db()
load_data()
# --- End Database Setup ---

# --- Malware Detection Functions ---
def get_file_type(file_content):
    signatures = {
        b'\x7fELF': 'application/x-executable',
        b'MZ': 'application/x-dosexec',
        b'\xfe\xed\xfa': 'application/x-mach-binary',
        b'\xce\xfa\xed\xfe': 'application/x-mach-binary',
        b'PK': 'application/zip',
        b'Rar!': 'application/x-rar',
    }
    for signature, mime_type in signatures.items():
        if file_content.startswith(signature):
            return mime_type
    return 'application/octet-stream'

def is_suspicious_file(file_content, file_name):
    file_lower = file_name.lower()
    suspicious_extensions = ['.exe', '.dll', '.bat', '.cmd', '.scr', '.com', '.pif', '.application', '.gadget',
                            '.msi', '.msp', '.com', '.scr', '.hta', '.cpl', '.msc', '.jar', '.bin', '.deb', '.rpm',
                            '.apk', '.app', '.dmg', '.iso', '.img']
    if any(file_lower.endswith(ext) for ext in suspicious_extensions):
        return True, f"Suspicious file extension: {file_name}"
    for signature in MALWARE_SIGNATURES:
        if file_content.startswith(signature):
            return True, f"Malware signature detected: {signature}"
    sample_size = min(len(file_content), 4096)
    file_sample = file_content[:sample_size]
    for indicator in ENCRYPTED_FILE_INDICATORS:
        if indicator in file_sample:
            return True, f"Encrypted file indicator: {indicator.decode('utf-8', errors='ignore')}"
    sample_text = file_sample.decode('utf-8', errors='ignore').lower()
    for keyword in SUSPICIOUS_KEYWORDS:
        if keyword.decode('utf-8').lower() in sample_text:
            return True, f"Suspicious keyword found: {keyword.decode('utf-8')}"
    try:
        file_type = get_file_type(file_sample)
        if file_type in ['application/x-dosexec', 'application/x-executable', 'application/x-mach-binary']:
            return True, f"Executable file type detected: {file_type}"
    except Exception as e:
        logger.warning(f"Could not determine file type: {e}")
    return False, "File appears safe"

def scan_file_for_malware(file_content, file_name, user_id):
    if user_id == OWNER_ID:
        return True, "Owner bypassed security check"
    is_suspicious, reason = is_suspicious_file(file_content, file_name)
    if is_suspicious:
        logger.warning(f"🚨 Malware detected in {file_name} from user {user_id}: {reason}")
        return False, f"Security violation: {reason}"
    return True, "File passed security check"

# --- Helper Functions ---
def sanitize_folder_name(name):
    """Sanitize folder name to be safe for filesystem"""
    if not name:
        return None
    # Remove path separators and dangerous chars
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = name.strip().strip('.')
    # Limit length
    name = name[:50]
    return name if name else None

def get_user_root_folder(user_id):
    """Get user's root folder (where all their script folders live)"""
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_folder(user_id, folder_name):
    """Get or create a specific folder inside user's root folder"""
    user_root = get_user_root_folder(user_id)
    safe_name = sanitize_folder_name(folder_name)
    if not safe_name:
        raise ValueError(f"Invalid folder name: {folder_name}")
    folder_path = os.path.join(user_root, safe_name)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def get_user_file_limit(user_id):
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in admin_ids: return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def make_script_key(user_id, folder_name, file_name):
    """Create unique script key: user_id::folder_name::file_name"""
    return f"{user_id}::{folder_name}::{file_name}"

def parse_script_key(script_key):
    """Parse script_key into (user_id, folder_name, file_name)"""
    parts = script_key.split('::', 2)
    if len(parts) != 3:
        return None, None, None
    return int(parts[0]), parts[1], parts[2]

def is_bot_running(script_owner_id, folder_name, file_name):
    """Check if a bot script is currently running for a specific user/folder/file"""
    script_key = make_script_key(script_owner_id, folder_name, file_name)
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running:
                logger.warning(f"Process for {script_key} not running/zombie. Cleaning up.")
                if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                    try: script_info['log_file'].close()
                    except Exception as log_e: logger.error(f"Error closing log: {log_e}")
                if script_key in bot_scripts: del bot_scripts[script_key]
            return is_running
        except psutil.NoSuchProcess:
            logger.warning(f"Process for {script_key} not found. Cleaning up.")
            if 'log_file' in script_info and hasattr(script_info['log_file'], 'close') and not script_info['log_file'].closed:
                try: script_info['log_file'].close()
                except Exception as log_e: logger.error(f"Error closing log: {log_e}")
            if script_key in bot_scripts: del bot_scripts[script_key]
            return False
        except Exception as e:
            logger.error(f"Error checking process status for {script_key}: {e}", exc_info=True)
            return False
    return False

def kill_process_tree(process_info):
    pid = None
    log_file_closed = False
    script_key = process_info.get('script_key', 'N/A')
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close') and not process_info['log_file'].closed:
            try:
                process_info['log_file'].close()
                log_file_closed = True
                logger.info(f"Closed log file for {script_key}")
            except Exception as log_e:
                logger.error(f"Error closing log file for {script_key}: {log_e}")

        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    logger.info(f"Killing process tree for {script_key} (PID: {pid})")
                    for child in children:
                        try: child.terminate()
                        except psutil.NoSuchProcess: pass
                        except Exception:
                            try: child.kill()
                            except Exception: pass
                    gone, alive = psutil.wait_procs(children, timeout=1)
                    for p in alive:
                        try: p.kill()
                        except Exception: pass
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired:
                            parent.kill()
                    except psutil.NoSuchProcess: pass
                    except Exception:
                        try: parent.kill()
                        except Exception: pass
                except psutil.NoSuchProcess:
                    logger.warning(f"Process {pid} for {script_key} already gone.")
            else:
                logger.error(f"Process PID is None for {script_key}.")
        elif log_file_closed:
            logger.warning(f"Process missing for {script_key}, but log closed.")
        else:
            logger.error(f"Process missing for {script_key}.")
    except Exception as e:
        logger.error(f"❌ Unexpected error killing process tree for {script_key}: {e}", exc_info=True)

# --- Automatic Package Installation ---
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI', 'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot', 'aiogram': 'aiogram',
    'pyrogram': 'pyrogram', 'telethon': 'telethon', 'telethon.sync': 'telethon',
    'telepot': 'telepot', 'tgcrypto': 'tgcrypto',
    'bs4': 'beautifulsoup4', 'requests': 'requests', 'pillow': 'Pillow',
    'cv2': 'opencv-python', 'yaml': 'PyYAML', 'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil', 'pandas': 'pandas', 'numpy': 'numpy',
    'flask': 'Flask', 'django': 'Django', 'sqlalchemy': 'SQLAlchemy',
    'psutil': 'psutil', 'PIL': 'Pillow',
}

def attempt_install_pip(module_name, message):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if module_name.lower() == "pil": package_name = "pillow"
    if package_name is None:
        logger.info(f"Module '{module_name}' is core. Skipping pip install.")
        return False
    try:
        bot.reply_to(message, f"🐍 Module `{module_name}` not found. Installing `{package_name}`...", parse_mode='Markdown')
        command = [sys.executable, '-m', 'pip', 'install', package_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                encoding='utf-8', errors='replace',
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if result.returncode == 0:
            logger.info(f"Installed {package_name}.")
            bot.reply_to(message, f"✅ Package `{package_name}` installed.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install `{package_name}`.\n```\n{result.stderr or result.stdout}\n```"
            logger.error(error_msg)
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except Exception as e:
        logger.error(f"Error installing {package_name}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error installing `{package_name}`: {str(e)}")
        return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"🟠 Node package `{module_name}` not found. Installing locally...", parse_mode='Markdown')
        command = ['npm', 'install', module_name]
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                cwd=user_folder, encoding='utf-8', errors='replace')
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Node package `{module_name}` installed.", parse_mode='Markdown')
            return True
        else:
            error_msg = f"❌ Failed to install Node package `{module_name}`.\n```\n{result.stderr or result.stdout}\n```"
            logger.error(error_msg)
            if len(error_msg) > 4000: error_msg = error_msg[:4000] + "\n... (truncated)"
            bot.reply_to(message, error_msg, parse_mode='Markdown')
            return False
    except FileNotFoundError:
        bot.reply_to(message, "❌ 'npm' not found. Ensure Node.js/npm installed.")
        return False
    except Exception as e:
        logger.error(f"Error installing npm {module_name}: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error installing Node `{module_name}`: {str(e)}")
        return False

def run_script(script_path, script_owner_id, folder_name, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return

    script_key = make_script_key(script_owner_id, folder_name, file_name)
    logger.info(f"Attempt {attempt} to run Python: {script_path} (Key: {script_key})")

    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj_for_reply, f"❌ Script '{file_name}' not found!")
            logger.error(f"Script not found: {script_path}")
            remove_user_file_db(script_owner_id, folder_name, file_name)
            return

        if attempt == 1:
            check_command = [sys.executable, script_path]
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding='utf-8', errors='replace',
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                logger.info(f"Python Pre-check RC: {return_code} | STDERR: {stderr[:200]}")

                if return_code != 0 and stderr:
                    match_py = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match_py:
                        module_name = match_py.group(1).strip()
                        if attempt_install_pip(module_name, message_obj_for_reply):
                            bot.reply_to(message_obj_for_reply, f"🔄 Retrying '{file_name}'...")
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(
                                script_path, script_owner_id, folder_name, user_folder,
                                file_name, message_obj_for_reply, attempt + 1)).start()
                            return
                        else:
                            bot.reply_to(message_obj_for_reply, f"❌ Install failed for '{module_name}'")
                            return
                    else:
                        error_summary = stderr[:500]
                        bot.reply_to(message_obj_for_reply,
                                     f"❌ Error in pre-check:\n```{error_summary}```", parse_mode='Markdown')
                        return
            except subprocess.TimeoutExpired:
                logger.info("Pre-check timeout -> imports likely OK")
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()
            except FileNotFoundError:
                bot.reply_to(message_obj_for_reply, "❌ Python interpreter not found.")
                return
            except Exception as e:
                logger.error(f"Pre-check error: {e}", exc_info=True)
                bot.reply_to(message_obj_for_reply, f"❌ Pre-check error:\n{e}")
                return
            finally:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()

        # Start long run
        logger.info(f"Starting long-running Python process for {script_key}")
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None
        process = None
        try:
            log_file = open(log_file_path, 'w', encoding='utf-8', errors='replace')
        except Exception as e:
            bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file:\n{e}")
            return

        try:
            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            process = subprocess.Popen([sys.executable, script_path], cwd=user_folder,
                stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                startupinfo=startupinfo, creationflags=creationflags,
                text=True, encoding='utf-8', errors='replace',
                env={**os.environ, "PYTHONIOENCODING": "utf-8"})

            logger.info(f"Started Python process {process.pid} for {script_key}")
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'folder_name': folder_name, 'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id, 'start_time': datetime.now(),
                'user_folder': user_folder, 'type': 'py', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply,
                         f"✅ Python script '{file_name}' started!\n📁 Folder: `{folder_name}`\n🆔 PID: {process.pid}",
                         parse_mode='Markdown')
        except FileNotFoundError:
            bot.reply_to(message_obj_for_reply, "❌ Python interpreter not found.")
            if log_file and not log_file.closed: log_file.close()
            if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            logger.error(f"Error starting script: {e}", exc_info=True)
            bot.reply_to(message_obj_for_reply, f"❌ Failed to start script:\n{e}")
            if process and process.poll() is None:
                kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        logger.error(f"Unexpected run_script error: {e}", exc_info=True)
        bot.reply_to(message_obj_for_reply, f"❌ Unexpected error:\n{e}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

def run_js_script(script_path, script_owner_id, folder_name, user_folder, file_name, message_obj_for_reply, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj_for_reply, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return

    script_key = make_script_key(script_owner_id, folder_name, file_name)
    logger.info(f"Attempt {attempt} to run JS: {script_path} (Key: {script_key})")

    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj_for_reply, f"❌ Script '{file_name}' not found!")
            remove_user_file_db(script_owner_id, folder_name, file_name)
            return

        if attempt == 1:
            check_command = ['node', script_path]
            check_proc = None
            try:
                check_proc = subprocess.Popen(check_command, cwd=user_folder,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    encoding='utf-8', errors='replace',
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                stdout, stderr = check_proc.communicate(timeout=5)
                return_code = check_proc.returncode
                logger.info(f"JS Pre-check RC: {return_code} | STDERR: {stderr[:200]}")

                if return_code != 0 and stderr:
                    match_js = re.search(r"Cannot find module '(.+?)'", stderr)
                    if match_js:
                        module_name = match_js.group(1).strip()
                        if not module_name.startswith('.') and not module_name.startswith('/'):
                            if attempt_install_npm(module_name, user_folder, message_obj_for_reply):
                                bot.reply_to(message_obj_for_reply, f"🔄 Retrying '{file_name}'...")
                                time.sleep(2)
                                threading.Thread(target=run_js_script, args=(
                                    script_path, script_owner_id, folder_name, user_folder,
                                    file_name, message_obj_for_reply, attempt + 1)).start()
                                return
                            else:
                                bot.reply_to(message_obj_for_reply, f"❌ Failed to install '{module_name}'")
                                return
                    error_summary = stderr[:500]
                    bot.reply_to(message_obj_for_reply,
                                 f"❌ JS Script Error:\n```{error_summary}```", parse_mode='Markdown')
                    return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()
            except FileNotFoundError:
                bot.reply_to(message_obj_for_reply, "❌ Node.js not installed.")
                return
            except Exception as e:
                logger.error(f"JS pre-check error: {e}", exc_info=True)
                bot.reply_to(message_obj_for_reply, f"❌ JS pre-check error:\n{e}")
                return
            finally:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill(); check_proc.communicate()

        logger.info(f"Starting long-running JS process for {script_key}")
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = None
        process = None
        try:
            log_file = open(log_file_path, 'w', encoding='utf-8', errors='replace')
        except Exception as e:
            bot.reply_to(message_obj_for_reply, f"❌ Failed to open log file:\n{e}")
            return

        try:
            startupinfo = None
            creationflags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            process = subprocess.Popen(['node', script_path], cwd=user_folder,
                stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                startupinfo=startupinfo, creationflags=creationflags,
                text=True, encoding='utf-8', errors='replace',
                env={**os.environ, "PYTHONIOENCODING": "utf-8"})

            logger.info(f"Started JS process {process.pid} for {script_key}")
            bot_scripts[script_key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'folder_name': folder_name, 'chat_id': message_obj_for_reply.chat.id,
                'script_owner_id': script_owner_id, 'start_time': datetime.now(),
                'user_folder': user_folder, 'type': 'js', 'script_key': script_key
            }
            bot.reply_to(message_obj_for_reply,
                         f"✅ JS script '{file_name}' started!\n📁 Folder: `{folder_name}`\n🆔 PID: {process.pid}",
                         parse_mode='Markdown')
        except FileNotFoundError:
            bot.reply_to(message_obj_for_reply, "❌ Node.js not installed.")
            if log_file and not log_file.closed: log_file.close()
            if script_key in bot_scripts: del bot_scripts[script_key]
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            logger.error(f"Error starting JS script: {e}", exc_info=True)
            bot.reply_to(message_obj_for_reply, f"❌ Failed to start JS script:\n{e}")
            if process and process.poll() is None:
                kill_process_tree({'process': process, 'log_file': log_file, 'script_key': script_key})
            if script_key in bot_scripts: del bot_scripts[script_key]
    except Exception as e:
        logger.error(f"Unexpected run_js_script error: {e}", exc_info=True)
        bot.reply_to(message_obj_for_reply, f"❌ Unexpected JS error:\n{e}")
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

# --- Database Operations ---
DB_LOCK = threading.Lock()

def save_user_file(user_id, folder_name, file_name, file_type='py'):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, folder_name, file_name, file_type) VALUES (?, ?, ?, ?)',
                      (user_id, folder_name, file_name, file_type))
            conn.commit()
            if user_id not in user_files: user_files[user_id] = []
            # Remove old entry with same folder+file, then append
            user_files[user_id] = [t for t in user_files[user_id] if not (t[0] == folder_name and t[1] == file_name)]
            user_files[user_id].append((folder_name, file_name, file_type))
            logger.info(f"Saved file '{folder_name}/{file_name}' ({file_type}) for user {user_id}")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error saving file: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error saving file: {e}", exc_info=True)
        finally: conn.close()

def remove_user_file_db(user_id, folder_name, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id = ? AND folder_name = ? AND file_name = ?',
                      (user_id, folder_name, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [t for t in user_files[user_id] if not (t[0] == folder_name and t[1] == file_name)]
                if not user_files[user_id]: del user_files[user_id]
            logger.info(f"Removed file '{folder_name}/{file_name}' for user {user_id} from DB")
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing file: {e}")
        except Exception as e: logger.error(f"❌ Unexpected error removing file: {e}", exc_info=True)
        finally: conn.close()

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except sqlite3.Error as e: logger.error(f"❌ SQLite error adding active user: {e}")
        finally: conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
                      (user_id, expiry.isoformat()))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
        except sqlite3.Error as e: logger.error(f"❌ SQLite error saving subscription: {e}")
        finally: conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            if user_id in user_subscriptions: del user_subscriptions[user_id]
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing subscription: {e}")
        finally: conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id)
        except sqlite3.Error as e: logger.error(f"❌ SQLite error adding admin: {e}")
        finally: conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID: return False
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        removed = False
        try:
            c.execute('SELECT 1 FROM admins WHERE user_id = ?', (admin_id,))
            if c.fetchone():
                c.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
                conn.commit()
                removed = c.rowcount > 0
                if removed: admin_ids.discard(admin_id)
            else:
                admin_ids.discard(admin_id)
            return removed
        except sqlite3.Error as e: logger.error(f"❌ SQLite error removing admin: {e}"); return False
        finally: conn.close()
# --- End Database Operations ---

# --- Menu creation ---
def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
        types.InlineKeyboardButton('📂 Check Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
        types.InlineKeyboardButton('📤 Send Command', callback_data='send_command'),
        types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot',
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts')
        ]
        markup.add(buttons[0]); markup.add(buttons[1], buttons[2])
        markup.add(buttons[3], admin_buttons[0]); markup.add(admin_buttons[1], admin_buttons[3])
        markup.add(admin_buttons[2], admin_buttons[5]); markup.add(buttons[4])
        markup.add(admin_buttons[4]); markup.add(buttons[5])
    else:
        markup.add(buttons[0]); markup.add(buttons[1], buttons[2])
        markup.add(buttons[3]); markup.add(buttons[4])
        markup.add(types.InlineKeyboardButton('📊 Statistics', callback_data='stats'))
        markup.add(buttons[5])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout_to_use = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row_buttons_text in layout_to_use:
        markup.add(*[types.KeyboardButton(text) for text in row_buttons_text])
    return markup

def create_control_buttons(script_owner_id, folder_name, file_name, is_running=True):
    """Buttons for controlling a script. callback_data format: action_ownerid_folderName_fileName"""
    # We need to encode folder_name safely - use a separator that won't appear in sanitized names
    # Sanitized names only contain [a-zA-Z0-9_.-], so we can use '|' as separator
    safe_folder = folder_name.replace('|', '_')
    safe_file = file_name.replace('|', '_')
    base = f"{script_owner_id}|{safe_folder}|{safe_file}"
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{base}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{base}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{base}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{base}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{base}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{base}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{base}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_send_command_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('📝 Send to Process', callback_data='send_to_process'),
        types.InlineKeyboardButton('🔍 View All Logs', callback_data='view_all_logs')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup
# --- End Menu Creation ---

# --- Pending Upload Flow: ask folder name ---
def ask_folder_name(user_id, chat_id, file_name, file_type, content, message_obj, is_zip=False):
    """Store the uploaded file temporarily and ask for folder name."""
    pending_uploads[user_id] = {
        'file_name': file_name,
        'file_type': file_type,
        'content': content,
        'message': message_obj,
        'is_zip': is_zip,
        'chat_id': chat_id,
    }
    msg = bot.send_message(chat_id,
        f"📁 File received: `{file_name}`\n\n"
        f"अब इस फाइल के लिए **फोल्डर का नाम** भेजें।\n"
        f"(उदाहरण: `mybot`, `project1`)\n\n"
        f"फोल्डर का नाम सिर्फ letters, numbers, `_`, `-`, `.` हो सकते हैं।\n"
        f"/cancel से रद्द करें।",
        parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_folder_name)

def process_folder_name(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id not in pending_uploads:
        bot.reply_to(message, "⚠️ कोई pending upload नहीं है। कृपया फाइल दोबारा अपलोड करें।")
        return

    if not message.text:
        bot.reply_to(message, "⚠️ कृपया फोल्डर का नाम text में भेजें, या /cancel करें।")
        msg = bot.send_message(chat_id, "📁 फोल्डर का नाम भेजें (या /cancel):")
        bot.register_next_step_handler(msg, process_folder_name)
        return

    if message.text.strip().lower() == '/cancel':
        del pending_uploads[user_id]
        bot.reply_to(message, "❌ Upload रद्द कर दिया गया।")
        return

    raw_name = message.text.strip()
    safe_name = sanitize_folder_name(raw_name)
    if not safe_name:
        bot.reply_to(message, "⚠️ अमान्य फोल्डर नाम! कृपया कोई और नाम भेजें (letters/numbers/_-.):")
        msg = bot.send_message(chat_id, "📁 फोल्डर का नाम भेजें (या /cancel):")
        bot.register_next_step_handler(msg, process_folder_name)
        return

    # Valid folder name - proceed to save and host
    pending = pending_uploads.pop(user_id)
    file_name = pending['file_name']
    file_type = pending['file_type']
    content = pending['content']
    original_msg = pending['message']
    is_zip = pending['is_zip']

    bot.reply_to(message, f"✅ फोल्डर नाम: `{safe_name}`\n⏳ फाइल को host किया जा रहा है...", parse_mode='Markdown')

    if is_zip:
        process_zip_upload(content, file_name, original_msg, safe_name)
    else:
        process_single_file_upload(content, file_name, file_type, original_msg, safe_name)

def process_single_file_upload(content, file_name, file_type, message_obj, folder_name):
    user_id = message_obj.from_user.id
    try:
        user_folder = get_user_folder(user_id, folder_name)
        file_path = os.path.join(user_folder, file_name)
        with open(file_path, 'wb') as f:
            f.write(content)
        logger.info(f"Saved file to {file_path}")
        save_user_file(user_id, folder_name, file_name, file_type)

        if file_type == 'py':
            threading.Thread(target=run_script, args=(
                file_path, user_id, folder_name, user_folder, file_name, message_obj)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(
                file_path, user_id, folder_name, user_folder, file_name, message_obj)).start()
    except Exception as e:
        logger.error(f"❌ Error processing file {file_name} for {user_id}: {e}", exc_info=True)
        bot.reply_to(message_obj, f"❌ Error processing file: {str(e)}")

def process_zip_upload(content, file_name_zip, message_obj, folder_name):
    user_id = message_obj.from_user.id
    temp_dir = None
    try:
        user_folder = get_user_folder(user_id, folder_name)

        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        logger.info(f"Temp dir for zip: {temp_dir}")
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as new_file:
            new_file.write(content)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Security check
            if user_id != OWNER_ID:
                for member in zip_ref.infolist():
                    member_name_lower = member.filename.lower()
                    suspicious_extensions = ['.exe', '.dll', '.bat', '.cmd', '.scr', '.com']
                    if any(member_name_lower.endswith(ext) for ext in suspicious_extensions):
                        bot.reply_to(message_obj, f"🚨 Security Alert: ZIP contains suspicious file: {member.filename}")
                        return
                    member_path = os.path.abspath(os.path.join(temp_dir, member.filename))
                    if not member_path.startswith(os.path.abspath(temp_dir)):
                        raise zipfile.BadZipFile(f"Zip has unsafe path: {member.filename}")
            zip_ref.extractall(temp_dir)
            logger.info(f"Extracted zip to {temp_dir}")

        # Find script in root or flatten subdirectory
        target_dir = temp_dir
        root_files = os.listdir(target_dir)
        if not any(f.endswith(('.py', '.js')) for f in root_files):
            for root, dirs, files in os.walk(temp_dir):
                dirs[:] = [d for d in dirs if not d.startswith('.') and not d.startswith('__')]
                if any(f.endswith(('.py', '.js')) for f in files):
                    target_dir = root
                    break
        if target_dir != temp_dir:
            logger.info(f"Flattening extracted files from {target_dir} to {temp_dir}")
            for item in os.listdir(target_dir):
                s = os.path.join(target_dir, item)
                d = os.path.join(temp_dir, item)
                if os.path.exists(d):
                    if os.path.isdir(d): shutil.rmtree(d)
                    else: os.remove(d)
                shutil.move(s, d)
            extracted_items = os.listdir(temp_dir)
        else:
            extracted_items = root_files

        py_files = [f for f in extracted_items if f.endswith('.py')]
        js_files = [f for f in extracted_items if f.endswith('.js')]
        req_file = 'requirements.txt' if 'requirements.txt' in extracted_items else None
        pkg_json = 'package.json' if 'package.json' in extracted_items else None

        if req_file:
            req_path = os.path.join(temp_dir, req_file)
            bot.reply_to(message_obj, f"🔄 Installing Python deps from `{req_file}`...")
            try:
                command = [sys.executable, '-m', 'pip', 'install', '-r', req_path]
                result = subprocess.run(command, capture_output=True, text=True, check=True,
                                        encoding='utf-8', errors='ignore')
                bot.reply_to(message_obj, f"✅ Python deps installed.")
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install Python deps.\n```\n{e.stderr or e.stdout}\n```"
                if len(error_msg) > 4000: error_msg = error_msg[:4000]
                bot.reply_to(message_obj, error_msg, parse_mode='Markdown'); return

        if pkg_json:
            bot.reply_to(message_obj, f"🔄 Installing Node deps...")
            try:
                command = ['npm', 'install']
                subprocess.run(command, capture_output=True, text=True, check=True, cwd=temp_dir,
                               encoding='utf-8', errors='ignore')
                bot.reply_to(message_obj, f"✅ Node deps installed.")
            except FileNotFoundError:
                bot.reply_to(message_obj, "❌ 'npm' not found."); return
            except subprocess.CalledProcessError as e:
                error_msg = f"❌ Failed to install Node deps.\n```\n{e.stderr or e.stdout}\n```"
                if len(error_msg) > 4000: error_msg = error_msg[:4000]
                bot.reply_to(message_obj, error_msg, parse_mode='Markdown'); return

        main_script_name = None; file_type = None
        for p in ['main.py', 'bot.py', 'app.py']:
            if p in py_files: main_script_name = p; file_type = 'py'; break
        if not main_script_name:
            for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                if p in js_files: main_script_name = p; file_type = 'js'; break
        if not main_script_name:
            if py_files: main_script_name = py_files[0]; file_type = 'py'
            elif js_files: main_script_name = js_files[0]; file_type = 'js'
        if not main_script_name:
            bot.reply_to(message_obj, "❌ No `.py` or `.js` script found in archive!"); return

        logger.info(f"Moving extracted files from {temp_dir} to {user_folder}")
        moved_count = 0
        for item_name in os.listdir(temp_dir):
            if item_name == file_name_zip: continue
            src_path = os.path.join(temp_dir, item_name)
            dest_path = os.path.join(user_folder, item_name)
            if os.path.isdir(dest_path): shutil.rmtree(dest_path)
            elif os.path.exists(dest_path): os.remove(dest_path)
            shutil.move(src_path, dest_path); moved_count += 1

        save_user_file(user_id, folder_name, main_script_name, file_type)
        bot.reply_to(message_obj,
                     f"✅ Files extracted to folder `{folder_name}`. Starting `{main_script_name}`...",
                     parse_mode='Markdown')
        main_script_path = os.path.join(user_folder, main_script_name)
        if file_type == 'py':
            threading.Thread(target=run_script, args=(
                main_script_path, user_id, folder_name, user_folder, main_script_name, message_obj)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(
                main_script_path, user_id, folder_name, user_folder, main_script_name, message_obj)).start()

    except zipfile.BadZipFile as e:
        bot.reply_to(message_obj, f"❌ Error: Invalid/corrupted ZIP. {e}")
    except Exception as e:
        logger.error(f"❌ Error processing zip: {e}", exc_info=True)
        bot.reply_to(message_obj, f"❌ Error processing zip: {str(e)}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir)
            except Exception as e: logger.error(f"Failed to clean temp dir {temp_dir}: {e}")

# --- Logic Functions ---
def _logic_send_welcome(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_name = message.from_user.first_name
    user_username = message.from_user.username

    if user_id not in admin_ids:
        if not is_user_joined_all(user_id):
            send_force_join_msg(chat_id)
            return

    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked by admin. Try later.")
        return

    user_bio = "Could not fetch bio"; photo_file_id = None
    try: user_bio = bot.get_chat(user_id).bio or "No bio"
    except Exception: pass
    try:
        user_profile_photos = bot.get_user_profile_photos(user_id, limit=1)
        if user_profile_photos.photos: photo_file_id = user_profile_photos.photos[0][-1].file_id
    except Exception: pass

    if user_id not in active_users:
        add_active_user(user_id)
        try:
            owner_notification = (f"🎉 New user!\n👤 Name: {user_name}\n✳️ User: @{user_username or 'N/A'}\n"
                                  f"🆔 ID: `{user_id}`\n📝 Bio: {user_bio}")
            bot.send_message(OWNER_ID, owner_notification, parse_mode='Markdown')
            if photo_file_id: bot.send_photo(OWNER_ID, photo_file_id, caption=f"Pic of new user {user_id}")
        except Exception as e: logger.error(f"⚠️ Failed to notify owner: {e}")

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    if user_id == OWNER_ID: user_status = "🤍 Owner"
    elif user_id in admin_ids: user_status = "🌙 Admin"
    elif user_id in user_subscriptions:
        expiry_date = user_subscriptions[user_id].get('expiry')
        if expiry_date and expiry_date > datetime.now():
            user_status = "⭐ Premium"; days_left = (expiry_date - datetime.now()).days
            expiry_info = f"\n⏳ Subscription expires in: {days_left} days"
        else: user_status = "🆓 Free User (Expired Sub)"; remove_subscription_db(user_id)
    else: user_status = "🆓 Free User"

    welcome_msg_text = (f"〽️ Welcome, {user_name}!\n\n🆔 Your User ID: `{user_id}`\n"
                        f"✳️ Username: `@{user_username or 'Not set'}`\n"
                        f"🔰 Your Status: {user_status}{expiry_info}\n"
                        f"📁 Files Uploaded: {current_files} / {limit_str}\n\n"
                        f"🤖 Host & run Python (`.py`) or JS (`.js`) scripts.\n"
                        f"   Upload single scripts or `.zip` archives.\n"
                        f"   📁 हर फाइल के लिए फोल्डर नाम पूछा जाएगा।\n\n"
                        f"👇 Use buttons or type commands.")
    main_reply_markup = create_reply_keyboard_main_menu(user_id)
    try:
        if photo_file_id: bot.send_photo(chat_id, photo_file_id)
        bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error sending welcome: {e}", exc_info=True)
        try: bot.send_message(chat_id, welcome_msg_text, reply_markup=main_reply_markup, parse_mode='Markdown')
        except Exception as fallback_e: logger.error(f"Fallback failed: {fallback_e}")

def _logic_updates_channel(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL))
    bot.reply_to(message, "Visit our Updates Channel:", reply_markup=markup)

def _logic_upload_file(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin, cannot accept files.")
        return
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached. Delete files first.")
        return
    bot.reply_to(message, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.\n"
                          "फाइल भेजने के बाद फोल्डर का नाम पूछा जाएगा।")

def _logic_check_files(message):
    user_id = message.from_user.id
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.reply_to(message, "📂 Your files:\n\n(No files uploaded yet)")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    # Group by folder for display
    by_folder = {}
    for folder_name, file_name, file_type in sorted(user_files_list):
        by_folder.setdefault(folder_name, []).append((file_name, file_type))
    for folder_name, files in sorted(by_folder.items()):
        for file_name, file_type in files:
            is_running = is_bot_running(user_id, folder_name, file_name)
            status_icon = "🟢 Running" if is_running else "🔴 Stopped"
            btn_text = f"📁{folder_name}/{file_name} ({file_type}) - {status_icon}"
            safe_folder = folder_name.replace('|', '_')
            safe_file = file_name.replace('|', '_')
            cb = f'file_{user_id}|{safe_folder}|{safe_file}'
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=cb))
    bot.reply_to(message, "📂 Your files:\nClick to manage.", reply_markup=markup, parse_mode='Markdown')

def _logic_bot_speed(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    start_time_ping = time.time()
    wait_msg = bot.reply_to(message, "🏃 Testing speed...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start_time_ping) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID: user_level = "🤍 Owner"
        elif user_id in admin_ids: user_level = "🌙 Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now(): user_level = "⭐ Premium"
        else: user_level = "🆓 Free User"
        speed_msg = (f"⚡ Bot Speed & Status:\n\n⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n👤 Your Level: {user_level}")
        bot.edit_message_text(speed_msg, chat_id, wait_msg.message_id)
    except Exception as e:
        logger.error(f"Error during speed test: {e}", exc_info=True)
        bot.edit_message_text("❌ Error during speed test.", chat_id, wait_msg.message_id)

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "Click to contact Owner:", reply_markup=markup)

def _logic_send_command(message):
    user_id = message.from_user.id
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked by admin.")
        return
    bot.reply_to(message, "📤 Send Command Options:", reply_markup=create_send_command_menu())

def _logic_subscriptions_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "💳 Subscription Management\nUse inline buttons.", reply_markup=create_subscription_menu())

def _logic_statistics(message):
    user_id = message.from_user.id
    total_users = len(active_users)
    total_files_records = sum(len(files) for files in user_files.values())
    running_bots_count = 0
    user_running_bots = 0
    for script_key_iter, script_info_iter in list(bot_scripts.items()):
        try:
            s_owner_id, s_folder, s_file = parse_script_key(script_key_iter)
            if s_owner_id is None: continue
            if is_bot_running(s_owner_id, s_folder, s_file):
                running_bots_count += 1
                if s_owner_id == user_id: user_running_bots += 1
        except Exception: pass
    stats_msg_base = (f"📊 Bot Statistics:\n\n👥 Total Users: {total_users}\n"
                      f"📂 Total File Records: {total_files_records}\n"
                      f"🟢 Total Active Bots: {running_bots_count}\n")
    if user_id in admin_ids:
        stats_msg = stats_msg_base + f"🔒 Bot Status: {'🔴 Locked' if bot_locked else '🟢 Unlocked'}\n🤖 Your Running Bots: {user_running_bots}"
    else:
        stats_msg = stats_msg_base + f"🤖 Your Running Bots: {user_running_bots}"
    bot.reply_to(message, stats_msg)

def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    msg = bot.reply_to(message, "📢 Send message to broadcast to all active users.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    global bot_locked
    bot_locked = not bot_locked
    status = "locked" if bot_locked else "unlocked"
    bot.reply_to(message, f"🔒 Bot has been {status}.")

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids:
        bot.reply_to(message, "⚠️ Admin permissions required.")
        return
    bot.reply_to(message, "👑 Admin Panel", reply_markup=create_admin_panel())

def _logic_run_all_scripts(message_or_call):
    if isinstance(message_or_call, telebot.types.Message):
        admin_user_id = message_or_call.from_user.id
        admin_chat_id = message_or_call.chat.id
        reply_func = lambda text, **kwargs: bot.reply_to(message_or_call, text, **kwargs)
        admin_message_obj_for_script_runner = message_or_call
    elif isinstance(message_or_call, telebot.types.CallbackQuery):
        admin_user_id = message_or_call.from_user.id
        admin_chat_id = message_or_call.message.chat.id
        bot.answer_callback_query(message_or_call.id)
        reply_func = lambda text, **kwargs: bot.send_message(admin_chat_id, text, **kwargs)
        admin_message_obj_for_script_runner = message_or_call.message
    else:
        return
    if admin_user_id not in admin_ids:
        reply_func("⚠️ Admin permissions required.")
        return
    reply_func("⏳ Starting all user scripts...")
    started_count = 0; attempted_users = 0; skipped_files = 0; error_files_details = []
    all_user_files_snapshot = dict(user_files)
    for target_user_id, files_for_user in all_user_files_snapshot.items():
        if not files_for_user: continue
        attempted_users += 1
        for folder_name, file_name, file_type in files_for_user:
            if not is_bot_running(target_user_id, folder_name, file_name):
                try:
                    user_folder = get_user_folder(target_user_id, folder_name)
                    file_path = os.path.join(user_folder, file_name)
                    if os.path.exists(file_path):
                        if file_type == 'py':
                            threading.Thread(target=run_script, args=(
                                file_path, target_user_id, folder_name, user_folder,
                                file_name, admin_message_obj_for_script_runner)).start()
                            started_count += 1
                        elif file_type == 'js':
                            threading.Thread(target=run_js_script, args=(
                                file_path, target_user_id, folder_name, user_folder,
                                file_name, admin_message_obj_for_script_runner)).start()
                            started_count += 1
                        else:
                            skipped_files += 1
                        time.sleep(0.7)
                    else:
                        error_files_details.append(f"`{folder_name}/{file_name}` (User {target_user_id}) - Not found")
                        skipped_files += 1
                except Exception as e:
                    logger.error(f"Error starting {folder_name}/{file_name}: {e}")
                    skipped_files += 1
    summary_msg = (f"✅ Run All Scripts Complete:\n\n▶️ Started: {started_count}\n"
                   f"👥 Users: {attempted_users}\n⚠️ Skipped: {skipped_files}")
    if error_files_details:
        summary_msg += "\nDetails (first 5):\n" + "\n".join([f"  - {e}" for e in error_files_details[:5]])
    reply_func(summary_msg, parse_mode='Markdown')

# --- Command Handlers ---
@bot.message_handler(commands=['start', 'help'])
def command_send_welcome(message): _logic_send_welcome(message)

@bot.message_handler(commands=['status'])
def command_show_status(message): _logic_statistics(message)

BUTTON_TEXT_TO_LOGIC = {
    "📢 Updates Channel": _logic_updates_channel,
    "📤 Upload File": _logic_upload_file,
    "📂 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "📤 Send Command": _logic_send_command,
    "📞 Contact Owner": _logic_contact_owner,
    "📊 Statistics": _logic_statistics,
    "💳 Subscriptions": _logic_subscriptions_panel,
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot,
    "🟢 Running All Code": _logic_run_all_scripts,
    "👑 Admin Panel": _logic_admin_panel,
}

@bot.message_handler(func=lambda message: message.text in BUTTON_TEXT_TO_LOGIC)
def handle_button_text(message):
    logic_func = BUTTON_TEXT_TO_LOGIC.get(message.text)
    if logic_func: logic_func(message)

@bot.message_handler(commands=['updateschannel'])
def command_updates_channel(message): _logic_updates_channel(message)
@bot.message_handler(commands=['uploadfile'])
def command_upload_file(message): _logic_upload_file(message)
@bot.message_handler(commands=['checkfiles'])
def command_check_files(message): _logic_check_files(message)
@bot.message_handler(commands=['botspeed'])
def command_bot_speed(message): _logic_bot_speed(message)
@bot.message_handler(commands=['sendcommand'])
def command_send_command(message): _logic_send_command(message)
@bot.message_handler(commands=['contactowner'])
def command_contact_owner(message): _logic_contact_owner(message)
@bot.message_handler(commands=['subscriptions'])
def command_subscriptions(message): _logic_subscriptions_panel(message)
@bot.message_handler(commands=['statistics'])
def command_statistics(message): _logic_statistics(message)
@bot.message_handler(commands=['broadcast'])
def command_broadcast(message): _logic_broadcast_init(message)
@bot.message_handler(commands=['lockbot'])
def command_lock_bot(message): _logic_toggle_lock_bot(message)
@bot.message_handler(commands=['adminpanel'])
def command_admin_panel(message): _logic_admin_panel(message)
@bot.message_handler(commands=['runningallcode'])
def command_run_all_code(message): _logic_run_all_scripts(message)

@bot.message_handler(commands=['ping'])
def ping(message):
    start_ping_time = time.time()
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start_ping_time) * 1000, 2)
    bot.edit_message_text(f"Pong! Latency: {latency} ms", message.chat.id, msg.message_id)

# --- Document (File) Handler ---
@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    doc = message.document
    logger.info(f"Doc from {user_id}: {doc.file_name}")

    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot locked, cannot accept files.")
        return

    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.reply_to(message, f"⚠️ File limit ({current_files}/{limit_str}) reached.")
        return

    file_name = doc.file_name
    if not file_name: bot.reply_to(message, "⚠️ No file name."); return
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Unsupported type! Only `.py`, `.js`, `.zip` allowed.")
        return
    max_file_size = 20 * 1024 * 1024
    if doc.file_size > max_file_size:
        bot.reply_to(message, f"⚠️ File too large (Max: {max_file_size // 1024 // 1024} MB)."); return

    try:
        try:
            bot.forward_message(OWNER_ID, chat_id, message.message_id)
            bot.send_message(OWNER_ID, f"⬆️ File '{file_name}' from {message.from_user.first_name} (`{user_id}`)", parse_mode='Markdown')
        except Exception as e: logger.error(f"Failed to forward to OWNER: {e}")

        download_wait_msg = bot.reply_to(message, f"⏳ Downloading `{file_name}`...")
        file_info_tg_doc = bot.get_file(doc.file_id)
        downloaded_file_content = bot.download_file(file_info_tg_doc.file_path)

        if user_id != OWNER_ID:
            is_safe, reason = scan_file_for_malware(downloaded_file_content, file_name, user_id)
            if not is_safe:
                bot.edit_message_text(f"🚨 Security Alert: {reason}", chat_id, download_wait_msg.message_id)
                return

        bot.edit_message_text(f"✅ Downloaded `{file_name}`.", chat_id, download_wait_msg.message_id)

        # Determine file_type
        if file_ext == '.py':
            file_type = 'py'
        elif file_ext == '.js':
            file_type = 'js'
        else:  # .zip
            file_type = 'zip'

        # Ask folder name - content stored in memory until folder name is given
        ask_folder_name(user_id, chat_id, file_name, file_type, downloaded_file_content, message,
                        is_zip=(file_type == 'zip'))
    except telebot.apihelper.ApiTelegramException as e:
        logger.error(f"Telegram API Error: {e}", exc_info=True)
        if "file is too big" in str(e).lower():
            bot.reply_to(message, f"❌ File too large (~20MB limit).")
        else: bot.reply_to(message, f"❌ Telegram API Error: {str(e)}.")
    except Exception as e:
        logger.error(f"❌ General error: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Unexpected error: {str(e)}")

# --- Callback Query Handlers ---
@bot.callback_query_handler(func=lambda call: call.data == "force_join_check")
def force_join_recheck(call):
    user_id = call.from_user.id
    if is_user_joined_all(user_id):
        bot.answer_callback_query(call.id, "✅ All channels verified!")
        _logic_send_welcome(call.message)
    else:
        bot.answer_callback_query(call.id, "❌ सभी channels join करो पहले", show_alert=True)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    logger.info(f"Callback: User={user_id}, Data='{data}'")

    if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats']:
        bot.answer_callback_query(call.id, "⚠️ Bot locked by admin.", show_alert=True)
        return
    try:
        if data == 'upload': upload_callback(call)
        elif data == 'check_files': check_files_callback(call)
        elif data.startswith('file_'): file_control_callback(call)
        elif data.startswith('start_'): start_bot_callback(call)
        elif data.startswith('stop_'): stop_bot_callback(call)
        elif data.startswith('restart_'): restart_bot_callback(call)
        elif data.startswith('delete_'): delete_bot_callback(call)
        elif data.startswith('logs_'): logs_bot_callback(call)
        elif data == 'speed': speed_callback(call)
        elif data == 'back_to_main': back_to_main_callback(call)
        elif data.startswith('confirm_broadcast_'): handle_confirm_broadcast(call)
        elif data == 'cancel_broadcast': handle_cancel_broadcast(call)
        elif data == 'send_command': send_command_callback(call)
        elif data == 'send_to_process': send_to_process_callback(call)
        elif data.startswith('sendcmd_select_'): sendcmd_select_callback(call)
        elif data == 'view_all_logs': view_all_logs_callback(call)
        elif data.startswith('viewlog_'): viewlog_callback(call)
        elif data == 'subscription': admin_required_callback(call, subscription_management_callback)
        elif data == 'stats': stats_callback(call)
        elif data == 'lock_bot': admin_required_callback(call, lock_bot_callback)
        elif data == 'unlock_bot': admin_required_callback(call, unlock_bot_callback)
        elif data == 'run_all_scripts': admin_required_callback(call, run_all_scripts_callback)
        elif data == 'broadcast': admin_required_callback(call, broadcast_init_callback)
        elif data == 'admin_panel': admin_required_callback(call, admin_panel_callback)
        elif data == 'add_admin': owner_required_callback(call, add_admin_init_callback)
        elif data == 'remove_admin': owner_required_callback(call, remove_admin_init_callback)
        elif data == 'list_admins': admin_required_callback(call, list_admins_callback)
        elif data == 'add_subscription': admin_required_callback(call, add_subscription_init_callback)
        elif data == 'remove_subscription': admin_required_callback(call, remove_subscription_init_callback)
        elif data == 'check_subscription': admin_required_callback(call, check_subscription_init_callback)
        else:
            bot.answer_callback_query(call.id, "Unknown action.")
    except Exception as e:
        logger.error(f"Error handling callback '{data}': {e}", exc_info=True)
        try: bot.answer_callback_query(call.id, "Error processing request.", show_alert=True)
        except Exception: pass

def admin_required_callback(call, func_to_run):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin permissions required.", show_alert=True)
        return
    func_to_run(call)

def owner_required_callback(call, func_to_run):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner permissions required.", show_alert=True)
        return
    func_to_run(call)

def parse_file_callback_data(data, prefix):
    """Parse data like 'prefix_ownerid|folder|file' -> (owner_id, folder, file)"""
    try:
        rest = data[len(prefix):]
        parts = rest.split('|', 2)
        if len(parts) != 3: return None, None, None
        return int(parts[0]), parts[1], parts[2]
    except Exception:
        return None, None, None

def send_command_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("📤 Send Command Options:", call.message.chat.id, call.message.message_id,
                              reply_markup=create_send_command_menu())
    except Exception as e: logger.error(f"Error: {e}")

def send_to_process_callback(call):
    bot.answer_callback_query(call.id)
    send_to_process_init(call.message)

def send_to_process_init(message):
    user_id = message.from_user.id
    user_running_scripts = []
    for script_key, script_info in bot_scripts.items():
        if script_info['script_owner_id'] == user_id or user_id in admin_ids:
            if is_bot_running(script_info['script_owner_id'], script_info['folder_name'], script_info['file_name']):
                user_running_scripts.append((script_key, script_info))
    if not user_running_scripts:
        bot.reply_to(message, "❌ No running scripts found.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for script_key, script_info in user_running_scripts:
        btn_text = f"📁{script_info['folder_name']}/{script_info['file_name']} (User: {script_info['script_owner_id']})"
        # encode script_key to safe callback data
        safe_key = script_key.replace('::', '^^')
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'sendcmd_select_{safe_key}'))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='send_command'))
    bot.reply_to(message, "📝 Select a running script to send command to:", reply_markup=markup)

def sendcmd_select_callback(call):
    try:
        safe_key = call.data.replace('sendcmd_select_', '')
        script_key = safe_key.replace('^^', '::')
        bot.answer_callback_query(call.id, f"Selected: {script_key}")
        msg = bot.send_message(call.message.chat.id, f"📝 Enter command to send to `{script_key}`:", parse_mode='Markdown')
        bot.register_next_step_handler(msg, lambda m: process_send_command(m, script_key))
    except Exception as e:
        logger.error(f"Error: {e}")
        bot.answer_callback_query(call.id, "Error.")

def process_send_command(message, script_key):
    user_id = message.from_user.id
    if script_key not in bot_scripts:
        bot.reply_to(message, "❌ Script no longer running.")
        return
    script_info = bot_scripts[script_key]
    command_text = message.text
    try:
        process = script_info['process']
        if process and process.poll() is None:
            process.stdin.write(command_text + '\n')
            process.stdin.flush()
            bot.reply_to(message, f"✅ Command sent to `{script_info['folder_name']}/{script_info['file_name']}`:\n`{command_text}`",
                         parse_mode='Markdown')
            time.sleep(1)
            if process.poll() is not None:
                bot.reply_to(message, f"⚠️ Script stopped after command.")
        else:
            bot.reply_to(message, f"❌ Script not running.")
    except Exception as e:
        logger.error(f"Error: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

def view_all_logs(message):
    user_id = message.from_user.id
    user_logs = []
    user_root = get_user_root_folder(user_id)
    if os.path.exists(user_root):
        for folder_name in os.listdir(user_root):
            folder_path = os.path.join(user_root, folder_name)
            if os.path.isdir(folder_path):
                for file in os.listdir(folder_path):
                    if file.endswith('.log'):
                        log_path = os.path.join(folder_path, file)
                        file_size = os.path.getsize(log_path)
                        user_logs.append((folder_name, file, file_size, log_path))
    if not user_logs:
        bot.reply_to(message, "📜 No log files found.")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for folder_name, log_file, size, log_path in sorted(user_logs):
        size_kb = size / 1024
        btn_text = f"📁{folder_name}/{log_file} ({size_kb:.1f} KB)"
        safe_folder = folder_name.replace('|', '_')
        safe_file = log_file.replace('|', '_')
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'viewlog_{user_id}|{safe_folder}|{safe_file}'))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='send_command'))
    bot.reply_to(message, "📜 Available Log Files:", reply_markup=markup)

def viewlog_callback(call):
    try:
        owner_id, folder_name, log_filename = parse_file_callback_data(call.data, 'viewlog_')
        if owner_id is None:
            bot.answer_callback_query(call.id, "Error parsing.")
            return
        requesting_user_id = call.from_user.id
        if not (requesting_user_id == owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ You can only view your own logs.", show_alert=True)
            return
        user_folder = get_user_folder(owner_id, folder_name)
        log_path = os.path.join(user_folder, log_filename)
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, "❌ Log not found.", show_alert=True)
            return
        bot.answer_callback_query(call.id, "📜 Sending log...")
        send_log_file(call.message, log_path, log_filename)
    except Exception as e:
        logger.error(f"Error: {e}")
        bot.answer_callback_query(call.id, "Error.")

def view_all_logs_callback(call):
    bot.answer_callback_query(call.id)
    view_all_logs(call.message)

def send_log_file(message, log_path, log_filename):
    try:
        file_size = os.path.getsize(log_path)
        if file_size > 50 * 1024 * 1024:
            bot.reply_to(message, f"❌ Log too large ({file_size/1024/1024:.1f} MB).")
            return
        with open(log_path, 'rb') as log_file:
            bot.send_document(message.chat.id, log_file, caption=f"📜 {log_filename}")
    except Exception as e:
        logger.error(f"Error sending log: {e}")
        bot.reply_to(message, f"❌ Error: {str(e)}")

def upload_callback(call):
    user_id = call.from_user.id
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
        bot.answer_callback_query(call.id, f"⚠️ File limit ({current_files}/{limit_str}) reached.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📤 Send your Python (`.py`), JS (`.js`), or ZIP (`.zip`) file.\nफाइल के बाद फोल्डर नाम पूछा जाएगा।")

def check_files_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.answer_callback_query(call.id, "⚠️ No files uploaded.", show_alert=True)
        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
            bot.edit_message_text("📂 Your files:\n\n(No files uploaded)", chat_id, call.message.message_id, reply_markup=markup)
        except Exception: pass
        return
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    by_folder = {}
    for folder_name, file_name, file_type in sorted(user_files_list):
        by_folder.setdefault(folder_name, []).append((file_name, file_type))
    for folder_name, files in sorted(by_folder.items()):
        for file_name, file_type in files:
            is_running = is_bot_running(user_id, folder_name, file_name)
            status_icon = "🟢 Running" if is_running else "🔴 Stopped"
            btn_text = f"📁{folder_name}/{file_name} ({file_type}) - {status_icon}"
            safe_folder = folder_name.replace('|', '_')
            safe_file = file_name.replace('|', '_')
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f'file_{user_id}|{safe_folder}|{safe_file}'))
    markup.add(types.InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main'))
    try:
        bot.edit_message_text("📂 Your files:\nClick to manage.", chat_id, call.message.message_id,
                              reply_markup=markup, parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" not in str(e):
            logger.error(f"Error editing msg: {e}")

def file_control_callback(call):
    try:
        script_owner_id, folder_name, file_name = parse_file_callback_data(call.data, 'file_')
        if script_owner_id is None:
            bot.answer_callback_query(call.id, "Error: Invalid data.", show_alert=True)
            return
        requesting_user_id = call.from_user.id
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ You can only manage your own files.", show_alert=True)
            check_files_callback(call)
            return
        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == folder_name and f[1] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True)
            check_files_callback(call)
            return
        bot.answer_callback_query(call.id)
        is_running = is_bot_running(script_owner_id, folder_name, file_name)
        status_text = '🟢 Running' if is_running else '🔴 Stopped'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{folder_name}/{file_name}` ({file_info[2]}) of User `{script_owner_id}`\nStatus: {status_text}",
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, folder_name, file_name, is_running),
                parse_mode='Markdown')
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e): raise
    except Exception as e:
        logger.error(f"Error in file_control_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "An error occurred.", show_alert=True)

def start_bot_callback(call):
    try:
        script_owner_id, folder_name, file_name = parse_file_callback_data(call.data, 'start_')
        if script_owner_id is None:
            bot.answer_callback_query(call.id, "Error: Invalid data.", show_alert=True)
            return
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return
        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == folder_name and f[1] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return
        file_type = file_info[2]
        user_folder = get_user_folder(script_owner_id, folder_name)
        file_path = os.path.join(user_folder, file_name)
        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ File missing! Re-upload.", show_alert=True)
            remove_user_file_db(script_owner_id, folder_name, file_name); check_files_callback(call); return
        if is_bot_running(script_owner_id, folder_name, file_name):
            bot.answer_callback_query(call.id, f"⚠️ Already running.", show_alert=True)
            try: bot.edit_message_reply_markup(chat_id_for_reply, call.message.message_id,
                                               reply_markup=create_control_buttons(script_owner_id, folder_name, file_name, True))
            except Exception: pass
            return
        bot.answer_callback_query(call.id, f"⏳ Starting {folder_name}/{file_name}...")
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, folder_name, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, folder_name, user_folder, file_name, call.message)).start()
        time.sleep(1.5)
        is_now_running = is_bot_running(script_owner_id, folder_name, file_name)
        status_text = '🟢 Running' if is_now_running else '🟡 Starting...'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{folder_name}/{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, folder_name, file_name, is_now_running),
                parse_mode='Markdown')
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e): raise
    except Exception as e:
        logger.error(f"Error in start_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error starting script.", show_alert=True)

def stop_bot_callback(call):
    try:
        script_owner_id, folder_name, file_name = parse_file_callback_data(call.data, 'stop_')
        if script_owner_id is None:
            bot.answer_callback_query(call.id, "Error: Invalid data.", show_alert=True); return
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return
        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == folder_name and f[1] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return
        file_type = file_info[2]
        script_key = make_script_key(script_owner_id, folder_name, file_name)
        if not is_bot_running(script_owner_id, folder_name, file_name):
            bot.answer_callback_query(call.id, f"⚠️ Already stopped.", show_alert=True)
            try: bot.edit_message_text(
                f"⚙️ Controls for: `{folder_name}/{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: 🔴 Stopped",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, folder_name, file_name, False), parse_mode='Markdown')
            except Exception: pass
            return
        bot.answer_callback_query(call.id, f"⏳ Stopping {folder_name}/{file_name}...")
        process_info = bot_scripts.get(script_key)
        if process_info:
            kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{folder_name}/{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: 🔴 Stopped",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, folder_name, file_name, False), parse_mode='Markdown')
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e): raise
    except Exception as e:
        logger.error(f"Error in stop_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error stopping script.", show_alert=True)

def restart_bot_callback(call):
    try:
        script_owner_id, folder_name, file_name = parse_file_callback_data(call.data, 'restart_')
        if script_owner_id is None:
            bot.answer_callback_query(call.id, "Error: Invalid data.", show_alert=True); return
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return
        user_files_list = user_files.get(script_owner_id, [])
        file_info = next((f for f in user_files_list if f[0] == folder_name and f[1] == file_name), None)
        if not file_info:
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return
        file_type = file_info[2]
        user_folder = get_user_folder(script_owner_id, folder_name)
        file_path = os.path.join(user_folder, file_name)
        script_key = make_script_key(script_owner_id, folder_name, file_name)
        if not os.path.exists(file_path):
            bot.answer_callback_query(call.id, f"⚠️ File missing! Re-upload.", show_alert=True)
            remove_user_file_db(script_owner_id, folder_name, file_name)
            if script_key in bot_scripts: del bot_scripts[script_key]
            check_files_callback(call); return
        bot.answer_callback_query(call.id, f"⏳ Restarting {folder_name}/{file_name}...")
        if is_bot_running(script_owner_id, folder_name, file_name):
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(1.5)
        if file_type == 'py':
            threading.Thread(target=run_script, args=(file_path, script_owner_id, folder_name, user_folder, file_name, call.message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(file_path, script_owner_id, folder_name, user_folder, file_name, call.message)).start()
        time.sleep(1.5)
        is_now_running = is_bot_running(script_owner_id, folder_name, file_name)
        status_text = '🟢 Running' if is_now_running else '🟡 Starting...'
        try:
            bot.edit_message_text(
                f"⚙️ Controls for: `{folder_name}/{file_name}` ({file_type}) of User `{script_owner_id}`\nStatus: {status_text}",
                chat_id_for_reply, call.message.message_id,
                reply_markup=create_control_buttons(script_owner_id, folder_name, file_name, is_now_running), parse_mode='Markdown')
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" not in str(e): raise
    except Exception as e:
        logger.error(f"Error in restart_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error restarting.", show_alert=True)

def delete_bot_callback(call):
    try:
        script_owner_id, folder_name, file_name = parse_file_callback_data(call.data, 'delete_')
        if script_owner_id is None:
            bot.answer_callback_query(call.id, "Error: Invalid data.", show_alert=True); return
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return
        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == folder_name and f[1] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return
        bot.answer_callback_query(call.id, f"🗑️ Deleting {folder_name}/{file_name}...")
        script_key = make_script_key(script_owner_id, folder_name, file_name)
        if is_bot_running(script_owner_id, folder_name, file_name):
            process_info = bot_scripts.get(script_key)
            if process_info: kill_process_tree(process_info)
            if script_key in bot_scripts: del bot_scripts[script_key]
            time.sleep(0.5)
        user_folder = get_user_folder(script_owner_id, folder_name)
        file_path = os.path.join(user_folder, file_name)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        deleted_disk = []
        if os.path.exists(file_path):
            try: os.remove(file_path); deleted_disk.append(file_name)
            except OSError as e: logger.error(f"Error deleting {file_path}: {e}")
        if os.path.exists(log_path):
            try: os.remove(log_path); deleted_disk.append(os.path.basename(log_path))
            except OSError as e: logger.error(f"Error deleting log {log_path}: {e}")
        remove_user_file_db(script_owner_id, folder_name, file_name)
        deleted_str = ", ".join(f"`{f}`" for f in deleted_disk) if deleted_disk else "associated files"
        try:
            bot.edit_message_text(f"🗑️ `{folder_name}/{file_name}` and {deleted_str} deleted!",
                                  chat_id_for_reply, call.message.message_id, reply_markup=None, parse_mode='Markdown')
        except Exception:
            bot.send_message(chat_id_for_reply, f"🗑️ `{folder_name}/{file_name}` deleted.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in delete_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error deleting.", show_alert=True)

def logs_bot_callback(call):
    try:
        script_owner_id, folder_name, file_name = parse_file_callback_data(call.data, 'logs_')
        if script_owner_id is None:
            bot.answer_callback_query(call.id, "Error: Invalid data.", show_alert=True); return
        requesting_user_id = call.from_user.id
        chat_id_for_reply = call.message.chat.id
        if not (requesting_user_id == script_owner_id or requesting_user_id in admin_ids):
            bot.answer_callback_query(call.id, "⚠️ Permission denied.", show_alert=True); return
        user_files_list = user_files.get(script_owner_id, [])
        if not any(f[0] == folder_name and f[1] == file_name for f in user_files_list):
            bot.answer_callback_query(call.id, "⚠️ File not found.", show_alert=True); check_files_callback(call); return
        user_folder = get_user_folder(script_owner_id, folder_name)
        log_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        if not os.path.exists(log_path):
            bot.answer_callback_query(call.id, f"⚠️ No logs for '{file_name}'.", show_alert=True); return
        bot.answer_callback_query(call.id)
        try:
            log_content = ""; file_size = os.path.getsize(log_path)
            max_log_kb = 100; max_tg_msg = 4096
            if file_size == 0: log_content = "(Log empty)"
            elif file_size > max_log_kb * 1024:
                with open(log_path, 'rb') as f: f.seek(-max_log_kb * 1024, os.SEEK_END); log_bytes = f.read()
                log_content = log_bytes.decode('utf-8', errors='ignore')
                log_content = f"(Last {max_log_kb} KB)\n...\n" + log_content
            else:
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f: log_content = f.read()
            if len(log_content) > max_tg_msg:
                log_content = log_content[-max_tg_msg:]
                first_nl = log_content.find('\n')
                if first_nl != -1: log_content = "...\n" + log_content[first_nl+1:]
                else: log_content = "...\n" + log_content
            if not log_content.strip(): log_content = "(No visible content)"
            bot.send_message(chat_id_for_reply,
                             f"📜 Logs for `{folder_name}/{file_name}` (User `{script_owner_id}`):\n```\n{log_content}\n```",
                             parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error reading log: {e}")
            bot.send_message(chat_id_for_reply, f"❌ Error reading log.")
    except Exception as e:
        logger.error(f"Error in logs_bot_callback: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error fetching logs.", show_alert=True)

def speed_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    start_cb_ping_time = time.time()
    try:
        bot.edit_message_text("🏃 Testing speed...", chat_id, call.message.message_id)
        bot.send_chat_action(chat_id, 'typing')
        response_time = round((time.time() - start_cb_ping_time) * 1000, 2)
        status = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if user_id == OWNER_ID: user_level = "🤍 Owner"
        elif user_id in admin_ids: user_level = "🌙 Admin"
        elif user_id in user_subscriptions and user_subscriptions[user_id].get('expiry', datetime.min) > datetime.now(): user_level = "⭐ Premium"
        else: user_level = "🆓 Free User"
        speed_msg = (f"⚡ Bot Speed & Status:\n\n⏱️ API Response Time: {response_time} ms\n"
                     f"🚦 Bot Status: {status}\n👤 Your Level: {user_level}")
        bot.answer_callback_query(call.id)
        bot.edit_message_text(speed_msg, chat_id, call.message.message_id, reply_markup=create_main_menu_inline(user_id))
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        bot.answer_callback_query(call.id, "Error in speed test.", show_alert=True)

def back_to_main_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    expiry_info = ""
    if user_id == OWNER_ID: user_status = "🤍 Owner"
    elif user_id in admin_ids: user_status = "🌙 Admin"
    elif user_id in user_subscriptions:
        expiry_date = user_subscriptions[user_id].get('expiry')
        if expiry_date and expiry_date > datetime.now():
            user_status = "⭐ Premium"; days_left = (expiry_date - datetime.now()).days
            expiry_info = f"\n⏳ Subscription expires in: {days_left} days"
        else: user_status = "🆓 Free User (Expired Sub)"
    else: user_status = "🆓 Free User"
    main_menu_text = (f"〽️ Welcome back, {call.from_user.first_name}!\n\n🆔 ID: `{user_id}`\n"
                      f"🔰 Status: {user_status}{expiry_info}\n📁 Files: {current_files} / {limit_str}\n\n"
                      f"👇 Use buttons or type commands.")
    try:
        bot.answer_callback_query(call.id)
        bot.edit_message_text(main_menu_text, chat_id, call.message.message_id,
                              reply_markup=create_main_menu_inline(user_id), parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" not in str(e):
            logger.error(f"API error on back_to_main: {e}")

def subscription_management_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("💳 Subscription Management\nSelect action:",
                              call.message.chat.id, call.message.message_id, reply_markup=create_subscription_menu())
    except Exception as e: logger.error(f"Error: {e}")

def stats_callback(call):
    bot.answer_callback_query(call.id)
    _logic_statistics(call.message)
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: pass

def lock_bot_callback(call):
    global bot_locked; bot_locked = True
    bot.answer_callback_query(call.id, "🔒 Bot locked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: pass

def unlock_bot_callback(call):
    global bot_locked; bot_locked = False
    bot.answer_callback_query(call.id, "🔓 Bot unlocked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: pass

def run_all_scripts_callback(call): _logic_run_all_scripts(call)

def broadcast_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📢 Send message to broadcast.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_broadcast_message)

def process_broadcast_message(message):
    user_id = message.from_user.id
    if user_id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text and message.text.lower() == '/cancel': bot.reply_to(message, "Broadcast cancelled."); return
    broadcast_content = message.text
    if not broadcast_content and not (message.photo or message.video):
        bot.reply_to(message, "⚠️ Cannot broadcast empty message.")
        return
    target_count = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("✅ Confirm & Send", callback_data=f"confirm_broadcast_{message.message_id}"),
               types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast"))
    preview_text = broadcast_content[:1000].strip() if broadcast_content else "(Media message)"
    bot.reply_to(message, f"⚠️ Confirm Broadcast:\n\n```\n{preview_text}\n```\nTo **{target_count}** users. Sure?",
                 reply_markup=markup, parse_mode='Markdown')

def handle_confirm_broadcast(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if user_id not in admin_ids: bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True); return
    try:
        original_message = call.message.reply_to_message
        if not original_message: raise ValueError("Could not retrieve original message.")
        broadcast_text = None; broadcast_photo_id = None; broadcast_video_id = None
        if original_message.text: broadcast_text = original_message.text
        elif original_message.photo: broadcast_photo_id = original_message.photo[-1].file_id
        elif original_message.video: broadcast_video_id = original_message.video.file_id
        else: raise ValueError("Message has no text or supported media.")
        bot.answer_callback_query(call.id, "🚀 Starting broadcast...")
        bot.edit_message_text(f"📢 Broadcasting to {len(active_users)} users...",
                              chat_id, call.message.message_id, reply_markup=None)
        thread = threading.Thread(target=execute_broadcast, args=(
            broadcast_text, broadcast_photo_id, broadcast_video_id,
            original_message.caption if (broadcast_photo_id or broadcast_video_id) else None, chat_id))
        thread.start()
    except ValueError as ve:
        bot.edit_message_text(f"❌ Error: {ve}", chat_id, call.message.message_id, reply_markup=None)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        bot.edit_message_text("❌ Unexpected error.", chat_id, call.message.message_id, reply_markup=None)

def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "Broadcast cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)
    if call.message.reply_to_message:
        try: bot.delete_message(call.message.chat.id, call.message.reply_to_message.message_id)
        except Exception: pass

def execute_broadcast(broadcast_text, photo_id, video_id, caption, admin_chat_id):
    sent_count = 0; failed_count = 0; blocked_count = 0
    start_exec_time = time.time()
    users_to_broadcast = list(active_users); total_users = len(users_to_broadcast)
    batch_size = 25; delay_batches = 1.5
    for i, user_id_bc in enumerate(users_to_broadcast):
        try:
            if broadcast_text: bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
            elif photo_id: bot.send_photo(user_id_bc, photo_id, caption=caption, parse_mode='Markdown' if caption else None)
            elif video_id: bot.send_video(user_id_bc, video_id, caption=caption, parse_mode='Markdown' if caption else None)
            sent_count += 1
        except telebot.apihelper.ApiTelegramException as e:
            err_desc = str(e).lower()
            if any(s in err_desc for s in ["bot was blocked", "user is deactivated", "chat not found", "kicked from", "restricted"]):
                blocked_count += 1
            elif "flood control" in err_desc or "too many requests" in err_desc:
                retry_after = 5; match = re.search(r"retry after (\d+)", err_desc)
                if match: retry_after = int(match.group(1)) + 1
                time.sleep(retry_after)
                try:
                    if broadcast_text: bot.send_message(user_id_bc, broadcast_text, parse_mode='Markdown')
                    elif photo_id: bot.send_photo(user_id_bc, photo_id, caption=caption)
                    sent_count += 1
                except Exception: failed_count += 1
            else: failed_count += 1
        except Exception: failed_count += 1
        if (i + 1) % batch_size == 0 and i < total_users - 1: time.sleep(delay_batches)
        elif i % 5 == 0: time.sleep(0.2)
    duration = round(time.time() - start_exec_time, 2)
    result_msg = (f"📢 Broadcast Complete!\n\n✅ Sent: {sent_count}\n❌ Failed: {failed_count}\n"
                  f"🚫 Blocked/Inactive: {blocked_count}\n👥 Targets: {total_users}\n⏱️ Duration: {duration}s")
    try: bot.send_message(admin_chat_id, result_msg)
    except Exception: pass

def admin_panel_callback(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text("👑 Admin Panel", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())
    except Exception: pass

def add_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID to promote to Admin.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_admin_id)

def process_add_admin_id(message):
    if message.from_user.id != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        new_admin_id = int(message.text.strip())
        if new_admin_id <= 0: raise ValueError("ID must be positive")
        if new_admin_id == OWNER_ID: bot.reply_to(message, "⚠️ Already Owner."); return
        if new_admin_id in admin_ids: bot.reply_to(message, f"⚠️ Already Admin."); return
        add_admin_db(new_admin_id)
        bot.reply_to(message, f"✅ User `{new_admin_id}` promoted to Admin.")
        try: bot.send_message(new_admin_id, "🎉 Congrats! You are now an Admin.")
        except Exception: pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "👑 Enter User ID or /cancel.")
        bot.register_next_step_handler(msg, process_add_admin_id)

def remove_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 Enter User ID of Admin to remove.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_admin_id)

def process_remove_admin_id(message):
    if message.from_user.id != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        admin_id_remove = int(message.text.strip())
        if admin_id_remove <= 0: raise ValueError("ID must be positive")
        if admin_id_remove == OWNER_ID: bot.reply_to(message, "⚠️ Cannot remove Owner."); return
        if admin_id_remove not in admin_ids: bot.reply_to(message, f"⚠️ Not Admin."); return
        if remove_admin_db(admin_id_remove):
            bot.reply_to(message, f"✅ Admin `{admin_id_remove}` removed.")
            try: bot.send_message(admin_id_remove, "ℹ️ You are no longer an Admin.")
            except Exception: pass
        else: bot.reply_to(message, f"❌ Failed to remove admin.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "👑 Enter Admin ID or /cancel.")
        bot.register_next_step_handler(msg, process_remove_admin_id)

def list_admins_callback(call):
    bot.answer_callback_query(call.id)
    try:
        admin_list_str = "\n".join(f"- `{aid}` {'(Owner)' if aid == OWNER_ID else ''}" for aid in sorted(list(admin_ids)))
        if not admin_list_str: admin_list_str = "(No Owner/Admins!)"
        bot.edit_message_text(f"👑 Current Admins:\n\n{admin_list_str}", call.message.chat.id,
                              call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')
    except Exception: pass

def add_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID & days (e.g., `12345678 30`).\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_add_subscription_details)

def process_add_subscription_details(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        parts = message.text.split()
        if len(parts) != 2: raise ValueError("Incorrect format")
        sub_user_id = int(parts[0].strip()); days = int(parts[1].strip())
        if sub_user_id <= 0 or days <= 0: raise ValueError("Positive required")
        current_expiry = user_subscriptions.get(sub_user_id, {}).get('expiry')
        start_date_new_sub = datetime.now()
        if current_expiry and current_expiry > start_date_new_sub: start_date_new_sub = current_expiry
        new_expiry = start_date_new_sub + timedelta(days=days)
        save_subscription(sub_user_id, new_expiry)
        bot.reply_to(message, f"✅ Sub for `{sub_user_id}` by {days} days.\nNew expiry: {new_expiry:%Y-%m-%d}")
        try: bot.send_message(sub_user_id, f"🎉 Sub activated/extended by {days} days! Expires: {new_expiry:%Y-%m-%d}.")
        except Exception: pass
    except ValueError as e:
        bot.reply_to(message, f"⚠️ Invalid: {e}. Format: `ID days`.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID & days, or /cancel.")
        bot.register_next_step_handler(msg, process_add_subscription_details)

def remove_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to remove sub.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_remove_subscription_id)

def process_remove_subscription_id(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        sub_user_id_remove = int(message.text.strip())
        if sub_user_id_remove <= 0: raise ValueError("ID must be positive")
        if sub_user_id_remove not in user_subscriptions:
            bot.reply_to(message, f"⚠️ No active sub in memory."); return
        remove_subscription_db(sub_user_id_remove)
        bot.reply_to(message, f"✅ Sub for `{sub_user_id_remove}` removed.")
        try: bot.send_message(sub_user_id_remove, "ℹ️ Your subscription removed by admin.")
        except Exception: pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID or /cancel.")
        bot.register_next_step_handler(msg, process_remove_subscription_id)

def check_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💳 Enter User ID to check sub.\n/cancel to abort.")
    bot.register_next_step_handler(msg, process_check_subscription_id)

def process_check_subscription_id(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        sub_user_id_check = int(message.text.strip())
        if sub_user_id_check <= 0: raise ValueError("ID must be positive")
        if sub_user_id_check in user_subscriptions:
            expiry_dt = user_subscriptions[sub_user_id_check].get('expiry')
            if expiry_dt:
                if expiry_dt > datetime.now():
                    days_left = (expiry_dt - datetime.now()).days
                    bot.reply_to(message, f"✅ User `{sub_user_id_check}` active sub.\nExpires: {expiry_dt:%Y-%m-%d %H:%M:%S} ({days_left} days left).")
                else:
                    bot.reply_to(message, f"⚠️ User `{sub_user_id_check}` expired sub (On: {expiry_dt:%Y-%m-%d %H:%M:%S}).")
                    remove_subscription_db(sub_user_id_check)
            else: bot.reply_to(message, f"⚠️ Expiry missing.")
        else: bot.reply_to(message, f"ℹ️ No active sub record.")
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        msg = bot.send_message(message.chat.id, "💳 Enter User ID or /cancel.")
        bot.register_next_step_handler(msg, process_check_subscription_id)

# --- Cleanup ---
def cleanup():
    logger.warning("Shutdown. Cleaning up processes...")
    script_keys_to_stop = list(bot_scripts.keys())
    if not script_keys_to_stop: logger.info("No scripts running."); return
    logger.info(f"Stopping {len(script_keys_to_stop)} scripts...")
    for key in script_keys_to_stop:
        if key in bot_scripts: kill_process_tree(bot_scripts[key])
atexit.register(cleanup)

# --- Main Execution ---
if __name__ == '__main__':
    logger.info("="*40 + "\n🤖 Bot Starting Up...\n" + f"🐍 Python: {sys.version.split()[0]}\n" +
                f"🔧 Base Dir: {BASE_DIR}\n📁 Upload Dir: {UPLOAD_BOTS_DIR}\n" +
                f"📊 Data Dir: {IROTECH_DIR}\n🔑 Owner ID: {OWNER_ID}\n🛡️ Admins: {admin_ids}\n" + "="*40)
    keep_alive()
    logger.info("🚀 Starting polling...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout:
            logger.warning("Polling ReadTimeout. Restarting in 5s..."); time.sleep(5)
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"Polling ConnectionError: {ce}. Retrying in 15s..."); time.sleep(15)
        except Exception as e:
            logger.critical(f"💥 Unrecoverable polling error: {e}", exc_info=True)
            logger.info("Restarting polling in 30s..."); time.sleep(30)
        finally:
            logger.warning("Polling attempt finished."); time.sleep(1)
