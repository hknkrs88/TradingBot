import requests

url = "https://api.coingecko.com/api/v3/coins/markets"

params = {
    "vs_currency": "usd",
    "order": "market_cap_desc",
    "per_page": 250,
    "page": 1,
    "sparkline": "false",
}

response = requests.get(url, params=params, timeout=30)

print("HTTP:", response.status_code)

if response.status_code == 200:
    data = response.json()
    print("CoinGecko coin sayisi:", len(data))

    for coin in data[:10]:
        print(
            coin["market_cap_rank"],
            coin["symbol"].upper(),
            coin["name"]
        )
else:
    print(response.text)
