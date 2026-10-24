from enum import StrEnum


class Currency(StrEnum):
    PKR = "PKR"
    USD = "USD"


class OrderStatus(StrEnum):
    OPEN = "open"
    RECEIVED = "received"
    CANCELLED = "cancelled"
