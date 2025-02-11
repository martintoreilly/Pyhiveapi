"""Sync version of HiveAuth."""

import base64
import binascii
import datetime
import hashlib
import hmac
import os
import re

import boto3
import botocore

from ..helper.hive_exceptions import (
    HiveApiError,
    HiveInvalid2FACode,
    HiveInvalidPassword,
    HiveInvalidUsername,
)
from .hive_api import HiveApi

# https://github.com/aws/amazon-cognito-identity-js/blob/master/src/AuthenticationHelper.js#L22
n_hex = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    + "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    + "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    + "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    + "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    + "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    + "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    + "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    + "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    + "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    + "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64"
    + "ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    + "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6B"
    + "F12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    + "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB31"
    + "43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
# https://github.com/aws/amazon-cognito-identity-js/blob/master/src/AuthenticationHelper.js#L49
g_hex = "2"
info_bits = bytearray("Caldera Derived Key", "utf-8")


class HiveAuth:
    """Sync Hive Auth.

    Raises:
        ValueError: [description]
        ValueError: [description]
        ValueError: [description]
        HiveInvalidUsername: [description]
        HiveApiError: [description]
        HiveInvalidPassword: [description]
        HiveApiError: [description]
        NotImplementedError: [description]
        HiveInvalid2FACode: [description]
        HiveApiError: [description]
        HiveApiError: [description]

    Returns:
        [type]: [description]
    """

    NEW_PASSWORD_REQUIRED_CHALLENGE = "NEW_PASSWORD_REQUIRED"
    PASSWORD_VERIFIER_CHALLENGE = "PASSWORD_VERIFIER"
    SMS_MFA_CHALLENGE = "SMS_MFA"

    def __init__(
        self,
        username: str,
        password: str,
        pool_region: str = None,
        client_secret: str = None,
    ):
        """Intilaise Sync Hive Auth.

        Args:
            username (str): [description]
            password (str): [description]
            pool_region (str, optional): [description]. Defaults to None.
            client_secret (str, optional): [description]. Defaults to None.

        Raises:
            ValueError: pool_region and client should not both be specified.
        """
        if pool_region is not None:
            raise ValueError(
                "pool_region and client should not both be specified "
                "(region should be passed to the boto3 client instead)"
            )

        self.username = username
        self.password = password
        self.user_id = "user_id"
        self.client_secret = client_secret
        self.big_n = hex_to_long(n_hex)
        self.g = hex_to_long(g_hex)
        self.k = hex_to_long(hex_hash("00" + n_hex + "0" + g_hex))
        self.small_a_value = self.generate_random_small_a()
        self.large_a_value = self.calculate_a()
        self.useFile = True if self.username == "use@file.com" else False
        self.file_response = {"AuthenticationResult": {"AccessToken": "file"}}
        self.api = HiveApi()
        self.data = self.api.getLoginInfo()
        self.__pool_id = self.data.get("UPID")
        self.__client_id = self.data.get("CLIID")
        self.__region = self.data.get("REGION").split("_")[0]
        self.client = boto3.client("cognito-idp", self.__region)

    def generate_random_small_a(self):
        """Helper function to generate a random big integer.

        Returns:
            int: {Long integer} a random value.
        """
        random_long_int = get_random(128)
        return random_long_int % self.big_n

    def calculate_a(self):
        """Calculate the client's public value A = g^a%N with the generated random number.

        Raises:
            ValueError: Safety check for 0 value.

        Returns:
            int: {Long integer} Computed large A.
        """
        big_a = pow(self.g, self.small_a_value, self.big_n)
        # safety check
        if (big_a % self.big_n) == 0:
            raise ValueError("Safety check for A failed")
        return big_a

    def get_password_authentication_key(
        self, username: str, password: str, server_b_value: int, salt: int
    ):
        """
        Calculates the final hkdf based on computed S value, and computed U value and the key.

        :param {String} username Username.
        :param {String} password Password.
        :param {Long integer} server_b_value Server B value.
        :param {Long integer} salt Generated salt.
        :return {Buffer} Computed HKDF value.
        """
        server_b_value = hex_to_long(server_b_value)
        u_value = calculate_u(self.large_a_value, server_b_value)
        if u_value == 0:
            raise ValueError("U cannot be zero.")
        username_password = "{}{}:{}".format(
            self.__pool_id.split("_")[1],
            username,
            password,
        )
        username_password_hash = hash_sha256(username_password.encode("utf-8"))

        x_value = hex_to_long(hex_hash(pad_hex(salt) + username_password_hash))
        g_mod_pow_xn = pow(self.g, x_value, self.big_n)
        int_value2 = server_b_value - self.k * g_mod_pow_xn
        s_value = pow(int_value2, self.small_a_value + u_value * x_value, self.big_n)
        hkdf = compute_hkdf(
            bytearray.fromhex(pad_hex(s_value)),
            bytearray.fromhex(pad_hex(long_to_hex(u_value))),
        )
        return hkdf

    def get_auth_params(self):
        """Get params."""
        auth_params = {
            "USERNAME": self.username,
            "SRP_A": long_to_hex(self.large_a_value),
        }
        if self.client_secret is not None:
            auth_params.update(
                {
                    "SECRET_HASH": self.get_secret_hash(
                        self.username, self.__client_id, self.client_secret
                    )
                }
            )
        return auth_params

    @staticmethod
    def get_secret_hash(username, client_id: str, client_secret: str):
        """Get secret hash."""
        message = bytearray(username + client_id, "utf-8")
        hmac_obj = hmac.new(bytearray(client_secret, "utf-8"), message, hashlib.sha256)
        return base64.standard_b64encode(hmac_obj.digest()).decode("utf-8")

    def process_challenge(self, challenge_parameters: dict):
        """Process 2FA challenge."""
        self.user_id = challenge_parameters["USER_ID_FOR_SRP"]
        salt_hex = challenge_parameters["SALT"]
        srp_b_hex = challenge_parameters["SRP_B"]
        secret_block_b64 = challenge_parameters["SECRET_BLOCK"]
        # re strips leading zero from a day number (required by AWS Cognito)
        timestamp = re.sub(
            r" 0(\d) ",
            r" \1 ",
            datetime.datetime.utcnow().strftime("%a %b %d %H:%M:%S UTC %Y"),
        )
        hkdf = self.get_password_authentication_key(
            self.user_id, self.password, srp_b_hex, salt_hex
        )
        secret_block_bytes = base64.standard_b64decode(secret_block_b64)
        msg = (
            bytearray(self.__pool_id.split("_")[1], "utf-8")
            + bytearray(self.user_id, "utf-8")
            + bytearray(secret_block_bytes)
            + bytearray(timestamp, "utf-8")
        )
        hmac_obj = hmac.new(hkdf, msg, digestmod=hashlib.sha256)
        signature_string = base64.standard_b64encode(hmac_obj.digest())
        response = {
            "TIMESTAMP": timestamp,
            "USERNAME": self.user_id,
            "PASSWORD_CLAIM_SECRET_BLOCK": secret_block_b64,
            "PASSWORD_CLAIM_SIGNATURE": signature_string.decode("utf-8"),
        }
        if self.client_secret is not None:
            response.update(
                {
                    "SECRET_HASH": self.get_secret_hash(
                        self.username, self.__client_id, self.client_secret
                    )
                }
            )

        return response

    def login(self):
        """Login into a Hive account."""
        if self.useFile:
            return self.file_response

        auth_params = self.get_auth_params()
        response = None
        result = None
        try:
            response = self.client.initiate_auth(
                AuthFlow="USER_SRP_AUTH",
                AuthParameters=auth_params,
                ClientId=self.__client_id,
            )
        except botocore.exceptions.ClientError as err:
            if err.__class__.__name__ == "UserNotFoundException":
                raise HiveInvalidUsername
        except botocore.exceptions.EndpointConnectionError as err:
            if err.__class__.__name__ == "EndpointConnectionError":
                raise HiveApiError

        if response["ChallengeName"] == self.PASSWORD_VERIFIER_CHALLENGE:
            challenge_response = self.process_challenge(response["ChallengeParameters"])
            try:
                result = self.client.respond_to_auth_challenge(
                    ClientId=self.__client_id,
                    ChallengeName=self.PASSWORD_VERIFIER_CHALLENGE,
                    ChallengeResponses=challenge_response,
                )
            except botocore.exceptions.ClientError as err:
                if err.__class__.__name__ == "NotAuthorizedException":
                    raise HiveInvalidPassword
            except botocore.exceptions.EndpointConnectionError as err:
                if err.__class__.__name__ == "EndpointConnectionError":
                    raise HiveApiError

            return result
        else:
            raise NotImplementedError(
                "The %s challenge is not supported" % response["ChallengeName"]
            )

    def sms_2fa(self, entered_code: str, challenge_parameters: dict):
        """Process 2FA sms verification."""
        session = challenge_parameters.get("Session")
        code = str(entered_code)
        result = None
        try:
            result = self.client.respond_to_auth_challenge(
                ClientId=self.__client_id,
                ChallengeName=self.SMS_MFA_CHALLENGE,
                Session=session,
                ChallengeResponses={
                    "SMS_MFA_CODE": code,
                    "USERNAME": self.user_id,
                },
            )
        except botocore.exceptions.ClientError as err:
            if (
                err.__class__.__name__ == "NotAuthorizedException"
                or err.__class__.__name__ == "CodeMismatchException"
            ):
                raise HiveInvalid2FACode
        except botocore.exceptions.EndpointConnectionError as err:
            if err.__class__.__name__ == "EndpointConnectionError":
                raise HiveApiError

        return result

    def refreshToken(
        self,
        refresh_token: str,
    ):
        """Process 2FA sms verification."""
        result = None
        try:
            result = (
                self.client.initiate_auth(
                    ClientId=self.__client_id,
                    AuthFlow="REFRESH_TOKEN_AUTH",
                    AuthParameters={"REFRESH_TOKEN": refresh_token},
                ),
            )
        except botocore.exceptions.ClientError as err:
            if err.__class__.__name__ == "NotAuthorizedException":
                raise HiveInvalid2FACode
        except botocore.exceptions.EndpointConnectionError as err:
            if err.__class__.__name__ == "EndpointConnectionError":
                raise HiveApiError

        return result


