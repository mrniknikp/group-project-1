from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Float
from datetime import datetime, date

class Base(DeclarativeBase):
    pass

class Basemodel(Base):
    __tablename__ = "items"

    ###
    # SYSTEM FIELDS
    ###

    status: Mapped[str] = mapped_column(String[100], init = False)
    delivered_at: int = None
    failed_at: int = None
    failure_code: int = None
    failure_code: int = None
    point_awarded: int = 0


    ###
    # OTHER FIELDS
    ###

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(25))
    description: Mapped[str] = mapped_column(String(200))
    tax: Mapped[float] = mapped_column(Float)

class ErrorDetail(Base):
    resource: str = None
    id: str = None # ID of order (fmt: "ord_[ID]")


class Error(Base):
     """
    {
    "code": "not_found",
    "message": "Объект не найден.",
    "details": {
        "resource": "order",
        "id": "ord_9999"
        }
    }

     """
     code: int = None
     message: str = None
     details: object = ErrorDetail()

class Cargo():
    description: str = None
    cargo_type: str = None

class Route():
    route_id: str = None
    leg_ids: list[str] = []

class Input():
    """
    {
    "request_id": "req_5001",
    "origin": "moscow",
    "destination": "vladivostok",
    "allowed_modes": ["rail", "road"],
    "weight_t": 18.5,
    "cargo": {
        "description": "Сборный груз, контейнер 40 футов.",
        "cargo_type": "general"
    },
    "response_deadline": "2026-11-19T18:00:00Z",
    "points_reward": 100,
    "route_hints": [
        {"route_id": "rte_5001", "leg_ids": ["leg_5001_1", "leg_5001_2"]}
    ],
    "comment": "Погрузка на терминале заказчика."
    }

    """

    request_id: str = None #id of request (req_[ID])
    origin: str = None #Пункт отправки
    destination: str = None #Пункт назначения
    allowed_modes: list[str] = [] #list of allowed modes
    weight_t: float = 0.0 #weight in tons
    cargo: Cargo
    response_deadline: str = None
    point_reward: int = 0
    route_hints: Route
    comment: str = None

class Qoute():
    """
    {
    "quote_id": "quo_7008",
    "price_rub": 1950000,
    "duration_hours": 210,
    "route_id": "rte_5001",
    "leg_ids": ["leg_5001_1"],
    "valid_until": "2026-11-20T18:00:00Z",
    "comment": "Ж/д до Хабаровска, авто до Владивостока."
    }

    """
    quote_id: str = None # quo_[ID]
    price_rub: int = None
    duration_hours: float = None
    route_id: str = None # rte_[ID]
    leg_ids: list[str]
    valid_until: str = None
    comment: str = None

class Notification():
    """
    {
    "order_id": "ord_6003",
    "status": "delivered",
    "delivered_at": "2026-11-17T03:40:00Z",
    "route_id": "rte_5003",
    "leg_ids": [
        "leg_5003_1"
    ],
    "points_awarded": 100
    }
    """

    order_id: str = None # ord_[ID]
    status: str = None # delivered | canceled | faild
    delivered_at: str = None if status == "delivered" else "Not delivered"
    route_id: str = None
    leg_ids: list[str] = None
    points_awarded: int = 0
