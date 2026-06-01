import httpx
import sys

URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
USER = "migbravoduk@gmail.com"
PASS = "8H_nF6DMBaTDtr7"


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
        import json
        r = httpx.get(URL, params=params, timeout=30)
        # BDE API returns Latin-1/ISO-8859-1. Decode manually to avoid UTF-8 errors in httpx
        text = r.content.decode("latin-1")
        data = json.loads(text)
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
    print("=== TESTING PIB CANDIDATES ===")
    test_api("F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T")   # Known working
    test_api("F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T.P") # % var

    print("\n=== TESTING IPC CANDIDATES ===")
    test_api("F073.IPC.IND.N.DIC.Z.Z.2023100") # base 2023
    test_api("F073.IPC.IND.N.DIC.Z.Z.2018100") # base 2018
    test_api("F073.IPC.VAR.N.DIC.Z.Z.M")       # monthly var
    test_api("F073.IPC.VAR.N.DIC.Z.Z.A")       # annual var

    print("\n=== TESTING TPM CANDIDATES ===")
    test_api("F072.TAS.IND.TEC.M.MES.D")       # TPM in catalog
    test_api("F022.TPM.TIN.D001.NO.Z.D")       # TPM google search candidate

    print("\n=== TESTING EXCHANGE RATE CANDIDATES ===")
    test_api("F016.DEM.DEM_1.USD.DIA.Z.Z")     # CLP/USD daily
    test_api("F016.DEM.DEM_1.USD.MES.Z.Z")     # CLP/USD monthly

    print("\n=== TESTING COBRE CANDIDATES ===")
    test_api("F051.PRE.PRE07.PRE07.USD.MES")   # Cobre monthly
