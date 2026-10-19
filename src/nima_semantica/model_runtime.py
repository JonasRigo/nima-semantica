"""Provider-neutral context preflight and cooperative cancellation for model IO."""
import asyncio
from contextvars import ContextVar
import json
from threading import Event

_cancelled = ContextVar('nima_model_cancelled', default=None)


class ModelContextExceeded(ValueError):
    def __init__(self, estimate, reserve, window):
        self.diagnostic = {'code': 'model.context_budget_exceeded',
            'estimated_input_tokens': estimate, 'reserved_output_tokens': reserve,
            'context_window': window, 'estimator': 'conservative_utf8_bytes_plus_framing',
            'message': 'Select fewer source regions or reduce retrieved context/history; alternatively configure a larger supported context window or a smaller output limit. No input was truncated and no model call was made.'}
        super().__init__(self.diagnostic['message'])


class ModelRequestTimeout(TimeoutError):
    def __init__(self, seconds):
        self.diagnostic = {'code': 'model.request_timeout', 'timeout_seconds': seconds,
            'message': 'The provider request timed out. Reduce the requested work/output or configure a longer model timeout. Do not assume the remote server cancelled generation.'}
        super().__init__(self.diagnostic['message'])


def model_diagnostic(exc):
    return dict(exc.diagnostic) if isinstance(exc, (ModelContextExceeded, ModelRequestTimeout)) else None


def check_cancelled():
    event = _cancelled.get()
    if event is not None and event.is_set():
        raise asyncio.CancelledError()


def check_context(profile, value, options):
    check_cancelled()
    if hasattr(value, 'to_messages'):
        value = value.to_messages()
    image_estimate = 0
    def bounded(obj):
        nonlocal image_estimate
        if hasattr(obj, 'model_dump'):
            return bounded(obj.model_dump(mode='json'))
        if isinstance(obj, list):
            return [bounded(item) for item in obj]
        if isinstance(obj, dict):
            if obj.get('type') in {'image', 'image_url'}:
                import base64
                from io import BytesIO
                from PIL import Image
                encoded = obj.get('base64')
                url = obj.get('image_url', {}).get('url') if isinstance(obj.get('image_url'), dict) else obj.get('url')
                if encoded is None and isinstance(url, str) and url.startswith('data:image/'):
                    encoded = url.partition(',')[2]
                if not isinstance(encoded, str) or len(encoded) > 11_000_000:
                    raise ValueError('Model image must be a bounded inline image')
                data = base64.b64decode(encoded, validate=True)
                if len(data) > 8_000_000:
                    raise ValueError('Model image exceeds the byte limit')
                with Image.open(BytesIO(data)) as image:
                    width, height = image.size
                    if width * height > 20_000_000:
                        raise ValueError('Model image exceeds the pixel limit')
                # Provider-independent approximation, not base64 text tokenization.
                image_estimate += 1024 + ((width + 255)//256) * ((height + 255)//256) * 512
                return {'type':'image', 'width':width, 'height':height, 'image_token_estimate':'approximate'}
            return {k:bounded(v) for k,v in obj.items()}
        return obj
    payload = json.dumps(bounded({'messages': value, 'tools': options.get('tools', [])}),
                         ensure_ascii=False, default=str)
    if profile.context_window is None:
        return
    # Deliberately conservative and tokenizer independent, not an exact provider
    # token count. Include tool schemas, UTF-8 math, message metadata and framing.
    estimate = len(payload.encode('utf-8')) + 1024 + image_estimate
    reserve = max([profile.max_tokens, *[v for key in ('max_tokens','max_output_tokens','num_predict')
                  if isinstance(v := options.get(key), int) and v > 0]])
    if estimate + reserve > profile.context_window:
        raise ModelContextExceeded(estimate, reserve, profile.context_window)


def guarded_model_class(base, profile):
    """Keep each SDK's native model/tool implementation; add no protocol gateway."""
    class GuardedModel(base):
        def invoke(self, input, config=None, **kwargs):
            check_context(profile, input, kwargs)
            try:
                result = super().invoke(input, config=config, **kwargs)
            except Exception as exc:
                if isinstance(exc, TimeoutError) or type(exc).__name__ in ('ReadTimeout','ConnectTimeout','WriteTimeout','PoolTimeout','APITimeoutError'):
                    raise ModelRequestTimeout(profile.request_timeout_seconds) from exc
                raise
            check_cancelled()
            return result

        async def ainvoke(self, input, config=None, **kwargs):
            check_context(profile, input, kwargs)
            try:
                result = await super().ainvoke(input, config=config, **kwargs)
            except Exception as exc:
                if isinstance(exc, TimeoutError) or type(exc).__name__ in ('ReadTimeout','ConnectTimeout','WriteTimeout','PoolTimeout','APITimeoutError'):
                    raise ModelRequestTimeout(profile.request_timeout_seconds) from exc
                raise
            check_cancelled()
            return result
    return GuardedModel


async def run_blocking(operation):
    """Own the complete store lifetime in one thread; never abandon its writes."""
    event = Event()
    token = _cancelled.set(event)
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        event.set()
        # A synchronous HTTP operation cannot be forcibly stopped safely. Wait
        # for its configured timeout/completion, then let controllers persist an
        # interrupted receipt and close the store before returning cancellation.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
    finally:
        _cancelled.reset(token)
