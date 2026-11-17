from fastapi import FastAPI, status, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy_utils import database_exists, create_database
import sqlalchemy as sa

from dotenv import load_dotenv
import os

load_dotenv()

PASSWORD = os.getenv("PASSWORD_DB")

print(PASSWORD)

class Item(BaseModel):
    name: str
    description: str | None = None
    price: float
    tax: float | None = None

app = FastAPI()

@app.post("/items")
def create_item(item: Item):
    total = item.price + (item.tax or 0)
    return {"item": item, "total_price": total}

@app.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(item: Item):
    if item.price < 0:
        raise HTTPException(
            status_code=400,
            detail="Цена не может быть отрицательной"
        )
    return item

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

@app.get("/users/{user_id}")
def get_user(user_id: int):
    return {"user_id": user_id}

@app.get("/items")
def list_items(skip: int = 0, limit: int = 10, q: str | None = None):
    return {"skip": skip, "limit": limit, "q": q}

@app.get("/items/{item_id}")
def get_item(item_id: int):
    return {"item_id": item_id}

engine = create_engine(f"postgresql+psycopg://admin:{PASSWORD}@127.0.0.1:5432/mydb")
if not database_exists(engine.url):
    create_database(engine.url)

print(database_exists(engine.url))



# @app.get("/items")       
# @app.post("/items")      
# @app.put("/items/{id}")  
# @app.patch("/items/{id}")
# @app.delete("/items/{id}")