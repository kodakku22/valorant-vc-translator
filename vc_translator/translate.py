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

_SYSTEM_TEMPLATE = """あなたはFPSゲーム「VALORANT」のボイスチャットのリアルタイム翻訳者です。
入力される英語の発話を、自然で簡潔な日本語に翻訳してください。

ルール:
- 出力は日本語訳のみ。説明・注釈・原文の繰り返しは一切書かない。
- ゲーム内の口語・スラング・省略表現は意図を汲んで訳す。
- 短いコールは短く訳す(逐語訳より伝わりやすさ優先)。
- 以下の用語は必ずこの対訳に従う:
{glossary}"""


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
                 terms: dict | None = None):
        self.model = model
        self.client = OllamaChat(model, host=host, think=think, keep_alive=keep_alive,
                                 temperature=temperature, timeout_s=timeout_s)
        glossary = "\n".join(f"- {en} → {ja}" for en, ja in (terms or {}).items()) or "(なし)"
        self.system_prompt = _SYSTEM_TEMPLATE.format(glossary=glossary)

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

    def translate(self, text: str) -> str:
        return self.client.chat(self.system_prompt, text)
