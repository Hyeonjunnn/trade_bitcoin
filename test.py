# pip install pyjwt requests python-dotenv
import os
import time
import uuid
import jwt
import requests
from dotenv import load_dotenv

# -------------------------
# 환경 변수 불러오기
# -------------------------
load_dotenv()
ACCESS_KEY = os.getenv("BITHUMB_API_KEY").strip()
SECRET_KEY = os.getenv("BITHUMB_API_SECRET").strip()
API_URL = "https://api.bithumb.com"

# -------------------------
# JWT 토큰 생성
# -------------------------
def generate_jwt():
    payload = {
        "access_key": ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    return f"Bearer {token}"

# -------------------------
# 잔고 조회
# -------------------------
def get_balance():
    headers = {"Authorization": generate_jwt()}
    url = API_URL + "/v1/accounts"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()  # list 형태 반환

        if not isinstance(data, list):
            print("API 반환 형식 오류:", data)
            return None

        print("=== Bithumb 계좌 조회 ===")
        for asset in data:
            print(f"{asset['currency']}: balance={asset['balance']}, locked={asset['locked']}")
        print("=======================")
        return data

    except requests.exceptions.HTTPError as http_err:
        print("HTTP 에러:", http_err)
    except Exception as err:
        print("오류 발생:", err)

# -------------------------
# 실행
# -------------------------
if __name__ == "__main__":
    get_balance()