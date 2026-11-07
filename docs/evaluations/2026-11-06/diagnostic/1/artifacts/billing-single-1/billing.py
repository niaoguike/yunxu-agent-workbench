from decimal import (
    Context, Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, ROUND_HALF_UP,
    localcontext,
)


def _decimal_string(value, field):
    if not isinstance(value, str):
        raise ValueError(f'{field} must be a decimal string')
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f'{field} must be a decimal string') from exc
    if not number.is_finite():
        raise ValueError(f'{field} must be finite')
    return number


def invoice_total(items, discount='0'):
    """Return the discounted total, rounding once to cents using half up."""
    if not isinstance(items, list):
        raise ValueError('items must be a list')
    discount = _decimal_string(discount, 'discount')
    if not Decimal(0) <= discount <= Decimal(100):
        raise ValueError('discount must be between 0 and 100')

    lines = []
    total_digits = 1
    for item in items:
        if not isinstance(item, dict) or 'price' not in item or 'qty' not in item:
            raise ValueError('each item must be a dict with price and qty')
        price = _decimal_string(item['price'], 'price')
        if price < 0 or price.as_tuple().exponent < -2:
            raise ValueError('price must be nonnegative with at most two decimal places')
        qty = item['qty']
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ValueError('qty must be a positive int, never bool')
        quantity = Decimal(qty)
        lines.append((price, quantity))
        # Upper bound on the digits needed for an exact line total in cents.
        if price:
            total_digits = max(total_digits, price.adjusted() + 1
                               + quantity.adjusted() + 1 + 2)

    if not lines:
        return '0.00'

    # Allow for summation carry and every fractional discount digit, so only
    # quantize rounds. Use a fresh context independent of the caller's settings.
    precision = (total_digits + len(str(len(lines)))
                 + max(0, -discount.as_tuple().exponent) + 4)
    context = Context(prec=precision, rounding=ROUND_HALF_UP,
                      Emin=MIN_EMIN, Emax=MAX_EMAX)
    with localcontext(context):
        total = sum((price * qty for price, qty in lines), Decimal(0))
        total = total * (Decimal(100) - discount) / Decimal(100)
        rounded = total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        # Normalize signed zero from valid inputs such as '-0.00'.
        return '0.00' if rounded == 0 else format(rounded, '.2f')
