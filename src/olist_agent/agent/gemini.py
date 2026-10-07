"""User-selected Gemini Developer API adapter for the existing tool agent."""
import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from olist_agent.config import Settings


def create_model():
    load_dotenv()
    key = (os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY') or '').strip()
    if not key:
        raise ValueError('Set GOOGLE_API_KEY in the local .env file to enable Gemini chat.')
    settings = Settings.load()
    return ChatGoogleGenerativeAI(
        model=os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite'),
        api_key=key,
        vertexai=False,
        temperature=1.0,
        max_tokens=4096,
        timeout=min(30, settings.max_seconds),
        max_retries=0,
        include_thoughts=False,
        thinking_level='low',
    )
