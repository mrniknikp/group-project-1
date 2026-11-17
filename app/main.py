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

engine = create_engine(f"postgresql+psycopg://admin:{PASSWORD}@127.0.0.1:5432/mydb")
if not database_exists(engine.url):
    create_database(engine.url)

print(database_exists(engine.url))



# @app.get("/items")       
# @app.post("/items")      
# @app.put("/items/{id}")  
# @app.patch("/items/{id}")
# @app.delete("/items/{id}")