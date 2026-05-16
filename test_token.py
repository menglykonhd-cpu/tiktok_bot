# test_token.py
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("BOT_TOKEN")

if token:
    print(f"✅ Token found: {token[:10]}...{token[-5:]}")
    print(f"Length: {len(token)} characters")
else:
    print("❌ No token found! Check your .env file")