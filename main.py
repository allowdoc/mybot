from flask import Flask
import subprocess
import threading
import time
import os

app = Flask(__name__)

# Global variable to store the bot process
bot_process = None

def start_bot():
    """Start the Telegram bot as a subprocess."""
    global bot_process
    try:
        # Ensure the bot script exists
        if not os.path.exists("tgbot.py"):
            raise FileNotFoundError("tgbot.py not found in the current directory.")
        
        # Start the bot process
        bot_process = subprocess.Popen(["python3", "tgbot.py"])
        print("Telegram bot started successfully.")
    except Exception as e:
        print(f"Failed to start Telegram bot: {e}")

def stop_bot():
    """Stop the Telegram bot process."""
    global bot_process
    if bot_process:
        bot_process.terminate()
        bot_process.wait()
        print("Telegram bot stopped successfully.")
        bot_process = None

def restart_bot():
    """Restart the Telegram bot every 3 hours."""
    while True:
        stop_bot()
        start_bot()
        time.sleep(3 * 60 * 60)  # Wait for 3 hours before restarting

@app.route('/')
def hello_world():
    return 'Hello, World! Flask server is running!'

@app.route('/start_bot')
def start_bot_route():
    """Route to manually start the Telegram bot."""
    start_bot()
    return 'Telegram bot started!'

@app.route('/stop_bot')
def stop_bot_route():
    """Route to manually stop the Telegram bot."""
    stop_bot()
    return 'Telegram bot stopped!'

@app.route('/restart_bot')
def restart_bot_route():
    """Route to manually restart the Telegram bot."""
    stop_bot()
    start_bot()
    return 'Telegram bot restarted!'

def run_flask_app():
    """Run the Flask app."""
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    # Start the Telegram bot when the Flask app starts
    start_bot()

    # Start the bot restart thread
    bot_restart_thread = threading.Thread(target=restart_bot)
    bot_restart_thread.daemon = True  # Daemonize thread to stop it when the main program exits
    bot_restart_thread.start()

    # Run the Flask app
    run_flask_app()
