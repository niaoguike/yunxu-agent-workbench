"""Invoice totals calculated exactly, with one final rounding to cents."""

import re
from decimal import (
    Context, Decimal, DecimalException, MAX_EMAX, MIN_EMIN,
    ROUND_HALF_UP, localcontext,
)


_DECIMAL_STRING = re.compile(
    r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
)


def _decimal_string(value, field):
    if not isinstance(value, str) or not _DECIMAL_STRING.fullmatch(value):
        raise ValueError(f'{field} must be a finite decimal string')
    try:
        result = Decimal(value)
    except (DecimalException, ValueError) as exc:
        raise ValueError(f'{field} must be a finite decimal string') from exc
    if not result.is_finite():
        raise ValueError(f'{field} must be finite')
    return result


def invoice_total(items, discount='0'):
    """Validate inputs and return the discounted total as a two-decimal string.

    Price precision is determined by its Decimal exponent: trailing fractional
    zeros count as decimal places. Scientific decimal notation is accepted.
    """
    if not isinstance(items, list):
        raise ValueError('items must be a list')
    rate = _decimal_string(discount, 'discount')
    if not 0 <= rate <= 100:
        raise ValueError('discount must be between 0 and 100')

    lines = []
    total_digits = 1
    for item in items:
        if not isinstance(item, dict) or 'price' not in item or 'qty' not in item:
            raise ValueError('each item must be a dict containing price and qty')
        price = _decimal_string(item['price'], 'price')
        if price < 0 or price.as_tuple().exponent < -2:
            raise ValueError('price must be nonnegative with at most two decimal places')
        qty = item['qty']
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ValueError('qty must be a positive int, never bool')
        quantity = Decimal(qty)
        if price:
            total_digits = max(
                total_digits, max(1, price.adjusted() + 1) + quantity.adjusted() + 1
            )
        lines.append((price, quantity))

    if not lines or rate == 100:
        return '0.00'
    # Zero exponents do not need to inflate the arithmetic context.
    if not rate:
        rate = Decimal(0)
    # Allow every subtotal digit, possible carry from summation, and every
    # fractional discount digit. All arithmetic before quantize stays exact.
    precision = total_digits + len(str(len(lines))) + max(0, -rate.as_tuple().exponent) + 6
    context = Context(
        prec=precision, rounding=ROUND_HALF_UP, Emax=MAX_EMAX, Emin=MIN_EMIN
    )
    with localcontext(context):
        total = sum((price * qty for price, qty in lines), Decimal(0))
        discounted = total * (Decimal(100) - rate) / Decimal(100)
        rounded = discounted.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        # Decimal preserves a negative sign on zero; invoices always use 0.00.
        return format(abs(rounded), '.2f')
