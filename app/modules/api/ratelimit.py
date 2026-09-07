"""Throttler + retry pour les appels Albert API.

Contexte : le tier "experimentation" d'Albert impose des quotas serres
(gpt-oss-120b : 10 RPM / 128 000 TPM ; Mistral 3.2 24B : 50 RPM / 128 000 TPM).
Sans throttling, un lot de 300+ documents va emettre des rafales qui
declenchent des 429 en cascade.

Ce module fournit :

- `RateLimiter` : throttler par modele, fenetre glissante de 60 s.
  `wait_and_reserve(tokens)` bloque jusqu'a ce qu'il y ait de la place
  pour un appel du nombre de tokens estime.
- `call_albert_chat_completions()` : wrapper httpx qui reserve, poste,
  et gere les 429 avec backoff exponentiel + `Retry-After` officiel.

Les etats de throttling sont **module-global** : la meme instance
`RateLimiter` par modele est partagee entre l'auto-fill LLM texte et
la conversion Vision, ce qui evite qu'un pipeline plein ne cannibalise
l'autre.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger("atelier.api.ratelimit")

# Quotas par modele pour le tier "experimentation" Albert.
# Reference : https://ia.numerique.gouv.fr/outils-ia/albert-api/tarifs-et-limites/
# Format : (rpm, tpm). Mets a jour si tu passes en "production limitee".
DEFAULT_QUOTAS: dict[str, tuple[int, int]] = {
    "openai/gpt-oss-120b": (10, 128_000),
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506": (50, 128_000),
    # Fallback conservateur pour un modele non liste.
    "_default": (10, 128_000),
}

WINDOW_SECONDS = 60.0
MAX_RETRIES_429 = 5
BACKOFF_BASE = 2.0  # secondes


class RateLimiter:
    """Fenetre glissante 60 s : RPM + TPM."""

    def __init__(self, rpm: int, tpm: int, name: str = ""):
        self.rpm = rpm
        self.tpm = tpm
        self.name = name
        self._lock = threading.Lock()
        self._requests: deque[float] = deque()             # timestamps
        self._tokens: deque[tuple[float, int]] = deque()   # (ts, tokens)

    def _purge(self, now: float) -> None:
        limit = now - WINDOW_SECONDS
        while self._requests and self._requests[0] < limit:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] < limit:
            self._tokens.popleft()

    def wait_and_reserve(self, tokens: int) -> None:
        """Bloque jusqu'a avoir un slot RPM ET un budget TPM suffisant."""
        # Une demande plus grosse que le budget d'une minute ne peut JAMAIS
        # tenir : la condition ci-dessous resterait fausse fenetre vide, et la
        # boucle tournerait indefiniment en journalisant « Quota atteint ». La
        # tache resterait active pour toujours, sans rien produire, et
        # bloquerait toute mise a jour. Atteignable en poussant
        # `autofill_max_chars` au-dela de ~512 000 caracteres.
        if tokens > self.tpm:
            logger.warning(
                "[%s] Demande de %d tokens au-dessus du budget d'une minute "
                "(%d) : plafonnee, l'API refusera peut-etre.",
                self.name, tokens, self.tpm)
            tokens = self.tpm
        while True:
            with self._lock:
                now = time.monotonic()
                self._purge(now)
                current_rpm = len(self._requests)
                current_tpm = sum(t for _, t in self._tokens)
                if current_rpm < self.rpm and current_tpm + tokens <= self.tpm:
                    # Reservation optimiste : on ecrit avant l'appel HTTP.
                    self._requests.append(now)
                    self._tokens.append((now, tokens))
                    return
                # Calcul du temps a attendre : soit un slot RPM se libere,
                # soit du budget TPM.
                waits: list[float] = []
                if current_rpm >= self.rpm and self._requests:
                    waits.append(WINDOW_SECONDS - (now - self._requests[0]))
                if current_tpm + tokens > self.tpm and self._tokens:
                    waits.append(WINDOW_SECONDS - (now - self._tokens[0][0]))
                if not waits:
                    waits = [0.5]
                wait = max(0.1, min(waits) + 0.05)
            logger.info(
                "[%s] Quota atteint (RPM=%d/%d, TPM=%d/%d) — attente %.1fs",
                self.name, current_rpm, self.rpm, current_tpm, self.tpm, wait,
            )
            time.sleep(wait)

    def adjust_last(self, actual_tokens: int) -> None:
        """Corrige la reservation avec la valeur reelle renvoyee par l'API."""
        with self._lock:
            if not self._tokens:
                return
            ts, _ = self._tokens[-1]
            self._tokens[-1] = (ts, actual_tokens)


# Registre singleton des limiters : un par modele.
_LIMITERS: dict[str, RateLimiter] = {}
_REGISTRY_LOCK = threading.Lock()


def get_limiter(model: str) -> RateLimiter:
    with _REGISTRY_LOCK:
        if model not in _LIMITERS:
            rpm, tpm = DEFAULT_QUOTAS.get(model, DEFAULT_QUOTAS["_default"])
            _LIMITERS[model] = RateLimiter(rpm=rpm, tpm=tpm, name=model)
        return _LIMITERS[model]


def estimate_tokens_from_chars(chars: int) -> int:
    """4 caracteres ~= 1 token (francais). Marge sur la sortie : + 500."""
    return chars // 4 + 500


def call_albert_chat_completions(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    estimated_input_chars: int,
    on_rate_limit: Any = None,
) -> httpx.Response:
    """Appel POST throttled + retry 429 pour /v1/chat/completions.

    - Reserve un slot avant emission (RPM/TPM par modele).
    - Sur 429, respecte `Retry-After` si present, sinon backoff exponentiel.
    - Sur reponse 200, corrige la reservation avec `usage.total_tokens` si
      renvoye par l'API.
    """
    model = payload.get("model") or "_default"
    limiter = get_limiter(model)
    estimated = estimate_tokens_from_chars(estimated_input_chars)

    last_resp: httpx.Response | None = None
    for attempt in range(1, MAX_RETRIES_429 + 1):
        limiter.wait_and_reserve(estimated)
        debut = time.monotonic()
        resp = client.post(url, headers=headers, json=payload)
        last_resp = resp
        if resp.status_code == 429:
            retry_after_hdr = resp.headers.get("Retry-After", "")
            try:
                wait = float(retry_after_hdr)
            except (TypeError, ValueError):
                wait = BACKOFF_BASE * (2 ** (attempt - 1))
            wait = max(1.0, min(wait, 60.0))
            if on_rate_limit is not None:
                on_rate_limit(attempt, MAX_RETRIES_429, wait, time.monotonic() - debut)
            logger.warning(
                "[%s] 429 (tentative %d/%d), attente %.1fs (Retry-After=%r)",
                model, attempt, MAX_RETRIES_429, wait, retry_after_hdr,
            )
            time.sleep(wait)
            continue
        # Reponse non 429 : corrige la reservation si l'API renvoie l'usage.
        if resp.status_code == 200:
            try:
                data = resp.json()
                usage = data.get("usage") or {}
                total = int(usage.get("total_tokens") or 0)
                if total > 0:
                    limiter.adjust_last(total)
            except (ValueError, KeyError, TypeError):
                pass
        return resp
    logger.error("[%s] Tous les retries 429 epuises.", model)
    return last_resp  # type: ignore[return-value]
