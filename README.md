# Valorant VC リアルタイム翻訳 + 英語学習アプリ

Valorant の英語ボイスチャットを、完全ローカルで **英日併記の字幕** にして
常時最前面の小窓に表示するデスクトップアプリ。VC 音声は一切外部に送信されない。

UI は **Editorial Ink** デザイン(pywebview / Edge WebView2 レンダリング):

- **ライブ** — リアルタイム字幕 + 味方の発言への英語返答サジェスト + 日本語→英語コール変換
- **ライブラリ** — 試合ごとのセッション一覧(発話密度・★保存数・未レビュー管理)
- **レビュー** — 行ごとの音声再生(0.5/0.75/1×)・★保存・シャドーイング採点・聞き逃し自動集計
- **復習** — ★保存フレーズを実試合音声でフラッシュカード出題(簡易間隔反復)
- **設定** — config.yaml の GUI 編集(コメント保持のまま即時書き戻し)
- **オーバーレイ** — ゲーム上のクリック透過字幕(日本語主体・最新行コーラルアクセント)

アプリ化: `build_exe.bat` で `dist/VCTranslator/VCTranslator.exe` を生成
(`make_shortcut.ps1` でデスクトップショートカット作成)。開発時は `run.bat` で GUI が起動、
`run.bat --console` 等の CLI モードも従来どおり使える。

```
Valorant VC → VB-Cable → [Silero VAD] → [faster-whisper] → [Ollama LLM] → 字幕オーバーレイ
                                             ↓ 英語を即表示          ↓ 日本語を後から同じ行に
```

- 英語原文は文字起こし完了と同時に表示(約 1 秒)、日本語訳が追って同じ行に入る二段表示
- 字幕窓はクリック透過(ゲーム操作を邪魔しない)・常時最前面・自動フェード
- `learning`(精度重視・英日併記)/ `ranked`(速度重視・日本語のみ)のプロファイル切替
- 用語辞書(`glossary.yaml`)が音声認識(hotwords)と翻訳(固定対訳)の両方に効く

## セットアップ

### 1. 依存ソフト

| ソフト | 用途 | 入手 |
|---|---|---|
| Python 3.11+ | 本体 | インストール済みなら不要 |
| VB-Audio Virtual Cable | VC 音声の分離 | https://vb-audio.com/Cable/ (無料) → インストール後 PC 再起動 |
| Ollama | 翻訳 LLM の実行 | https://ollama.com |

### 2. インストール

```bat
install.bat        :: venv 作成 + ライブラリ導入(CPU ベースライン)
install_gpu.bat    :: GPU(RTX)で文字起こしする場合に追加実行(約3GBダウンロード)
```

翻訳モデルの用意(いずれか。`config.yaml` の `translate.model` と合わせる):

```bat
ollama pull gemma4         :: 導入済みならスキップ
```

### 3. 音声ルーティング(初回のみ)

1. Valorant: 設定 → オーディオ → 音声チャット → **出力デバイス** を「CABLE Input」に
2. Windows: サウンド設定 → サウンドの詳細設定 → 「録音」タブ → **CABLE Output** →
   プロパティ → 「聴く」タブ → 「このデバイスを聴く」に✔、再生先を自分のヘッドホンに
3. 味方の VC が自分のヘッドホンから聞こえれば OK

> ゲーム音(銃声等)は通常デバイスへ、VC だけが翻訳パイプラインに入る構成。
> ノイズが混ざらないので認識精度が高い。

### 4. 表示モードの注意

Valorant を**フルスクリーン(排他)**にしていると字幕窓がゲームの上に表示されない。
**「ボーダーレスウィンドウ」に変更**するか、**サブモニタに字幕を置く**こと。

## 使い方

```bat
run.bat                          :: 標準起動(learning プロファイル + オーバーレイ)
run.bat --profile ranked         :: ランク用(速度重視・日本語のみ)
run.bat --console                :: オーバーレイなし、コンソールに字幕
run.bat --list-devices           :: 音声入力デバイス一覧
run.bat --test-file sample.wav   :: WAV ファイルでパイプラインをテスト(Valorant 不要)
run.bat --no-translate           :: 文字起こしのみ(Ollama 停止中でも動く)
run.bat --stt-model base         :: 動作確認用に軽量モデルへ一時変更
```

