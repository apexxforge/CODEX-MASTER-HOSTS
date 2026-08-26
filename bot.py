import os
import subprocess
import threading
import sys
import time
from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "🔥 Apex Ultimate Shield Master Host is 100% Active & Protecting All Bots 24/7!"

def install_bot_requirements(folder_path):
    req_file = os.path.join(folder_path, "requirements.txt")
    if os.path.exists(req_file):
        try:
            print(f"[📦] Installing/Updating requirements for: {folder_path}")
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], check=True)
        except Exception as e:
            print(f"[!] Requirement install warning for {folder_path}: {e}")

def run_bot_script(folder_name):
    bot_folder_path = os.path.abspath(folder_name)
    bot_file = os.path.join(bot_folder_path, "bot.py")
    
    # Dependencies check karke install karega
    install_bot_requirements(bot_folder_path)

    while True:
        try:
            print(f"[*] Starting bot instance: {folder_name}")
            # Subprocess ke logs capture karne ke liye
            process = subprocess.Popen(
                [sys.executable, "bot.py"], 
                cwd=bot_folder_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Live console mein bot ka output print karega
            for line in process.stdout:
                print(f"[{folder_name}] {line.strip()}")
                
            process.wait()
            print(f"[!] Warning: Bot in '{folder_name}' stopped. Auto-restarting in 3 seconds...")
        except Exception as e:
            print(f"[!] Error in bot loop ({folder_name}): {e}")
        
        time.sleep(3)

def start_all_bots():
    print("[*] Scanning all directories for active bots...")
    for item in os.listdir("."):
        folder_path = os.path.join(".", item)
        if os.path.isdir(folder_path):
            if item.startswith(".") or item in ["__pycache__", ".git", "venv", "env"]:
                continue
                
            bot_script = os.path.join(folder_path, "bot.py")
            if os.path.exists(bot_script):
                print(f"[+] Loaded & Protected Bot: {item}")
                t = threading.Thread(target=run_bot_script, args=(item,))
                t.daemon = True
                t.start()

if __name__ == "__main__":
    start_all_bots()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
    
