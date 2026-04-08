import httpx
import sys

URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
USER = "migbravoduk@gmail.com"
PASS = "sYNCMASTER450BMIGUEL"

def test_api(series_id, first=None, last=None):
    params = {
        "user": USER,
        "pass": PASS,
        "function": "GetSeries",
        "timeseries": series_id,
    }
    if first: params["firstdate"] = first
    if last: params["lastdate"] = last
        
    print(f"Testing {series_id} with {first} to {last} ...")
    try:
        r = httpx.get(URL, params=params, timeout=30)
        data = r.json()
        print("Codigo:", data.get('Codigo'))
        print("Descripcion:", data.get('Descripcion'))
        obs = data.get('Series', {}).get('Obs', [])
        if obs is None: obs = []
        if isinstance(obs, dict): obs = [obs]
        print("Records length:", len(obs))
    except Exception as e:
        print(f"FAILED: {e}")
    print("--------------------------------------------------")

if __name__ == "__main__":
    sid = "F073.IPC.IND.N.DIC.Z.Z.2023100"
    test_api(sid, "2021-04-07", "2026-04-07")
    test_api(sid, "2023-12-01", "2026-04-07")
    test_api(sid) # no dates
    
    # Let's test a monthly series that has older history
    sid2 = "F072.TAS.IND.TEC.M.MES.D" # TPM
    test_api(sid2, "2021-04-07", "2026-04-07")
    test_api(sid2)
    test_api(sid2, "2020-01-01", "2024-01-01")
    
    sid3 = "F073.IPC.VAR.N.DIC.Z.Z.M"
    test_api(sid3, "2021-04-07", "2026-04-07")
    test_api(sid3, "2023-12-01", "2026-04-07")
