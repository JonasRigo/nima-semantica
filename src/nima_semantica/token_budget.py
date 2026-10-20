"""Local token estimates, independent of transport bytes and provider requests."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile


_VOCABULARIES = {
    "cl100k_base": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    "o200k_base": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
}


def _local_encoding(name):
    """Use an initialized or integrity-checked cached vocabulary; never download."""
    import tiktoken
    from tiktoken.registry import ENCODINGS
    if name in ENCODINGS:
        return ENCODINGS[name]
    if name not in _VOCABULARIES:
        return None
    directory = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get(
        "DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache")))
    if not directory:
        return None
    url = f"https://openaipublic.blob.core.windows.net/encodings/{name}.tiktoken"
    path = Path(directory) / hashlib.sha1(url.encode()).hexdigest()
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != _VOCABULARIES[name]:
        return None
    return tiktoken.get_encoding(name)


class TokenCounter:
    """Counts are estimates; unknown model protocols retain explicit safety room."""
    def __init__(self, model="", *, tokenizer_model="", conservative=False):
        self.encoding = None
        self.name = "conservative_utf8_bytes_div2"
        if conservative:
            return
        selected = tokenizer_model or model
        selected = selected.removeprefix("openai/").removeprefix("openrouter/")
        try:
            import tiktoken
            try:
                encoding = tiktoken.encoding_name_for_model(selected)
                proxy = False
            except KeyError:
                encoding, proxy = "cl100k_base", True
            self.encoding = _local_encoding(encoding)
            if self.encoding is not None:
                self.name = "tiktoken:" + encoding + (":proxy" if proxy else ":model")
        except (ImportError, OSError, ValueError):
            pass

    def text(self, value):
        if self.encoding is not None:
            return len(self.encoding.encode(value, disallowed_special=()))
        # Deliberately more conservative than the usual English chars/4 rule.
        # This is a heuristic, not a guaranteed bound for arbitrary tokenizers.
        return math.ceil(len(value.encode("utf-8")) / 2)

    def value(self, value):
        return self.text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))

    def messages(self, messages):
        total = 32
        for message in messages:
            if isinstance(message,(list,tuple)) and len(message)==2:
                role,content=message
                extras={}
            elif isinstance(message,dict):
                role=message.get("role",message.get("type","message"));content=message.get("content","")
                extras={key:value for key,value in message.items() if key not in {"role","type","content"} and value}
            else:
                role,content,extras="message",str(message),{}
            total += 16 + self.text(str(role)) + (self.text(content) if isinstance(content,str) else self.value(content))
            if extras:total += self.value(extras)
        return total
