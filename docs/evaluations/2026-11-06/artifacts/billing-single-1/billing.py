import re
from decimal import Context, Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, ROUND_HALF_UP, localcontext


_DECIMAL_STRING = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')


def _decimal_string(value, field):
    if not isinstance(value, str) or not _DECIMAL_STRING.fullmatch(value):
        raise ValueError(f'{field} must be a finite decimal string')
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f'{field} must be a finite decimal string') from error
    if not number.is_finite():
        raise ValueError(f'{field} must be finite')
    return number


def invoice_total(items, discount='0'):
    """Validate an invoice and round its discounted total once, half up."""
    if not isinstance(items, list):
        raise ValueError('items must be a list')
    discount_value = _decimal_string(discount, 'discount')
    if not 0 <= discount_value <= 100:
        raise ValueError('discount must be between 0 and 100')

    lines = []
    precision = 1
    for item in items:
        if not isinstance(item, dict) or 'price' not in item or 'qty' not in item:
            raise ValueError('each item must contain price and qty')
        price = _decimal_string(item['price'], 'price')
        if price < 0 or price.as_tuple().exponent < -2:
            raise ValueError('price must be nonnegative with at most two decimal places')
        qty = item['qty']
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ValueError('qty must be a positive integer')
        quantity = Decimal(qty)
        lines.append((price, quantity))
        # Bound the digits needed by each exact line total, including cents.
        precision = max(precision, max(1, price.adjusted() + 1)
                        + len(quantity.as_tuple().digits) + 2)

    if not lines:
        return '0.00'

    # Allow carry digits from summation and every fractional discount digit.
    precision += len(str(len(lines))) + max(0, -discount_value.as_tuple().exponent) + 5
    context = Context(prec=precision, Emin=MIN_EMIN, Emax=MAX_EMAX)
    with localcontext(context):
        total = sum((price * qty for price, qty in lines), Decimal(0))
        total = total * (Decimal(100) - discount_value) / Decimal(100)
        rounded = total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return format(rounded.copy_abs(), '.2f')
