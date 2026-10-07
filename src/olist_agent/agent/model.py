from importlib import import_module
import re
from langchain_core.language_models.chat_models import BaseChatModel


def load_model(factory_path):
    if not factory_path:
        raise ValueError("No LLM selected. Set MODEL_FACTORY=your_module:create_model after choosing a provider/model. No paid service is selected automatically.")
    module, separator, name = factory_path.partition(":")
    if not separator:
        raise ValueError("MODEL_FACTORY must be module:function")
    model = getattr(import_module(module), name)()
    if not isinstance(model, BaseChatModel):
        raise ValueError("Model factory must return a LangChain BaseChatModel with structured tool calling")
    return model


def model_error_message(error):
    """Give actionable provider errors without reflecting payloads or secrets."""
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, 'code', None) or getattr(current, 'status_code', None)
        if code not in (401, 403, 404, 429, 503, 504):
            match = re.search(r'\b(401|403|404|429|503|504)\b', str(current))
            code = int(match.group(1)) if match else None
        if code == 503:
            return 'The model provider is temporarily busy. Try the question again shortly.'
        if code == 429:
            return 'The model API quota or rate limit was reached. Check your free-tier limits and try again after they reset.'
        if code in (401, 403):
            return 'The model API rejected authentication or access. Check the API key and model permissions in your provider account.'
        if code == 404:
            return 'The configured model is unavailable to this API account. Check GEMINI_MODEL and your provider model list.'
        if code == 504 or isinstance(current, TimeoutError):
            return 'The investigation reached its time limit. Try a smaller question or retry when the model provider is responsive.'
        current = current.__cause__ or current.__context__
    return 'Check model configuration, indexing, database connectivity and configured budgets.'
