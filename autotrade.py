# pip install pyjwt requests python-dotenv pandas pandas_ta schedule google-generativeai
import os
import time
import uuid
import jwt
import hashlib
import json
import requests
from urllib.parse import urlencode
import pandas as pd
import pandas_ta as ta
import schedule
import re
import google.generativeai as genai
from dotenv import load_dotenv

# -------------------------
# 환경 변수
# -------------------------
load_dotenv()
ACCESS_KEY = os.getenv("BITHUMB_API_KEY").strip()
SECRET_KEY = os.getenv("BITHUMB_API_SECRET").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
API_URL = "https://api.bithumb.com"

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

# -------------------------
# JWT + query_hash 생성
# -------------------------
def generate_jwt(request_body=None):
    payload = {
        "access_key": ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000)
    }
    if request_body:
        query_string = urlencode(request_body).encode()
        query_hash = hashlib.sha512(query_string).hexdigest()
        payload['query_hash'] = query_hash
        payload['query_hash_alg'] = 'SHA512'
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    return f"Bearer {token}"

# -------------------------
# 계좌 조회
# -------------------------
def get_current_status():
    headers = {"Authorization": generate_jwt()}
    url = API_URL + "/v1/accounts"
    try:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        data = res.json()
        if not isinstance(data, list):
            print("API 반환 형식 오류:", data)
            return None
        btc_balance = krw_balance = 0.0
        for asset in data:
            if asset['currency'] == 'BTC':
                btc_balance = float(asset['balance'])
            elif asset['currency'] == 'KRW':
                krw_balance = float(asset['balance'])
        print(f"=== 현재 계좌 잔고 === BTC:{btc_balance}, KRW:{krw_balance}")
        return {"btc_balance": btc_balance, "krw_balance": krw_balance}
    except Exception as e:
        print("계좌 조회 오류:", e)
        return None

# -------------------------
# OHLCV 조회
# -------------------------
def fetch_bithumb_ohlcv(interval="24h", count=30):
    res = requests.get(f"{API_URL}/public/candlestick/BTC_KRW/{interval}")
    data = res.json()
    if data['status'] != '0000':
        raise Exception(f"Bithumb OHLCV error: {data}")
    df = pd.DataFrame(data['data'])
    df.columns = ['time','open','close','high','low','volume']
    df = df.astype(float)
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    return df.head(count)

# -------------------------
# 기술적 지표 계산
# -------------------------
def fetch_and_prepare_data():
    df_daily = fetch_bithumb_ohlcv("24h", 30)
    df_hourly = fetch_bithumb_ohlcv("1h", 24)

    def add_indicators(df):
        df['SMA_10'] = ta.sma(df['close'], length=10)
        df['EMA_10'] = ta.ema(df['close'], length=10)
        df['RSI_14'] = ta.rsi(df['close'], length=14)
        stoch = ta.stoch(df['high'], df['low'], df['close'], k=14, d=3, smooth_k=3)
        if stoch is not None:
            df = df.join(stoch)
        ema_fast = df['close'].ewm(span=12, adjust=False).mean()
        ema_slow = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema_fast - ema_slow
        df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']
        df['Middle_Band'] = df['close'].rolling(window=20).mean()
        std_dev = df['close'].rolling(window=20).std()
        df['Upper_Band'] = df['Middle_Band'] + (std_dev * 2)
        df['Lower_Band'] = df['Middle_Band'] - (std_dev * 2)
        return df

    df_daily = add_indicators(df_daily)
    df_hourly = add_indicators(df_hourly)
    combined_df = pd.concat([df_daily, df_hourly], keys=['daily','hourly'])
    return combined_df.to_json(orient='split')

# -------------------------
# Gemini 응답 JSON 파싱
# -------------------------
def parse_gemini_response(text):
    cleaned = re.sub(r"^```json\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    return json.loads(cleaned)

# -------------------------
# AI 분석
# -------------------------
def get_instructions(file_path="instructions.md"):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "AI 분석을 위한 지침이 없습니다."

def analyze_data_with_gemini(data_json):
    instructions = get_instructions()
    current_status = get_current_status()
    if not current_status:
        return None
    prompt = f"""
{instructions}

비트코인 데이터:
{data_json}

현재 계정 상태:
{json.dumps(current_status)}

반드시 JSON 형식으로 응답:
{{
  "decision": "buy" | "sell" | "hold",
  "reason": "간단한 이유(한글)"
}}
"""
    response = model.generate_content(prompt)
    if not response or not response.text:
        print("Gemini 응답 없음")
        return None
    return response.text.strip()

# -------------------------
# 매수/매도 (손절/익절 포함)
# -------------------------
TAKE_PROFIT_RATIO = 1.02  # 2% 수익 시 익절
STOP_LOSS_RATIO   = 0.98  # 2% 손절

def execute_order(order_type="buy", amount=0.001, price=None):
    request_body = {
        "market": "KRW-BTC",
        "side": "bid" if order_type=="buy" else "ask",
        "volume": amount,
        "ord_type": "market"
    }
    headers = {"Authorization": generate_jwt(request_body), "Content-Type": "application/json"}
    try:
        res = requests.post(API_URL + "/v1/orders", data=json.dumps(request_body), headers=headers)
        print(f"{order_type.upper()} 주문 응답:", res.json())
        return res.json()
    except Exception as e:
        print(f"{order_type.upper()} 주문 오류:", e)
        return None

def execute_buy():
    status = get_current_status()
    if status and status['krw_balance'] > 1000:  # 소액 공격적 전략
        price = fetch_bithumb_ohlcv("1h",1)['close'].iloc[-1]
        amount = (status['krw_balance'] * 0.9995) / price
        execute_order("buy", round(amount,8))

def execute_sell():
    status = get_current_status()
    if status and status['btc_balance'] > 0.00005:
        execute_order("sell", round(status['btc_balance'],8))

# -------------------------
# 전체 흐름
# -------------------------
def make_decision_and_execute():
    print("=== 매매 판단 시작 ===")
    data_json = fetch_and_prepare_data()
    advice = analyze_data_with_gemini(data_json)
    if not advice:
        print("AI 분석 실패")
        return
    try:
        decision = parse_gemini_response(advice)
        print(f"AI 결정: {decision}")
        if decision.get("decision") == "buy":
            execute_buy()
        elif decision.get("decision") == "sell":
            execute_sell()
        else:
            print("보유 유지")
    except Exception as e:
        print("JSON 파싱 실패:", e)
        print("원본:", advice)

# -------------------------
# 스케줄링
# -------------------------
if __name__ == "__main__":
    make_decision_and_execute()
    schedule.every().hour.at(":01").do(make_decision_and_execute)
    while True:
        schedule.run_pending()
        time.sleep(1)