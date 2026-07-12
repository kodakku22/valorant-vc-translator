"""EN -> JA translation through a local Ollama server."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import time

import requests

log = logging.getLogger("translate")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# ---------------------------------------------------------------- P1 cache

# Very common short calls whose translation should be instant and stable.
_STOCK_PHRASES = {
    "nice": "ナイス",
    "nice one": "ナイス",
    "nice shot": "ナイスショット",
    "nice try": "ナイストライ",
    "thanks": "ありがとう",
    "thank you": "ありがとう",
    "sorry": "ごめん",
    "my bad": "すまん、ミスった",
    "good luck": "頑張ろう",
    "gl hf": "よろしく!",
    "gg": "お疲れ",
    "good game": "お疲れ",
    "one more": "もう1回",
    "let's go": "行くぞ!",
    "lets go": "行くぞ!",
    "reloading": "リロード中",
    "be careful": "気をつけて",
    "watch out": "危ない、注意!",
    "behind you": "後ろ!",
    "on your left": "左にいる!",
    "on your right": "右にいる!",
    "i'm dead": "やられた",
    "he's one": "敵は残り一撃",
    "one shot": "敵は残り一撃",
    "last one": "残り1人",
    "good job": "よくやった",
    "well played": "ナイスプレー",
    "no problem": "気にしないで",
    "wait": "待って",
    "go go go": "行け行け行け!",
    "help": "助けて!",
    "help me": "助けて!",
}


def _cache_key(text: str) -> str:
    """Normalize for cache lookup: case/punctuation-insensitive."""
    return re.sub(r"[^a-z0-9' ]+", "", text.lower()).strip()


class TranslationCache:
    """Two layers: built-in stock phrases + an LRU of past LLM translations.

    Only SHORT utterances are cached (long ones are context-dependent), which
    makes frequent calls render instantly and always with the same wording."""

    MAX_WORDS = 6
    LRU_SIZE = 512

    def __init__(self):
        from collections import OrderedDict
        self._lru: "OrderedDict[str, str]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _cacheable(self, key: str) -> bool:
        return bool(key) and len(key.split()) <= self.MAX_WORDS

    def get(self, text: str) -> str | None:
        key = _cache_key(text)
        if not self._cacheable(key):
            return None
        hit = _STOCK_PHRASES.get(key)
        if hit is None and key in self._lru:
            self._lru.move_to_end(key)
            hit = self._lru[key]
        if hit is not None:
            self.hits += 1
        else:
            self.misses += 1
        return hit

    def put(self, text: str, ja: str):
        key = _cache_key(text)
        if not self._cacheable(key) or not ja or ja.startswith("(翻訳失敗"):
            return
        self._lru[key] = ja
        self._lru.move_to_end(key)
        while len(self._lru) > self.LRU_SIZE:
            self._lru.popitem(last=False)

_SYSTEM_TEMPLATE = """あなたはFPSゲーム「VALORANT」のボイスチャットのリアルタイム翻訳者です。
入力される英語の発話を、自然で簡潔な日本語に翻訳してください。

