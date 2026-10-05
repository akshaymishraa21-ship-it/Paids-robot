import os
from threading import Thread
from flask import Flask

app = Flask("")

@app.route("/")
def home():
    return "Bot is alive!"

def run():
    port = int(os.environ.get("PORT", 8082))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def keep_alive():
    thread = Thread(target=run, name="bot-health-server", daemon=True)
    thread.start()
    return thread