import urllib.request
import urllib.parse
import urllib.error
import json

def soda(fid, p):
    u = f'https://data.cityofnewyork.us/resource/{fid}.json?' + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(u, timeout=90) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {'ERROR': e.read().decode()[:300]}

print(soda('5zyy-y8am', {'$select': "substr(nyc_borough_block_and_lot,1,1) as boro, count(*) as n", '$group': 'boro', '$where': "report_year='2024'"}))
print('mh children (parented) 2024:', soda('5zyy-y8am', {'$select': 'count(distinct property_id)', '$where': "report_year='2024' and starts_with(nyc_borough_block_and_lot,'1') and starts_with(parent_property_id,'Not')=false"}))
