import getpass
import json
import os
from pyhiveapi import Hive, SMS_REQUIRED
import requests
from requests.compat import urljoin
import sys
import time

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

# === Authenticate to Hive beekeeper API and print authorization token ===
query_interval = 0 # We get a "403 - Forbidden" response if we query the API too frequently

# --- Authenticate to Hive beekeeper API --- 
print(" - Authenticating to Hive beekeeper API (waiting {:d} seconds before making request)".format(query_interval))
time.sleep(query_interval)
session = Hive(username=hive_username, password=hive_password)
login = session.login()
if login.get("ChallengeName") == SMS_REQUIRED:
    code = input("Enter 2FA code: ")
    session.sms2FA(code, login)

session.startSession()
print(session.tokens.tokenData["token"])
