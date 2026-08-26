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

def run_bot_script(folder_name):
    bot_folder_path = os.path.abspath(folder_name)
    
    while True:
        try:
            print(f"[*] Starting bot instance: {folder_name}")
            process = subprocess.Popen(
                [sys.executable, "bot.py"], 
                cwd=bot_folder_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
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
    
