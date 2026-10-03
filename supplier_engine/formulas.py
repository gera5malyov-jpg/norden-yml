import ast

_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}
_ALLOWED_UNARY = {
    ast.UAdd: lambda a: a,
    ast.USub: lambda a: -a,
}


def evaluate_formula(expression, variables):
    """Evaluate a deliberately tiny arithmetic language; never use Python eval()."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Price formula must be a non-empty string")
    tree = ast.parse(expression, mode="eval")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError("Unknown formula variable: %s" % node.id)
            value = variables[node.id]
            if value is None:
                raise ValueError("Formula variable %s is empty" % node.id)
            return float(value)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("Division by zero in formula")
            return _ALLOWED_BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](walk(node.operand))
        raise ValueError("Unsupported syntax in price formula")

    return walk(tree)


def apply_price_formulas(product, formulas):
    if not formulas:
        return product
    source_price = product.price
    variables = {
        "supplier_price": source_price,
        "purchase_price": product.purchase_price,
        "price": product.price,
        "compare_price": product.compare_price,
        "stock": product.stock,
    }
    allowed_targets = {"purchase_price", "price", "compare_price"}
    for field, expression in formulas.items():
        if field not in allowed_targets:
            raise ValueError("Unsupported formula target: %s" % field)
        value = round(evaluate_formula(expression, variables), 2)
        setattr(product, field, value)
        variables[field] = value
    return product
