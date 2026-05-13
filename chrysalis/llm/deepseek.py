"""通过 OpenAI-compatible API 调用 DeepSeek。"""

from dataclasses import dataclass

from openai import OpenAI, OpenAIError

from chrysalis.config import LLMConfig


@dataclass
class ChatResult:
    text: str


class DeepSeekChat:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )

    def chat(self, messages: list[dict]) -> ChatResult:
        if not self.config.api_key:
            raise RuntimeError("没有配置 CHRYSALIS_API_KEY，请先在 .env 里写入 DeepSeek API Key。")

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
        except OpenAIError as exc:
            raise RuntimeError(f"DeepSeek 请求失败：{exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"DeepSeek 请求异常：{exc}") from exc
        return ChatResult(text=response.choices[0].message.content or "")
