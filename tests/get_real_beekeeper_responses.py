import json
import os
from pyhiveapi import Hive, SMS_REQUIRED
import requests
from requests.compat import urljoin
import sys
import time

script_dir = os.path.dirname(__file__)
module_root = os.path.join(script_dir, '..', 'secrets')
sys.path.append(module_root)
from hive_credentials import hive_credentials

# === Specify API base URL and endpoints ===
# Note: For joining base URL and endpoint paths to work correctly
# the base URL *must* have a trailing '/' and the endpoint paths
# *must not* have a leading '/'
base_url = "https://beekeeper.hivehome.com/1.0/" 
endpoints = [
    {
        "name": "nodes",
        "path": "nodes/all/"
    },
    {
        "name": "devices",
        "path": "devices/"
    },
    {
        "name": "products",
        "path": "products/"
    },
    {
        "name": "actions",
        "path": "actions/"
    }
]

# === Get user number for anonymising and storing test data ===
user_number_str = input("Enter user number: ")
# Check user number is valid (in range 1-999)
try:
    user_number = int(user_number_str)
except:
    print("User number {:s} is invalid. User number must be an integer in range 1-999. Quitting.".format(user_number_str))
    exit(-1)
if not(1 <= user_number <=999):
    print("User number {:s} is invalid. User number must be an integer in range 1-999. Quitting.".format(user_number_str))
    exit(-1)

response_data_filename = "user-{:03d}-response-data.json".format(user_number)
response_data_filepath = os.path.join(script_dir, "../secrets/", response_data_filename)
# If file already exists for user, ask for confirmation before overwriting
# and exit if overwrite not confirmed
if os.path.exists(os.path.join("data", response_data_filepath)):
    confirmed = input("Data for user {:d} already exists. Are you sure you want to overwrite it? (y/n): ".format(user_number))
    if not (confirmed in ["y", "Y"]):
        print("Overwrite for user {:d} not confirmed. Aborting without generating new data.".format(user_number))
        exit(-1)


# === Fetch real responses from Hive beekeeper API ===
query_interval = 10 # We get a "403 - Forbidden" response if we query the API too frequently

# --- Authenticate to Hive beekeeper API --- 
try:
    script_dir = os.path.dirname(__file__)
    module_root = os.path.join(script_dir, '..', 'secrets')
    sys.path.append(module_root)
    from hive_credentials import hive_credentials
    hive_username = hive_credentials["username"]
    hive_password = hive_credentials["password"]
except:
    hive_username = input("Enter your Hive username: ")
    hive_password = getpass.getpass("Enter your Hive password: ")
print(" - Authenticating to Hive beekeeper API (waiting {:d} seconds before making request)".format(query_interval))
time.sleep(query_interval)
session = Hive(username=hive_username, password=hive_password)
login = session.login()
if login.get("ChallengeName") == SMS_REQUIRED:
    code = input("Enter 2FA code: ")
    session.sms2FA(code, login)

# --- Fetch real responses from all endpoints used by the library --
headers = {"Content-Type": "application/json", "Authorization": session.tokens.tokenData["token"]}
for endpoint in endpoints:
    print(" - Getting data from '{:s}' endpoint (waiting {:d} seconds before making request)".format(endpoint["name"], query_interval))
    time.sleep(query_interval)
    url = urljoin(base_url, endpoint["path"])
    resp = requests.get(url=url, headers=headers)
    if(resp.status_code != 200):
        print("Received unexpected status code {:d} with '{:s}' message in response body when querying {:s}. Quitting.".format(resp.status_code, resp.text))
        exit(-1)
    endpoint["response"] = {
        "status_code": resp.status_code,
        "json": resp.json()
    }

print("Writing response data for 'User {:d}' to file '{:s}'".format(user_number, response_data_filepath))
with open(response_data_filepath, 'w') as f:
    json.dump(endpoints, f, indent=2)
print("Done.")