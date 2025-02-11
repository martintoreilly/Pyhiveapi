"""Hive Session Module."""
import asyncio
import copy
import json
import operator
import os
import time
import traceback
from datetime import datetime, timedelta

from aiohttp.web import HTTPException
from apyhiveapi import API, Auth

from .device_attributes import HiveAttributes
from .helper.const import ACTIONS, DEVICES, HIVE_TYPES, PRODUCTS
from .helper.hive_exceptions import (
    HiveApiError,
    HiveReauthRequired,
    HiveUnknownConfiguration,
)
from .helper.hive_helper import HiveHelper
from .helper.logger import Logger
from .helper.map import Map


class HiveSession:
    """Hive Session Code.

    Raises:
        HiveUnknownConfiguration: Unknown configuration.
        HTTPException: HTTP error has occurred.
        HiveApiError: Hive has retuend an error code.
        HiveReauthRequired: Tokens have expired and reauthentiction is required.

    Returns:
        object: Session object.
    """

    sessionType = "Session"

    def __init__(
        self, username: str = None, password: str = None, websession: object = None
    ):
        """Initialise the base variable values.

        Args:
            username (str, optional): Hive username. Defaults to None.
            password (str, optional): Hive Password. Defaults to None.
            websession (object, optional): Websession for api calls. Defaults to None.
        """
        self.auth = None
        self.api = API(hiveSession=self, websession=websession)
        if None not in (username, password):
            self.auth = Auth(username=username, password=password)

        self.helper = HiveHelper(self)
        self.attr = HiveAttributes(self)
        self.log = Logger(self)
        self.updateLock = asyncio.Lock()
        self.tokens = Map(
            {
                "tokenData": {},
                "tokenCreated": datetime.now() - timedelta(seconds=4000),
                "tokenExpiry": timedelta(seconds=3600),
            }
        )
        self.config = Map(
            {
                "alarm": False,
                "battery": [],
                "errorList": {},
                "file": False,
                "homeID": None,
                "lastUpdated": datetime.now(),
                "mode": [],
                "scanInterval": timedelta(seconds=120),
                "sensors": False,
                "userID": None,
                "username": username,
            }
        )
        self.data = Map(
            {
                "products": {},
                "devices": {},
                "actions": {},
                "user": {},
                "minMax": {},
                "alarm": {},
            }
        )
        self.devices = {}
        self.deviceList = {}

    def openFile(self, file: str):
        """Open a file.

        Args:
            file (str): File location

        Returns:
            dict: Data from the chosen file.
        """
        path = os.path.dirname(os.path.realpath(__file__)) + "/data/" + file
        path = path.replace("/pyhiveapi/", "/apyhiveapi/")
        with open(path) as j:
            data = json.loads(j.read())

        return data

    def addList(self, type: str, data: dict, **kwargs: dict):
        """Add entity to the list.

        Args:
            type (str): Type of entity
            data (dict): Information to create entity.

        Returns:
            dict: Entity.
        """
        add = False if kwargs.get("custom") and not self.config.sensors else True
        device = self.helper.getDeviceData(data)
        device_name = (
            device["state"]["name"]
            if device["state"]["name"] != "Receiver"
            else "Heating"
        )
        formatted_data = {}

        if add:
            try:
                formatted_data = {
                    "hiveID": data.get("id", ""),
                    "hiveName": device_name,
                    "hiveType": data.get("type", ""),
                    "haType": type,
                    "deviceData": device.get("props", data.get("props", {})),
                    "parentDevice": data.get("parent", None),
                    "isGroup": data.get("isGroup", False),
                    "device_id": device["id"],
                    "device_name": device_name,
                }

                if kwargs.get("haName", "FALSE")[0] == " ":
                    kwargs["haName"] = device_name + kwargs["haName"]
                else:
                    formatted_data["haName"] = device_name
                formatted_data.update(kwargs)
            except KeyError as e:
                self.logger.error(e)

            self.deviceList[type].append(formatted_data)
        return add

    async def updateInterval(self, new_interval: timedelta):
        """Update the scan interval.

        Args:
            new_interval (int): New interval for polling.
        """
        if type(new_interval) == int:
            new_interval = timedelta(seconds=new_interval)

        interval = new_interval
        if interval < timedelta(seconds=15):
            interval = timedelta(seconds=15)
        self.config.scanInterval = interval

    async def useFile(self, username: str = None):
        """Update to check if file is being used.

        Args:
            username (str, optional): Looks for use@file.com. Defaults to None.
        """
        using_file = True if username == "use@file.com" else False
        if using_file:
            self.config.file = True

    async def updateTokens(self, tokens: dict):
        """Update session tokens.

        Args:
            tokens (dict): Tokens from API response.

        Returns:
            dict: Parsed dictionary of tokens
        """
        data = {}
        if "AuthenticationResult" in tokens:
            data = tokens.get("AuthenticationResult")
            self.tokens.tokenData.update({"token": data["IdToken"]})
            if "RefreshToken" in data:
                self.tokens.tokenData.update({"refreshToken": data["RefreshToken"]})
            self.tokens.tokenData.update({"accessToken": data["AccessToken"]})
        elif "token" in tokens:
            data = tokens
            self.tokens.tokenData.update({"token": data["token"]})
            self.tokens.tokenData.update({"refreshToken": data["refreshToken"]})
            self.tokens.tokenData.update({"accessToken": data["accessToken"]})

        if "ExpiresIn" in data:
            self.tokens.tokenExpiry = timedelta(seconds=data["ExpiresIn"])

        return self.tokens

    async def login(self):
        """Login to hive account.

        Raises:
            HiveUnknownConfiguration: Login information is unknown.

        Returns:
            dict: result of the login request.
        """
        if not self.auth:
            raise HiveUnknownConfiguration

        result = self.auth.login()
        await self.updateTokens(result)
        return result

    async def sms2FA(self, code: str, session: dict):
        """Complete 2FA auth.

        Args:
            code (str): 2FA code to complete login.
            session (dict): The session data from login.

        Returns:
            dict: result of the login request.
        """
        result = self.auth.sms_2fa(code, session)
        await self.updateTokens(result)
        return result

    async def hiveRefreshTokens(self):
        """Refresh Hive tokens.

        Returns:
            boolean: True/False if update was successful
        """
        result = None

        if self.config.file:
            return None
        else:
            expiry_time = self.tokens.tokenCreated + self.tokens.tokenExpiry
            if datetime.now() >= expiry_time:
                result = await self.auth.refreshToken(
                    self.tokens.tokenData["refreshToken"]
                )
                self.updateTokens(result[0])
                self.tokens.tokenCreated = datetime.now()

        return result

    async def updateData(self, device: dict):
        """Get latest data for Hive nodes - rate limiting.

        Args:
            device (dict): Device requesting the update.

        Returns:
            boolean: True/False if update was successful
        """
        await self.updateLock.acquire()
        updated = False
        try:
            ep = self.config.lastUpdate + self.config.scanInterval
            if datetime.now() >= ep:
                await self.getDevices(device["hiveID"])
                updated = True
        finally:
            self.updateLock.release()

        return updated

    async def getAlarm(self):
        """Get alarm data.

        Raises:
            HTTPException: HTTP error has occurred updating the devices.
            HiveApiError: An API error code has been returned.
        """
        if self.config.file:
            api_resp_d = self.openFile("alarm.json")
        elif self.tokens is not None:
            api_resp_d = await self.api.getAlarm()
            if operator.contains(str(api_resp_d["original"]), "20") is False:
                raise HTTPException
            elif api_resp_d["parsed"] is None:
                raise HiveApiError

        self.data.alarm = api_resp_d["parsed"]

    async def getDevices(self, n_id: str):
        """Get latest data for Hive nodes.

        Args:
            n_id (str): ID of the device requesting data.

        Raises:
            HTTPException: HTTP error has occurred updating the devices.
            HiveApiError: An API error code has been returned.

        Returns:
            boolean: True/False if update was successful.
        """
        get_nodes_successful = False
        api_resp_d = None

        try:
            if self.config.file:
                api_resp_d = self.openFile("data.json")
            elif self.tokens is not None:
                await self.hiveRefreshTokens()
                api_resp_d = await self.api.getAll()
                if operator.contains(str(api_resp_d["original"]), "20") is False:
                    raise HTTPException
                elif api_resp_d["parsed"] is None:
                    raise HiveApiError

            api_resp_p = api_resp_d["parsed"]
            tmpProducts = {}
            tmpDevices = {}
            tmpActions = {}

            for hiveType in api_resp_p:
                if hiveType == "user":
                    self.data.user = api_resp_p[hiveType]
                    self.config.userID = api_resp_p[hiveType]["id"]
                if hiveType == "products":
                    for aProduct in api_resp_p[hiveType]:
                        tmpProducts.update({aProduct["id"]: aProduct})
                if hiveType == "devices":
                    for aDevice in api_resp_p[hiveType]:
                        tmpDevices.update({aDevice["id"]: aDevice})
                        if aDevice["type"] == "siren":
                            self.config.alarm = True
                if hiveType == "actions":
                    for aAction in api_resp_p[hiveType]:
                        tmpActions.update({aAction["id"]: aAction})
                if hiveType == "homes":
                    self.config.homeID = api_resp_p[hiveType]["homes"][0]["id"]

            if len(tmpProducts) > 0:
                self.data.products = copy.deepcopy(tmpProducts)
            if len(tmpDevices) > 0:
                self.data.devices = copy.deepcopy(tmpDevices)
            self.data.actions = copy.deepcopy(tmpActions)
            if self.config.alarm:
                await self.getAlarm()
            self.config.lastUpdate = datetime.now()
            get_nodes_successful = True
        except (OSError, RuntimeError, HiveApiError, ConnectionError, HTTPException):
            get_nodes_successful = False

        return get_nodes_successful

    async def startSession(self, config: dict = {}):
        """Setup the Hive platform.

        Args:
            config (dict, optional): Configuration for Home Assistant to use. Defaults to {}.

        Raises:
            HiveUnknownConfiguration: Unknown configuration identifed.
            HiveReauthRequired: Tokens have expired and reauthentication is required.

        Returns:
            list: List of devices
        """
        custom_component = False
        for file, line, w1, w2 in traceback.extract_stack():
            if "/custom_components/" in file:
                custom_component = True

        self.config.sensors = custom_component
        await self.useFile(config.get("username", self.config.username))
        await self.updateInterval(
            config.get("options", {}).get("scan_interval", self.config.scanInterval)
        )

        if config != {}:
            if config["tokens"] is not None and not self.config.file:
                await self.updateTokens(config["tokens"])
            elif not self.config.file:
                raise HiveUnknownConfiguration

        try:
            await self.getDevices("No_ID")
        except HTTPException:
            return HTTPException

        if self.data.devices == {} or self.data.products == {}:
            raise HiveReauthRequired

        return await self.createDevices()

    async def createDevices(self):
        """Create list of devices.

        Returns:
            list: List of devices
        """
        self.deviceList["alarm_control_panel"] = []
        self.deviceList["binary_sensor"] = []
        self.deviceList["climate"] = []
        self.deviceList["light"] = []
        self.deviceList["sensor"] = []
        self.deviceList["switch"] = []
        self.deviceList["water_heater"] = []

        hive_type = HIVE_TYPES["Heating"] + HIVE_TYPES["Switch"] + HIVE_TYPES["Light"]
        for aProduct in self.data.products:
            p = self.data.products[aProduct]
            if p.get("isGroup", False):
                continue
            product_list = PRODUCTS.get(self.data.products[aProduct]["type"], [])
            for code in product_list:
                eval("self." + code)

            if self.data.products[aProduct]["type"] in hive_type:
                self.config.mode.append(p["id"])

        hive_type = HIVE_TYPES["Thermo"] + HIVE_TYPES["Sensor"]
        for aDevice in self.data["devices"]:
            d = self.data.devices[aDevice]
            device_list = DEVICES.get(self.data.devices[aDevice]["type"], [])
            for code in device_list:
                eval("self." + code)

            if self.data["devices"][aDevice]["type"] in hive_type:
                self.config.battery.append(d["id"])

        if "action" in HIVE_TYPES["Switch"]:
            for action in self.data["actions"]:
                a = self.data["actions"][action]  # noqa: F841
                eval("self." + ACTIONS)

        return self.deviceList

    @staticmethod
    def epochTime(date_time: any, pattern: str, action: str):
        """date/time conversion to epoch.

        Args:
            date_time (any): epoch time or date and time to use.
            pattern (str): Pattern for converting to epoch.
            action (str): Convert from/to.

        Returns:
            any: Converted time.
        """
        if action == "to_epoch":
            pattern = "%d.%m.%Y %H:%M:%S"
            epochtime = int(time.mktime(time.strptime(str(date_time), pattern)))
            return epochtime
        elif action == "from_epoch":
            date = datetime.fromtimestamp(int(date_time)).strftime(pattern)
            return date
