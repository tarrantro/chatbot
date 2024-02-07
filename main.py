from fastapi import FastAPI, Request
from dotenv import dotenv_values
from pymongo import MongoClient
from typing import List, Optional
from contextlib import asynccontextmanager
from pymongo.server_api import ServerApi
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
import requests
import json
import datetime

config = dotenv_values(".env")


class User(BaseModel):
    name: str
    last_access: Optional[List[int]] = []
    message_count: Optional[int] = 0


class Message(BaseModel):
    user_name: str
    timestamp: Optional[int] = 0
    message: str
    reply: str = None


class MessageHistory(BaseModel):
    user_name: str
    last_n: Optional[int] = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.mongodb_client = MongoClient(
        config["ATLAS_URI"],
        maxIdleTimeMS=60000,
        timeoutMS=60000,
        server_api=ServerApi("1"),
    )
    app.mongodb_client.admin.command("ping")
    app.database = app.mongodb_client[config["DB_NAME"]]
    app.collection = app.database.get_collection(config["COLLECTION_NAME"])
    print("Connected to the MongoDB database!")
    yield
    app.mongodb_client.close()


app = FastAPI(lifespan=lifespan)


@app.post("/register")
def register(user: User):
    result = app.collection.insert_one(jsonable_encoder(user))
    return str(result.inserted_id)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.post("/get_ai_chat_response")
def get_ai_chat_response(message: Message):
    msg = jsonable_encoder(message)
    token = config["OPENAPI_TOKEN"]
    user_data = app.collection.find_one({"name": msg.get("user_name")})
    if user_data is None:
        return f'invalid user {user_data.get("user_name")}'

    current_time = datetime.datetime.now()
    time_stamp = int(round(current_time.timestamp()))
    l = user_data.get("last_access")
    if len(l) >= 3:
        sorted(l)
        if time_stamp - l[-3] < 30:
            return f"each user can send 3 message per 30 sec"
        if len(l) >= 20:
            if time_stamp - l[-20] < 86400:
                return f"each user can send 20 message per day"
            del l[0]
    l.append(time_stamp)
    user_data["last_access"] = l

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {token}",
        },
        data=json.dumps(
            {
                "model": "mistralai/mistral-7b-instruct:free",  # Optional
                "messages": [{"role": "user", "content": f'{msg.get("message")}'}],
            }
        ),
        timeout=10,
    )

    choices = json.loads(response.text).get("choices")
    rs = ""
    if len(choices) > 0:
        rs = choices[0].get("message").get("content")
        c = int(user_data.get("message_count", 0))
        c += 1
        user_data["message_count"] = c
        msg["timestamp"] = time_stamp
        msg["reply"] = rs
        uid = user_data["_id"]
        del user_data["_id"]
        try:
            app.collection.update_one(
                {"_id": uid}, {"$set": jsonable_encoder(user_data)}
            )
            app.collection.insert_one(jsonable_encoder(msg))
        except Exception as e:
            print(e)
            return "failed to update database"
    return rs


@app.post("/get_user_chat_history")
def get_user_chat_history(history: MessageHistory):
    body = jsonable_encoder(history)
    user_name = body.get("user_name")
    last_n = body.get("last_n")

    if user_name:
        user_data = app.collection.find_one({"name": user_name})
        if user_data is None:
            return f"invalid user {user_name}"

    msg_data = (
        app.collection.find({"user_name": user_name}).sort("timestamp").limit(last_n)
    )

    rs = [{"user": r.get("message"), "ai": r.get("reply")} for r in msg_data]
    return rs


@app.post("/get_chat_status_today")
def get_chat_status_today(user: User):
    body = jsonable_encoder(user)
    result = app.collection.find_one({"name": body.get("name")})
    if result:
        return {
            "user_name": result.get("name"),
            "chat_cnt": result.get("message_count"),
        }
    return {}
