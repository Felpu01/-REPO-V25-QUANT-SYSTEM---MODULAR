import random

def get_market_data():

    data = []
    price = 10000

    for _ in range(300):
        price += random.uniform(-20, 20)

        data.append({
            "price": price
        })

    return data
