"""Pure fixed-point pricing; reasoning tokens are already included in output tokens."""

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

MILLI = 1000
POLICY_VERSION = "v1"


def snapshot_weight(value: float | Decimal) -> Decimal:
    result = Decimal(str(value)).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    if not result.is_finite() or result < 0:
        raise ValueError("pricing weights must be finite and nonnegative")
    return result


def weighted_amount(input_tokens: int, cached_tokens: int, output_tokens: int, weight: Decimal, cached_weight: Decimal) -> int:
    if min(input_tokens, cached_tokens, output_tokens) < 0 or cached_tokens > input_tokens:
        raise ValueError("invalid token counts")
    tokens = Decimal(input_tokens - cached_tokens + output_tokens) + cached_tokens * cached_weight
    return int((tokens * weight * MILLI).to_integral_value(rounding=ROUND_CEILING))


def reserved_amount(input_tokens: int, output_tokens: int, weight: Decimal, cached_weight: Decimal) -> int:
    # Assume no cache hits, or the more expensive cached price if configured above one.
    return weighted_amount(input_tokens, input_tokens, output_tokens, weight, max(Decimal(1), cached_weight))
