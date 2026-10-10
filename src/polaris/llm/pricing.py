"""各模型每百万 token 的美元单价（输入, 输出）。来源见 docs/FINDINGS.md 第 2 节。

没查到单价的模型不写，费用记成空，不瞎估。开发期用的模型查到价格后补在这里。
"""
from decimal import Decimal

PRICES: dict[str, tuple[str, str]] = {
    "nvidia/Nemotron-3_5-Lightning": ("0.06", "0.24"),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": ("0.06", "0.24"),
    "nvidia/nemotron-3-super-120b-a12b": ("0.30", "0.90"),
    "nvidia/Nemotron-3-Ultra-550b-a55b": ("1.00", "3.00"),
    "Qwen/Qwen3-Embedding-8B": ("0.01", "0"),
}

MILLION = Decimal(1_000_000)


def cost_usd(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> Decimal | None:
    price = PRICES.get(model)
    if price is None or prompt_tokens is None:
        return None
    p_in, p_out = (Decimal(x) for x in price)
    total = Decimal(prompt_tokens) * p_in + Decimal(completion_tokens or 0) * p_out
    return (total / MILLION).quantize(Decimal("0.000001"))
