import json
import httpx

URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
USER = "migbravoduk@gmail.com"
PASS = "8H_nF6DMBaTDtr7"

def search_and_filter(frequency, search_terms):
    params = {
        "user": USER,
        "pass": PASS,
        "function": "SearchSeries",
        "frequency": frequency
    }
    r = httpx.get(URL, params=params, timeout=30)
    text = r.content.decode("latin-1")
    data = json.loads(text)
    
    si = data.get('SeriesInfos')
    if not si:
        print(f"No series found for frequency: {frequency}")
        return
        
    print(f"\n=== SEARCH RESULTS FOR {frequency} (filtering by: {search_terms}) ===")
    count = 0
    for item in si:
        title = item.get('spanishTitle', '').lower()
        key = item.get('seriesId')
        # Check if any search term is in the title
        if any(term.lower() in title for term in search_terms):
            count += 1
            if count <= 50:
                print(f"  Key: {key} | Name: {item.get('spanishTitle')}")
            elif count == 51:
                print("  ... [Truncated to 50 results] ...")
    print(f"Found total of {count} matching series.")

if __name__ == "__main__":
    search_and_filter("MONTHLY", ["imacec"])