ルール:
- 出力は日本語訳のみ。説明・注釈・原文の繰り返しは一切書かない。
- ゲーム内の口語・スラング・省略表現は意図を汲んで訳す。
- 短いコールは短く訳す(逐語訳より伝わりやすさ優先)。
{style}
- 以下の用語は必ずこの対訳に従う:
{glossary}"""

# P7: selectable translation tone (`translate.style` in config.yaml)
_STYLES = {
    "casual": ("- 口調(必須): 自然な常体・タメ口で訳す。丁寧語は使わない。"
               "(例: 「下がれ」「俺を待て」「Bに来てる」)"),
    "polite": ("- 口調(必須): 全ての文末を「です・ます調」の丁寧語にする。"
               "命令は「〜してください」。(例: 「下がってください」「Bに来ています」)"),
    "gamer": ("- 口調(必須): 日本の FPS プレイヤーの実況口語。短く鋭く、"
              "定番スラングを使う。(例: 「B来てる、引け引け!」「詰めろ」「カバー入る」)"),
}


def ensure_ollama(host: str, wait_s: float = 25.0) -> bool:
    """Return True if the Ollama server is reachable, starting it if needed."""
    version_url = host.rstrip("/") + "/api/version"
    try:
        requests.get(version_url, timeout=3)
        return True
    except requests.RequestException:
        pass

    exe = shutil.which("ollama")
    if exe is None:
        log.error("ollama コマンドが見つかりません(Ollama 未インストール?)")
        return False
    log.info("Ollama が起動していないため自動起動します...")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([exe, "serve"], creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            requests.get(version_url, timeout=2)
            log.info("Ollama 起動完了")
            return True
        except requests.RequestException:
            time.sleep(1.0)
    log.error("Ollama の自動起動がタイムアウトしました")
    return False


class OllamaChat:
    """Minimal chat client shared by the translator and the suggestion engine."""

    def __init__(self, model: str, host: str = "http://127.0.0.1:11434",
                 think: bool | None = False, keep_alive: int | str = -1,
                 temperature: float = 0.2, timeout_s: float = 30.0):
        self.model = model
        # "localhost" costs ~2s per request on Windows (IPv6 fallback) -- force IPv4.
        self.base = host.replace("//localhost", "//127.0.0.1").rstrip("/")
        self.url = self.base + "/api/chat"
        self.think = think
        self.keep_alive = keep_alive
        self.temperature = temperature
        self.timeout_s = timeout_s
        self._send_think = think is not None
        self._session = requests.Session()

    def ensure_server(self) -> bool:
        return ensure_ollama(self.base)

    def chat(self, system: str, user: str, num_predict: int = 256,
             timeout_s: float | None = None) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_predict": num_predict},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._send_think:
            payload["think"] = self.think
        timeout = timeout_s if timeout_s is not None else self.timeout_s

        resp = self._session.post(self.url, json=payload, timeout=timeout)
        if resp.status_code == 400 and self._send_think and "think" in resp.text.lower():
            # Model doesn't support the thinking flag -- retry without it, permanently.
            log.info("model %s does not support 'think' parameter; disabling it", self.model)
            self._send_think = False
            del payload["think"]
            resp = self._session.post(self.url, json=payload, timeout=timeout)
        resp.raise_for_status()

        content = resp.json().get("message", {}).get("content", "")
        return _THINK_RE.sub("", content).strip()


class Translator:
    def __init__(self, model: str, host: str = "http://127.0.0.1:11434",
                 think: bool | None = False, keep_alive: int | str = -1,
                 temperature: float = 0.2, timeout_s: float = 30.0,
                 terms: dict | None = None, style: str = "casual"):
        self.model = model
        self.client = OllamaChat(model, host=host, think=think, keep_alive=keep_alive,
                                 temperature=temperature, timeout_s=timeout_s)
        self.terms = terms or {}
        self.style = style if style in _STYLES else "casual"
        self.cache = TranslationCache()
        glossary = "\n".join(f"- {en} → {ja}" for en, ja in self.terms.items()) or "(なし)"
        self.system_prompt = _SYSTEM_TEMPLATE.format(
            style=_STYLES[self.style], glossary=glossary)

    def warmup(self):
        """Load the model into VRAM before the match starts."""
        self.client.ensure_server()
        try:
            # Cold model load can take minutes -- extend the timeout just here.
            result = self.client.chat(self.system_prompt, "hello", timeout_s=300)
            log.info("translator warmup done (model=%s, sample: %r)", self.model, result)
        except requests.ConnectionError:
            raise RuntimeError(
                f"Ollama に接続できません ({self.client.url})。"
                "別ターミナルで `ollama serve` を起動するか、Ollama アプリを開いてください。")

    def translate(self, text: str, context: str = "") -> str:
        # P1: instant, stable translations for frequent short calls.
        cached = self.cache.get(text)
        if cached is not None:
            return cached
        # B6: give the model the previous line so pronouns/ellipsis resolve.
        if context:
            user = f"直前の発言: {context}\n---\n次を訳す: {text}"
        else:
            user = text
        ja = self.client.chat(self.system_prompt, user)
        ja = self._enforce_terms(text, ja)
        self.cache.put(text, ja)
        return ja

    def _enforce_terms(self, en: str, ja: str) -> str:
        """B7: if a glossary term is in the source but its fixed translation is
        missing from the output, retry once with an explicit reminder."""
        missing = []
        low = en.lower()
        for term_en, term_ja in self.terms.items():
            if not term_ja:
                continue
            if re.search(rf"\b{re.escape(term_en.lower())}\b", low) and term_ja not in ja:
                missing.append((term_en, term_ja))
        if not missing:
            return ja
        reminder = "、".join(f"'{e}'は「{j}」" for e, j in missing)
        retry_user = (f"次の英語を、指定の対訳を必ず使って自然な日本語に訳して"
                      f"({reminder})。訳文のみ:\n{en}")
        try:
            fixed = self.client.chat(self.system_prompt, retry_user)
            # keep the retry only if it actually satisfied the constraint
            if all(j in fixed for _, j in missing):
                return fixed
        except Exception:
            pass
        return ja