初回起動時は Whisper モデル(large-v3 で約 3GB)が自動ダウンロードされる。
起動後、ログに `=== ready: listening for speech ===` が出たら準備完了。

### Valorant なしで動作確認する

YouTube の Valorant 英語実況を再生し、**ブラウザ(または PC 全体)の出力デバイスを
「CABLE Input」に切り替える**と、実戦相当の入力でフルパイプラインを試せる。
もしくは英語音声の WAV を `--test-file` に渡す。

## チューニング(config.yaml)

| 症状 | 対処 |
|---|---|
| 字幕が出るのが遅い | `vad.min_silence_ms` を 300 に / `stt.beam_size: 1` / profile を ranked に |
| 文が細切れになる | `vad.min_silence_ms` を 500〜600 に |
| 誤認識が多い | `glossary.yaml` の hotwords に用語を追加 / モデルを large-v3 に |
| 誤訳が多い | `glossary.yaml` の terms に固定対訳を追加 |
| 無音なのに字幕が出る | `vad.threshold` を 0.6 に / `stt.no_speech_threshold` を 0.7 に |
| VRAM が足りない | `stt.model: medium` または `large-v3-turbo` に |

ログに各段の処理時間(`stt 0.45s` / `translate 0.80s`)が出るので、
どこが遅いかは実測で判断できる。

実測値(RTX 5070 Ti / gemma4 12B / 用語辞書込み): 翻訳 1 件あたり **約 0.5 秒**。
`config.yaml` の host は `127.0.0.1` を使うこと(`localhost` だと Windows の
IPv6 フォールバックで毎回 +2 秒かかる)。

## 注意・限界

- **遅延は構造上ゼロにできない**(日本語訳まで約 2 秒)。英語は約 1 秒で先に出る。
- **同時発話には弱い**。VC は全員の声が 1 本にミックスされて届くため。
- **配信に字幕を映さないこと**。味方の声の翻訳を公開すると第三者の音声の無断公開になり得る。
- Vanguard について: 本ツールはゲームプロセスに一切触れない(音声デバイスを読むだけ・
  字幕はただの最前面ウィンドウ)ため、チート検出の対象となる要素はない。
- **プライバシー**: 履歴機能(`history.enabled`)は味方の発言の文字起こし・翻訳・
  **音声クリップ**を `data/` フォルダにローカル保存する。全てローカル完結で外部送信は
  一切ないが、他人の声が含まれる録音であることに留意し、自分専用の学習目的で使うこと。
  `data/` は `.gitignore` 済みで、このリポジトリには含まれない。

## 構成ファイル

```
config.yaml       設定(プロファイル / VAD / モデル / オーバーレイ / 履歴 / 提案)
glossary.yaml     用語辞書(hotwords = 認識ヒント, terms = 固定対訳)
VCTranslator.spec / build_exe.bat   PyInstaller で単一 exe を作る
vc_translator/    本体
  app.py          GUI アプリ(ライブ / 履歴 / スピーキング練習タブ)
  main.py         エントリポイント(引数なし→GUI、--console 等はCLIモード)
  paths.py        exe化時/開発時のパス解決
  audio.py        音声取り込み(WASAPI / リサンプリング / WAVテスト入力)
  vad.py          Silero VAD による発話区間検出(強制区切り付き)
  stt.py          faster-whisper 文字起こし(幻聴フィルタ / CUDA→CPU フォールバック)
  translate.py    Ollama 翻訳(thinking 自動無効化 / モデル常駐 / 共通チャットクライアント)
  suggest.py      返答提案・日→英コール変換・スピーキング練習フィードバック
  history.py      SQLite 履歴 + 音声クリップ保存(学習用)
  overlay.py      字幕オーバーレイ(クリック透過 / 二段表示)/ コンソール表示
  pipeline.py     スレッドパイプライン(遅延ログ / 詰まり時は古い区間を破棄)
```

## ライセンス

Apache License 2.0. 詳細は [LICENSE](LICENSE) を参照。
