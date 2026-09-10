import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

# 每 1M tokens 的价格 (USD)，按需扩展
MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
}


class LLMClient:
    """OpenAI 兼容的 LLM 客户端，带 token 用量和费用统计"""

    def __init__(self, model, api_key, base_url=None, temperature=0.7, max_tokens=2048):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0

    def chat(self, user_prompt, system_prompt=None, history=None):
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})

        response = self.client.chat.completions.create(
            model=self.model, messages=messages,
            temperature=self.temperature, max_tokens=self.max_tokens,
        )

        usage = response.usage
        if usage:
            inp, out = usage.prompt_tokens, usage.completion_tokens
            self.total_input_tokens += inp
            self.total_output_tokens += out
            cost = self._calc_cost(inp, out)
            self.total_cost += cost
            logger.info(f"  Tokens: {inp} in / {out} out | Cost: ${cost:.4f} (total: ${self.total_cost:.4f})")

        return response.choices[0].message.content

    def _calc_cost(self, input_tokens, output_tokens):
        pricing = MODEL_PRICING.get(self.model, {"input": 0, "output": 0})
        return input_tokens / 1_000_000 * pricing["input"] + \
               output_tokens / 1_000_000 * pricing["output"]

    def usage_summary(self) -> dict:
        return {
            "model": self.model,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost, 6),
        }
