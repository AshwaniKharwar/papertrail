from google import genai

from .config import GEMINI_API_KEY

gemini = genai.Client(api_key=GEMINI_API_KEY)
