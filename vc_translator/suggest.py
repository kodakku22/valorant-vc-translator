"""English-learning helpers: reply suggestions, JA->EN callout conversion,
and feedback on the player's own spoken replies. All via the local Ollama model.
"""

from __future__ import annotations

import logging

from vc_translator.translate import OllamaChat

log = logging.getLogger("suggest")

_REPLY_SYSTEM = """あなたはFPSゲーム「VALORANT」の英語コーチです。
味方が英語のボイスチャットで発言しました。それに対してプレイヤー(英語学習中の日本人)が
返せる、短くて実用的な英語の返答例を2〜3個提案してください。

ルール:
- ゲーム内で実際に使われる簡潔なコール口調(長文禁止、1返答は10語以内目安)
- 初心者でも言いやすい表現を優先
- 出力は次の形式で、返答例のみを行ごとに書く(説明や番号は書かない):
英語の返答 || 日本語の意味
英語の返答 || 日本語の意味"""

_CALLOUT_SYSTEM = """あなたはFPSゲーム「VALORANT」の英語コーチです。
プレイヤーが日本語で伝えたい内容を、ゲーム内ボイスチャットで実際に使える
自然で簡潔な英語コールに変換してください。

ルール:
- 実戦のコール口調(短く、即座に伝わる形。丁寧語や完全な文は不要)
- 言い方が複数あれば最大2個
- 出力は次の形式で、変換結果のみを行ごとに書く(説明や番号は書かない):
英語のコール || 日本語の意味(ニュアンス)"""

_FEEDBACK_SYSTEM = """あなたはFPSゲーム「VALORANT」の英語スピーキングコーチです。
味方の発言に対して、プレイヤー(日本人学習者)が英語で返答しました。
その返答を評価してください。

出力形式(日本語で、簡潔に):
伝わるか: ◎/○/△ と一言
より自然な言い方: 英語の例文(あれば)
ワンポイント: 発音や表現のアドバイス1つ"""

_EXPLAIN_SYSTEM = """あなたはFPSゲーム「VALORANT」の英語コーチです。
プレイヤー(日本人学習者)が保存した実戦の英語フレーズを、日本語で短く解説してください。

出力形式(日本語で、各1行、簡潔に):
意味: このコールが実戦で指す内容
表現: スラング・略語・文法のポイント(あれば)
使う場面: いつ自分で言えるか"""


def _parse_pairs(text: str) -> list[tuple[str, str]]:
    """Parse 'English || 日本語' lines; tolerate stray formatting."""
    pairs = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*0123456789. ")
        if not line:
            continue
        if "||" in line:
            en, _, ja = line.partition("||")
            pairs.append((en.strip(), ja.strip()))
        elif len(pairs) < 3 and any(c.isalpha() for c in line):
            pairs.append((line, ""))
    return pairs[:3]


class Suggester:
    def __init__(self, client: OllamaChat):
        self.client = client

    def suggest_replies(self, en_utterance: str) -> list[tuple[str, str]]:
        """Short English replies (with JA gloss) to a teammate's VC line."""
        raw = self.client.chat(_REPLY_SYSTEM, f"味方の発言: {en_utterance}", num_predict=200)
        return _parse_pairs(raw)

    def ja_to_callout(self, ja_text: str) -> list[tuple[str, str]]:
        """Convert Japanese intent into natural English callouts."""
        raw = self.client.chat(_CALLOUT_SYSTEM, f"伝えたい内容: {ja_text}", num_predict=160)
        return _parse_pairs(raw)

    def feedback(self, context_en: str, reply_en: str) -> str:
        """Coach feedback (in Japanese) on the player's spoken English reply."""
        user = (f"味方の発言: {context_en}\n"
                f"プレイヤーの返答(音声認識結果): {reply_en}")
        return self.client.chat(_FEEDBACK_SYSTEM, user, num_predict=300)

    def explain(self, en: str, ja: str = "") -> str:
        """Explain a saved phrase's meaning / slang / usage in Japanese (D11)."""
        user = f"フレーズ: {en}" + (f"\n参考訳: {ja}" if ja else "")
        return self.client.chat(_EXPLAIN_SYSTEM, user, num_predict=260)
