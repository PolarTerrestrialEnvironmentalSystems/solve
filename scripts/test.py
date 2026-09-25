import http.client
import json
import os

conn = http.client.HTTPSConnection("google.serper.dev")
payload = json.dumps({
  "q": "Arendsee"
})
headers = {
  'X-API-KEY': os.environ["SERPER_KEY"],
  'Content-Type': 'application/json'
}
conn.request("POST", "/search", payload, headers)
res = conn.getresponse()
data = res.read()
print(data.decode("utf-8"))