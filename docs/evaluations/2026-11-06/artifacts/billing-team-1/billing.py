def invoice_total(items, discount='0'):
    return str(sum(float(i['price']) * i['qty'] for i in items) * (1 - float(discount) / 100))
