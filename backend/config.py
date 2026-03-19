import os
from dotenv import load_dotenv

load_dotenv()

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DB = os.getenv("MYSQL_DB")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
CALCOM_API_KEY = os.getenv("CALCOM_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")