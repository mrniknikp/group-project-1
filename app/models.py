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
    detail_description: str = None


class Error(Base):
     code: int = None
     message: str = None
     details: object = ErrorDetail()