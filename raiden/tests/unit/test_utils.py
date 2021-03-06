from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
import requests
from eth_keys.exceptions import BadSignature, ValidationError
from eth_utils import decode_hex, to_canonical_address

from raiden.constants import EMPTY_HASH
from raiden.exceptions import InvalidSignature
from raiden.network.utils import get_http_rtt
from raiden.tests.utils.mocks import MockWeb3
from raiden.utils import block_specification_to_number, privatekey_to_publickey, sha3
from raiden.utils.signer import LocalSigner, Signer, recover
from raiden.utils.typing import BlockNumber


def test_privatekey_to_publickey():
    privkey = sha3(b"secret")
    pubkey = (
        "c283b0507c4ec6903a49fac84a5aead951f3c38b2c72b69da8a70a5bac91e9c"
        "705f70c7554b26e82b90d2d1bbbaf711b10c6c8b807077f4070200a8fb4c6b771"
    )

    assert pubkey == privatekey_to_publickey(privkey).hex()


def test_signer_sign():
    privkey = sha3(b"secret")  # 0x38e959391dD8598aE80d5d6D114a7822A09d313A
    message = b"message"
    # generated with Metamask's web3.personal.sign
    signature = decode_hex(
        "0x1eff8317c59ab169037f5063a5129bb1bab0299fef0b5621d866b07be59e2c0a"
        "6a404e88d3360fb58bd13daf577807c2cf9b6b26d80fc929c52e952769a460981c"
    )

    signer: Signer = LocalSigner(privkey)

    assert signer.sign(message) == signature


def test_recover():
    account = to_canonical_address("0x38e959391dD8598aE80d5d6D114a7822A09d313A")
    message = b"message"
    # generated with Metamask's web3.personal.sign
    signature = decode_hex(
        "0x1eff8317c59ab169037f5063a5129bb1bab0299fef0b5621d866b07be59e2c0a"
        "6a404e88d3360fb58bd13daf577807c2cf9b6b26d80fc929c52e952769a460981c"
    )

    assert recover(data=message, signature=signature) == account


@pytest.mark.parametrize(
    ("signature", "nested_exception"),
    [
        pytest.param(b"\x00" * 65, BadSignature, id="BadSignature"),
        pytest.param(b"bla", ValidationError, id="ValidationError"),
    ],
)
def test_recover_exception(signature, nested_exception):
    with pytest.raises(InvalidSignature) as exc_info:
        recover(b"bla", signature)
    assert isinstance(exc_info.value.__context__, nested_exception)


def test_block_speficiation_to_number():
    web3 = MockWeb3(1)
    assert block_specification_to_number("latest", web3) == BlockNumber(42)
    assert block_specification_to_number("pending", web3) == BlockNumber(42)

    with pytest.raises(AssertionError):
        block_specification_to_number("whatever", web3)

    assert block_specification_to_number(EMPTY_HASH, web3) == BlockNumber(42)
    assert block_specification_to_number(BlockNumber(56), web3) == BlockNumber(56)

    with pytest.raises(AssertionError):
        block_specification_to_number([1, 2], web3)


def test_get_http_rtt():
    with patch.object(requests, "request", side_effect=requests.RequestException):
        assert get_http_rtt(url="url", method="get") is None

    seconds = iter([0.2, 0.2, 0.5])

    def request_mock(method, url, **_):
        assert method == "get"
        assert url == "url"
        return Mock(elapsed=timedelta(seconds=next(seconds)))

    with patch.object(requests, "request", side_effect=request_mock):
        assert get_http_rtt(url="url", method="get") == 0.3
