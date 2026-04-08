import httpx

URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
USER = "migbravoduk@gmail.com"
PASS = "sYNCMASTER450BMIGUEL"

def search_term(frequency):
    params = {
        "user": USER,
        "pass": PASS,
        "function": "SearchSeries",
        "frequencycode": frequency
    }
    r = httpx.get(URL, params=params, timeout=30)
    data = r.json()
    print("Raw Data type:", type(data))
    if isinstance(data, dict):
        print("Raw Data Keys:", data.keys())
        si = data.get('SeriesInfos')
        print("SeriesInfos type:", type(si))
    else:
        print("Data is not a dict:", str(data)[:200])

if __name__ == "__main__":
    search_term("DAILY")
    search_term("MONTHLY")
