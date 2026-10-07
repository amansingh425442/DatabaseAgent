"""Enable the selected Gemini adapter without displaying the user's API key."""
from pathlib import Path
import argparse
import re
from dotenv import dotenv_values
from configure_reader import update_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', help='explicit Gemini model ID; existing selection is otherwise preserved')
    args = parser.parse_args()
    env_file = Path(__file__).resolve().parents[1] / '.env'
    values = dotenv_values(env_file, interpolate=False)
    key = (values.get('GOOGLE_API_KEY') or values.get('GEMINI_API_KEY') or '').strip()
    if not key:
        raise SystemExit('Add GOOGLE_API_KEY to the local .env file first.')
    model = args.model or values.get('GEMINI_MODEL') or 'gemini-3.1-flash-lite'
    if not re.fullmatch(r'gemini-[A-Za-z0-9.-]+', model):
        raise SystemExit('Use a Gemini model ID from your provider model list.')
    update_env(env_file, {
        'MODEL_FACTORY': 'olist_agent.agent.gemini:create_model',
        'GEMINI_MODEL': model,
    })
    print(f'Configured {model} through the Gemini Developer API; credentials were not displayed.')


if __name__ == '__main__':
    main()
