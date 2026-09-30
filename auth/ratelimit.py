"""
Rate limit simples baseado no cache do Django (settings.CACHES).

Usado para proteger o login e a recuperação de senha contra tentativas
repetidas (força bruta / enumeração de e-mails). Não depende de nenhuma
biblioteca externa: funciona com o LocMemCache padrão em desenvolvimento
e pode ser trocado por Redis/Memcached em produção só mudando CACHES.
"""
from dataclasses import dataclass

from django.core.cache import cache

CACHE_PREFIX = "ratelimit"


@dataclass
class RateLimitResult:
    blocked: bool
    retry_after: int = 0  # segundos até poder tentar novamente


def _key(scope: str, identifier: str) -> str:
    return f"{CACHE_PREFIX}:{scope}:{identifier}"


def get_client_ip(request) -> str:
    """Obtém o IP do cliente, respeitando um eventual proxy reverso (X-Forwarded-For)."""
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def check_rate_limit(scope: str, identifier: str, max_attempts: int, window_seconds: int) -> RateLimitResult:
    """Verifica se `identifier` já estourou o limite de tentativas para `scope`.

    Não incrementa o contador — use `hit()` para registrar uma tentativa.
    """
    key = _key(scope, identifier)
    data = cache.get(key)
    if not data:
        return RateLimitResult(blocked=False)

    count, _ = data
    if count >= max_attempts:
        ttl = cache.ttl(key) if hasattr(cache, "ttl") else window_seconds
        return RateLimitResult(blocked=True, retry_after=ttl or window_seconds)

    return RateLimitResult(blocked=False)


def hit(scope: str, identifier: str, window_seconds: int) -> int:
    """Registra uma tentativa para `identifier` em `scope`. Retorna o total acumulado na janela."""
    key = _key(scope, identifier)
    data = cache.get(key)
    if not data:
        cache.set(key, (1, window_seconds), timeout=window_seconds)
        return 1

    count, _ = data
    count += 1
    # Mantém o tempo restante original (não estende a janela a cada tentativa)
    ttl = cache.ttl(key) if hasattr(cache, "ttl") else window_seconds
    cache.set(key, (count, window_seconds), timeout=ttl or window_seconds)
    return count


def reset(scope: str, identifier: str) -> None:
    """Limpa o contador (chamar após um login/solicitação bem-sucedida)."""
    cache.delete(_key(scope, identifier))


def retry_after_minutes(seconds: int) -> int:
    return max(1, (seconds + 59) // 60)
