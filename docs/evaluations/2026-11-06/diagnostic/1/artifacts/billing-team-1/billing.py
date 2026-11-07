"""Invoice totals calculated with exact decimal arithmetic."""

from decimal import (
    Context, Decimal, DecimalException, MAX_EMAX, MIN_EMIN,
    ROUND_HALF_UP, localcontext,
)


def _decimal_string(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a decimal string")
    try:
        number = Decimal(value)
    except (DecimalException, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal string") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def invoice_total(items, discount='0'):
    """Validate an invoice and round its discounted total once to cents."""
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    discount_value = _decimal_string(discount, "discount")
    if not 0 <= discount_value <= 100:
        raise ValueError("discount must be between 0 and 100")

    validated = []
    total_digits = 1
    for item in items:
        if not isinstance(item, dict) or 'price' not in item or 'qty' not in item:
            raise ValueError("each item must be a dict with price and qty")
        price = _decimal_string(item['price'], "price")
        qty = item['qty']
        if price < 0 or price.as_tuple().exponent < -2:
            raise ValueError("price must be nonnegative with at most two decimal places")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise ValueError("qty must be a positive int, never bool")
        validated.append((price, qty))
        if price:
            total_digits = max(
                total_digits, price.adjusted() + Decimal(qty).adjusted() + 2
            )

    if not validated:
        return '0.00'

    # Allow every subtotal digit, carry from summation, and discount digit.
    # A fresh context also makes the result independent of caller precision/traps.
    discount_places = max(0, -discount_value.as_tuple().exponent) if discount_value else 0
    precision = total_digits + len(str(len(validated))) + discount_places + 6
    context = Context(prec=precision, Emin=MIN_EMIN, Emax=MAX_EMAX)
    with localcontext(context):
        subtotal = sum((price * qty for price, qty in validated), Decimal(0))
        total = subtotal * (Decimal(100) - discount_value) / Decimal(100)
        rounded = total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if not rounded:
        rounded = rounded.copy_abs()
    return format(rounded, '.2f')
