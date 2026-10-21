import os
import requests
import json
import time
from datetime import datetime, timezone
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN = os.environ['TINKOFF_TOKEN']
APPS_SCRIPT_URL = os.environ['APPS_SCRIPT_URL']

HEADERS = {
    'Authorization': f'Bearer {TOKEN}',
    'Content-Type': 'application/json'
}

def tinkoff_request(method, payload):
    url = f'https://invest-public-api.tinkoff.ru/rest/tinkoff.public.invest.api.contract.v1.{method}'
    resp = requests.post(url, headers=HEADERS, json=payload, verify=False)
    if resp.status_code != 200:
        raise Exception(f"Ошибка API {method}: {resp.text}")
    return resp.json()

def parse_quotation(q):
    if not q: return 0.0
    units = float(q.get('units', 0))
    nano = float(q.get('nano', 0)) / 1_000_000_000
    return units + nano

def get_operations(account_id, from_date, to_date):
    operations = []
    cursor = ""
    while True:
        payload = {
            "accountId": account_id,
            "from": from_date,
            "to": to_date,
            "limit": 1000
        }
        if cursor:
            payload["cursor"] = cursor
        
        res = tinkoff_request('OperationsService/GetOperationsByCursor', payload)
        items = res.get('items', [])
        operations.extend(items)
        
        if not res.get('hasNext'):
            break
        cursor = res.get('nextCursor')
    return operations

def main():
    # 1. Получаем счета
    accounts = tinkoff_request('UsersService/GetAccounts', {})['accounts']
    print(f"Найдено счетов: {len(accounts)}")

    from_date = "2019-01-01T00:00:00Z"
    to_date = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    
    all_ops = []
    for acc in accounts:
        if acc['status'] != 'ACCOUNT_STATUS_OPEN':
            continue
        print(f"Загружаем операции по счету: {acc['name']}")
        ops = get_operations(acc['id'], from_date, to_date)
        all_ops.extend(ops)
        time.sleep(1)

    print(f"Всего операций получено: {len(all_ops)}")

    # 3. Преобразуем в формат для Apps Script
    import_data = []
    for op in all_ops:
        if op.get('state') != 'OPERATION_STATE_EXECUTED':
            continue
            
        # ✅ Пропускаем заблокированные активы (замороженные)
        if op.get('blocked', False): 
            continue

        op_type = op.get('type', '')
        
        # Маппинг типов операций
        if 'BUY' in op_type: 
            mapped_type = 'buy'
        elif 'SELL' in op_type: 
            mapped_type = 'sell'
        elif 'DIVIDEND' in op_type and 'TAX' not in op_type: 
            mapped_type = 'dividend'
        elif 'COUPON' in op_type: 
            mapped_type = 'coupon'
        elif 'TAX' in op_type: 
            continue # Налоги пропускаем
        else: 
            continue # переводы и прочее

                # Маппинг типа актива
        inst_type = op.get('instrumentType', '')
        op_currency = op.get('payment', {}).get('currency', 'rub')
        ticker = op.get('ticker', '')
        
        if ticker == 'GLDRUB_TOM':
            asset_type = 'gold'
        elif 'money_market' in op.get('classCode', '').lower() or ticker == 'TMON@':
            asset_type = 'money_market'
        elif inst_type == 'bond': 
            if op_currency != 'rub': 
                asset_type = 'currency_bond'
            else: 
                asset_type = 'bond'
        elif inst_type == 'etf': 
            asset_type = 'etf'
        else: 
            asset_type = 'stock'

        payment = parse_quotation(op.get('payment', {}))
        price = parse_quotation(op.get('price', {}))
        commission = parse_quotation(op.get('commission', {}))
        quantity = float(op.get('quantity', 0))
        
        amount = abs(payment) if mapped_type in ['buy', 'sell'] else payment
        date_str = op.get('date', '').split('T')[0]
        
        raw_id = op.get('id', '')
        formatted_id = f"inv_tinkoff_{raw_id}"

        import_data.append({
            "id": formatted_id,
            "date": date_str,
            "type": mapped_type,
            "ticker": op.get('ticker', ''),
            "name": op.get('name', ''),
            "asset_type": asset_type,
            "currency": "RUB",
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "fee": abs(commission),
            "tax": 0,
            "account": "Т-Банк",
            "comment": op.get('description', ''),
            "face_value": 1000
        })

    # 4. Отправляем в Apps Script
    if import_data:
        print(f"Отправка {len(import_data)} операций в Apps Script...")
        resp = requests.post(
            APPS_SCRIPT_URL, 
            json={"action": "importFromTinkoff", "data": import_data},
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
        print("Ответ:", resp.text)
    else:
        print("Нет новых операций для отправки.")

if __name__ == "__main__":
    main()
