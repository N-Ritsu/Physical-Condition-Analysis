"""ローカルLLM（Ollama）によるグラフ解説コメントの生成。

現場ヒアリングで得られたガードレールを厳守する:
- 医学的診断・病名判断・服薬に関する言及は一切行わない。
- 断定的な決めつけを避け、「〜の傾向が見られます」「面談で聞いてみませんか」という
  対話補助のトーンに限定する。
- データは一切外部送信せず、ローカルのOllamaにのみ問い合わせる。
"""

from __future__ import annotations

import pandas as pd

from health_dashboard.insight_context import InsightContext, build_context

DEFAULT_MODEL = "qwen3:8b"

_SYSTEM_PROMPT = """\
あなたは就労移行支援事業所の支援員が、利用者の日報データを一緒に振り返るための
補助コメントを作成するアシスタントです。あなたは医療職ではなく、利用者や支援員に
向けて発言することを常に意識してください。

厳守事項:
- 病名・診断・症状の医学的判断を絶対に行わない。
- 服薬の要否・変更など医療行為に関する言及を絶対に行わない。
- 「〜だから危険です」のような断定的な因果関係の決めつけをしない。
- 出力は日本語で150〜200文字程度。
- 文末は「〜の傾向が見られます」「面談で〇〇について聞いてみるとよいかもしれません」
  など、対話のきっかけを提案する穏やかなトーンにする。
- 数値の羅列ではなく、支援員が利用者と会話するための自然な文章にする。
"""


class LLMUnavailableError(RuntimeError):
    """Ollama本体・モデルが利用できない場合に送出する。"""


def _build_user_prompt(ctx: InsightContext) -> str:
    lines = [
        f"対象項目: {ctx.axis_label}",
        f"対象期間: {ctx.period_label}",
        f"データ件数: {ctx.stats.count}",
    ]
    if ctx.stats.count == 0:
        lines.append("この期間のデータはありません。")
    else:
        lines.append(f"平均値: {ctx.stats.mean:.2f}")
        lines.append(f"中央値: {ctx.stats.median:.2f}")
        if ctx.stats.mode is not None:
            lines.append(f"最頻値: {ctx.stats.mode:.2f}")
        if ctx.latest_value is not None:
            lines.append(f"直近の値: {ctx.latest_value:.2f}")
        if ctx.trend_diff is not None:
            direction = "上昇" if ctx.trend_diff > 0 else "低下" if ctx.trend_diff < 0 else "横ばい"
            lines.append(f"期間前半と後半の平均の変化: {direction}（差分 {ctx.trend_diff:+.2f}）")
    lines.append("")
    lines.append("上記のデータをもとに、支援員が利用者との面談で使える振り返りコメントを作成してください。")
    return "\n".join(lines)


def generate_insight(
    df: pd.DataFrame,
    axis_col: str,
    axis_label: str,
    period_label: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """選択中の指標・期間についてのAI考察コメントを生成する。"""
    try:
        import ollama
    except ImportError as e:  # pragma: no cover - 依存未導入時の防御
        raise LLMUnavailableError(
            "ollamaライブラリがインストールされていません。requirements.txt を"
            "インストールしてください。"
        ) from e

    ctx = build_context(df, axis_col, axis_label, period_label)
    user_prompt = _build_user_prompt(ctx)

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            # qwen3は思考モードが既定で有効だが、短い定型コメント生成では不要かつ
            # CPU実行では大幅な遅延(数十秒〜数分)につながるため無効化する。
            think=False,
        )
    except Exception as e:  # ollama未起動・モデル未取得など
        message = str(e).lower()
        if "not found" in message or "pull" in message:
            raise LLMUnavailableError(
                f"モデル『{model}』が見つかりません。"
                f"コマンドプロンプトで `ollama pull {model}` を実行してください。"
            ) from e
        raise LLMUnavailableError(
            "ローカルのOllamaに接続できません。Ollamaアプリが起動しているか"
            "確認してください（タスクトレイにアイコンが表示されていればOKです）。"
        ) from e

    return response["message"]["content"].strip()
