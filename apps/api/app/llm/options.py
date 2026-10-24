from app.core.config import Settings


def chat_options(settings: Settings, *, num_predict: int, temperature: float) -> dict[str, object]:
    """Options for every chat-model call. num_ctx must be identical on all of them (answers,
    query rewrites, guides, studio): a different value makes Ollama reload the model (5-8 s)."""
    return {
        "num_ctx": settings.chat_context_tokens,
        "num_predict": num_predict,
        "temperature": temperature,
    }
