from curl_cffi import requests
import os
import re
import time
import logging
from urllib.parse import urljoin
import json
import urllib.request

# 設定日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 網站設定
BASE_URL = 'https://zodgame.xyz/'
SIGN_URL = urljoin(BASE_URL, 'plugin.php?id=dsu_paulsign:sign')

# 重試設定
MAX_RETRIES = 2
RETRY_DELAY = 10
MAX_ROUNDS = 12        # 最多幾輪（每輪 MAX_RETRIES 次）
LONG_RETRY_DELAY = 3600  # 輪次之間的等待（秒）

# 必要的 cookie 名稱（只保留這四個，cf_clearance 不帶）
ESSENTIAL_COOKIES = [
    'qhMq_2132_saltkey',
    'qhMq_2132_auth',
    'qhMq_2132_lastvisit',
    'qhMq_2132_ulastactivity'
]

def send_pushplus(title, content):
    """發送 PushPlus 通知"""
    token = os.environ.get("PUSH_PLUS_TOKEN")
    if not token:
        logger.info("未配置 PUSH_PLUS_TOKEN，跳過消息推送。")
        return

    url = "http://www.pushplus.plus/send"
    data = json.dumps({
        "token": token,
        "title": title,
        "content": content
    }).encode('utf-8')

    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('code') == 200:
                logger.info("PushPlus 推送成功！")
            else:
                logger.error(f"PushPlus 推送失敗：{result}")
    except Exception as e:
        logger.error(f"PushPlus 推送發生異常：{e}")

def parse_cookies(cookie_str):
    """解析 cookie 字串並只保留必要的 cookie"""
    cookies = {}
    for item in cookie_str.split(';'):
        item = item.strip()
        if not item or '=' not in item:
            continue
        name, value = item.split('=', 1)
        name = name.strip()
        if name in ESSENTIAL_COOKIES:
            cookies[name] = value.strip()
    return cookies

def check_sign_status(response_text):
    """檢查簽到狀態"""
    if '您今天已經簽到過了' in response_text or '您今天已经签到过了' in response_text:
        return 'already_signed'
    elif '您還未登錄' in response_text or '您还未登录' in response_text:
        return 'not_logged_in'
    return 'ready_to_sign'

def extract_formhash(html_content):
    """從 HTML 內容中提取 formhash"""
    match = re.search(r'name="formhash" value="([^"]+)"', html_content)
    return match.group(1) if match else None

def extract_reward(response_text):
    """從回應中提取獎勵資訊"""
    match = re.search(r'获得随机奖励\s*酱油\s*(\d+)\s*瓶', response_text)
    return match.group(1) if match else None

def sign_with_retry(cookies, max_retries=MAX_RETRIES, retry_delay=RETRY_DELAY):
    """帶有重試機制的簽到函數（多輪重試，輪次間長等待）"""
    for round_num in range(MAX_ROUNDS):
        if round_num > 0:
            logger.info(f"等待 {LONG_RETRY_DELAY} 秒後開始第 {round_num + 1} 輪重試...")
            time.sleep(LONG_RETRY_DELAY)

        for attempt in range(max_retries):
            try:
                session = requests.Session(impersonate="chrome120")
                session.cookies.update(cookies)
                session.headers.update({
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                    'Referer': SIGN_URL
                })

                response = session.get(SIGN_URL, timeout=30)
                response.raise_for_status()

                status = check_sign_status(response.text)
                if status == 'already_signed':
                    msg = "今天已經簽到過了"
                    logger.info(msg)
                    return True, msg
                elif status == 'not_logged_in':
                    msg = "Cookie 已過期或無效，請更新 ZODGAME_COOKIE"
                    logger.error(msg)
                    return False, msg

                formhash = extract_formhash(response.text)
                if not formhash:
                    msg = "無法獲取 formhash，可能未正確登入"
                    logger.error(msg)
                    return False, msg

                sign_data = {
                    'formhash': formhash,
                    'qdxq': 'kx',
                    'qdmode': '1',
                    'todaysay': '每日簽到',
                    'fastreply': '0'
                }

                sign_url = f"{SIGN_URL}&operation=qiandao&infloat=1&inajax=1"
                sign_response = session.post(sign_url, data=sign_data, timeout=30)
                sign_response.raise_for_status()

                if "恭喜你签到成功" in sign_response.text or "簽到成功" in sign_response.text:
                    reward = extract_reward(sign_response.text)
                    if reward:
                        msg = f"簽到成功！獲得酱油 {reward} 瓶"
                    else:
                        msg = "簽到成功！"
                    logger.info(msg)
                    return True, msg
                elif "已經簽到" in sign_response.text or "已经签到" in sign_response.text:
                    msg = "今天已經簽到過了"
                    logger.info(msg)
                    return True, msg
                else:
                    logger.warning(f"簽到回應不符合預期：{sign_response.text[:200]}...")

            except Exception as e:
                logger.error(f"第 {round_num + 1} 輪第 {attempt + 1} 次嘗試失敗：{str(e)}")

            if attempt < max_retries - 1:
                logger.info(f"等待 {retry_delay} 秒後重試...")
                time.sleep(retry_delay)

    msg = f"已重試 {MAX_ROUNDS} 輪，簽到失敗"
    logger.error(msg)
    return False, msg

def main():
    try:
        cookie_str = os.environ.get('ZODGAME_COOKIE')
        if not cookie_str:
            msg = "請設定 ZODGAME_COOKIE 環境變數"
            logger.error(msg)
            send_pushplus("ZodGame 簽到失敗", msg)
            exit(1)

        cookies = parse_cookies(cookie_str)
        logger.info(f"已載入 {len(cookies)} 個 cookie")

        success, msg = sign_with_retry(cookies)
        if success:
            logger.info("簽到操作完成")
            send_pushplus("ZodGame 簽到成功", msg)
            exit(0)
        else:
            logger.error("簽到失敗！")
            send_pushplus("ZodGame 簽到失敗", msg)
            exit(1)
    except Exception as e:
        msg = f"發生未預期的錯誤：{str(e)}"
        logger.exception(msg)
        send_pushplus("ZodGame 簽到異常", msg)
        exit(1)

if __name__ == "__main__":
    main()