def hex_to_long(hex_string: str):
    """Convert hex to long."""
    return int(hex_string, 16)


def get_random(nbytes):
    """Get random bytes."""
    random_hex = binascii.hexlify(os.urandom(nbytes))
    return hex_to_long(random_hex)


def hash_sha256(buf):
    """Authentication Helper hash."""
    a = hashlib.sha256(buf).hexdigest()
    return (64 - len(a)) * "0" + a


def hex_hash(hex_string):
    """Convert hex to hash."""
    return hash_sha256(bytearray.fromhex(hex_string))


def calculate_u(big_a, big_b):
    """
    Calculate the client's value U which is the hash of A and B.

    :param {Long integer} big_a Large A value.
    :param {Long integer} big_b Server B value.
    :return {Long integer} Computed U value.
    """
    u_hex_hash = hex_hash(pad_hex(big_a) + pad_hex(big_b))
    return hex_to_long(u_hex_hash)


def long_to_hex(long_num):
    """Convert long number to hex."""
    return "%x" % long_num


def pad_hex(long_int):
    """
    Converts a Long integer (or hex string) to hex format padded with zeroes for hashing.

    :param {Long integer|String} long_int Number or string to pad.
    :return {String} Padded hex string.
    """
    if not isinstance(long_int, str):
        hash_str = long_to_hex(long_int)
    else:
        hash_str = long_int
    if len(hash_str) % 2 == 1:
        hash_str = "0%s" % hash_str
    elif hash_str[0] in "89ABCDEFabcdef":
        hash_str = "00%s" % hash_str
    return hash_str


def compute_hkdf(ikm, salt):
    """
    Standard hkdf algorithm.

    :param {Buffer} ikm Input key material.
    :param {Buffer} salt Salt value.
    :return {Buffer} Strong key material.
    @private
    """
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    info_bits_update = info_bits + bytearray(chr(1), "utf-8")
    hmac_hash = hmac.new(prk, info_bits_update, hashlib.sha256).digest()
    return hmac_hash[:16]
