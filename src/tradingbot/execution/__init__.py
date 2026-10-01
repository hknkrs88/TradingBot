"""Order execution. Intentionally refuses to place orders in this skeleton."""


class OrdersDisabledError(RuntimeError):
    pass


def place_order(*_args, **_kwargs) -> None:
    raise OrdersDisabledError("order placement is not implemented yet")
