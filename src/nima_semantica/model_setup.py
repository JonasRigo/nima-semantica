"""Independent model/embedding choices shared by interactive and scripted setup."""
import json
from .installation import ModelProfile, EmbeddingProfile, MODEL_ENDPOINTS


def model_profile(args, *, provider=None, model=None):
    interactive = not args.non_interactive
    provider = provider or args.provider or (input("LLM provider [openrouter/openai/anthropic/gemini/ollama/compatible] (ollama): ").strip() or "ollama" if interactive else "openrouter")
    if provider not in MODEL_ENDPOINTS:
        raise ValueError("Unknown LLM provider")
    model = model or args.model or input("LLM model identifier (choose an installed model for local servers): ").strip()
    base_url = args.base_url
    if not base_url:
        default = MODEL_ENDPOINTS[provider]
        base_url = (input(f"LLM server URL ({default or 'required'}): ").strip() or default) if interactive else default
    credential = args.credential
    if credential is None:
        default = "" if provider == "ollama" else "NIMA_MODEL_API_KEY"
        credential = (input(f"LLM credential environment variable ({default or 'none'}; '-' for no key): ").strip() or default) if interactive else default
    if credential == "-":
        credential = ""
    if provider in {"openrouter", "openai", "anthropic", "gemini"} and not credential:
        raise ValueError("Hosted native providers require a named credential environment variable")
    parameters = json.loads(args.model_parameters or "{}")
    return ModelProfile(provider=provider, model=model, base_url=base_url,
                        credential=credential, parameters=parameters, max_tokens=args.max_tokens)


def embedding_profile(args):
    interactive = not args.non_interactive
    provider = args.embedding_provider
    if provider is None:
        provider = (input("Embedding provider [none/ollama/openai/compatible] (none): ").strip() or "none") if interactive else "none"
    if provider == "none":
        return None
    if provider not in {"ollama", "openai", "compatible"}:
        raise ValueError("Unknown embedding provider")
    model = args.embedding_model or (input("Embedding model identifier: ").strip() if interactive else "")
    if not model:
        raise ValueError("Embedding model is required")
    default = {"ollama":"http://127.0.0.1:11434", "openai":"https://api.openai.com/v1", "compatible":""}[provider]
    base_url = args.embedding_base_url or ((input(f"Embedding server URL ({default or 'required'}): ").strip() or default) if interactive else default)
    if not base_url:
        raise ValueError("Embedding endpoint is required")
    if provider == "ollama":
        from .setup_services import probe_embedding
        return EmbeddingProfile(**probe_embedding(model, base_url))
    revision = args.embedding_revision or (input("Embedding model revision/pin: ").strip() if interactive else "")
    dimension = args.embedding_dimension or (int(input("Embedding vector dimension: ").strip()) if interactive else 0)
    if not revision or not dimension:
        raise ValueError("Remote embeddings require explicit revision and dimension")
    credential = args.embedding_credential
    if credential is None:
        credential = (input("Embedding credential environment variable (NIMA_EMBEDDING_API_KEY; '-' for no key): ").strip() or "NIMA_EMBEDDING_API_KEY") if interactive else "NIMA_EMBEDDING_API_KEY"
    return EmbeddingProfile(provider=provider, model=model, base_url=base_url,
        revision=revision, dimension=dimension, credential="" if credential == "-" else credential)


def native_chat_model(profile, credential_value=None):
    """Construct the chosen native protocol; never route local traffic to cloud."""
    import os
    token = credential_value or (os.environ.get(profile.credential) if profile.credential else None)
    if hasattr(token, "get_secret_value"):
        token = token.get_secret_value()
    if profile.provider != "ollama" and not token:
        raise ValueError("Configured model credential is missing")
    endpoint = profile.container_url or profile.base_url
    options = dict(profile.parameters)
    reserved = {"model", "model_name", "base_url", "api_key", "anthropic_api_key", "google_api_key",
                "anthropic_api_url", "client", "credentials", "client_kwargs", "http_client", "http_async_client"}
    if reserved & options.keys():
        raise ValueError("Model parameters must not override provider, endpoint or credential routing")
    if profile.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        class AnthropicModel(ChatAnthropic):
            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return super().bind_tools(tools, tool_choice="any" if tool_choice == "required" else tool_choice, **kwargs)
        return AnthropicModel(model=profile.model, anthropic_api_key=token, anthropic_api_url=endpoint,
            max_tokens=profile.max_tokens, max_retries=0, **options)
    if profile.provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        class GeminiModel(ChatGoogleGenerativeAI):
            def bind_tools(self, tools, **kwargs):
                kwargs.pop("parallel_tool_calls", None)
                return super().bind_tools(tools, **kwargs)
        return GeminiModel(model=profile.model, google_api_key=token, base_url=endpoint,
            max_output_tokens=profile.max_tokens, max_retries=0, vertexai=False, **options)
    if profile.provider == "ollama":
        from langchain_ollama import ChatOllama
        class OllamaModel(ChatOllama):
            def bind_tools(self, tools, **kwargs):
                # Ollama cannot enforce tool_choice or parallel-tool limits;
                # NIMA still rejects anything other than one authorized call.
                kwargs.pop("parallel_tool_calls", None)
                return super().bind_tools(tools, **kwargs)
        return OllamaModel(model=profile.model, base_url=endpoint.rstrip('/').removesuffix('/v1'),
            num_predict=profile.max_tokens, **options)
    raise ValueError("Not a native-protocol provider")
