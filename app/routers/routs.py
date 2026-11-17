"""
GET 	/healthz 	getHealth 	Живость и готовность, без ключа и БД
GET 	/v1/requests 	listRequests 	Заявки, переданные команде (массив)
POST 	/v1/requests 	createRequest 	Принять RFQ от портала (Idempotency-Key)
GET 	/v1/requests/{request_id}/quotes 	listQuotes 	Свои котировки по заявке (массив)
POST 	/v1/requests/{request_id}/quotes 	createQuote 	Опубликовать котировку
GET 	/v1/orders/{order_id} 	getOrder 	Read-only снимок заказа
POST 	/v1/orders/{order_id}/notify 	notifyOrder 	Принять факт системной доставки
GET 	/v1/points 	listPoints 	Баллы команды (массив)

структура:
api/
├── openapi.yaml   # контракт
├── examples/      # 4 тела: request, quote, order-notify, error
├── mocks/         # 6 согласованных наборов
└── viewer.py      # Swagger UI и Redoc

ERRORS:

{"code": string, "message": string, "details": object?}.

"""

from fastapi import FastAPI, exceptions, status
from models import Basemodel


app = FastAPI()

@app.get("/healthz")
def healthz():
    """
    Работоспособность API
    """
    return

@app.get("/v1/requests")
def requests_get():
    """
    Заявки для API (в виде массива)
    """
    return

@app.post("/v1/requests")
def requests_post():
    """
    Заявка для API (создание)
    """
    return

@app.get("/requests/{request_id}/quotes")
def requests_by_id_get():
    """
    
    """
    return

@app.post("/requests/{request_id}/quotes")
def requests_by_id_post():
    return

@app.get("/v1/orders/{order_id}")
def order_get_by_id():
    return


@app.post("/v1/orders/{order_id}/notify")
def notify():
    return


@app.get("/v1/points")
def points():
    return
