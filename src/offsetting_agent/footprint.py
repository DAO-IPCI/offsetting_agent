import rospy
import sqlite3
import urllib.request
import json
from web3 import Web3, HTTPProvider
from ethereum_common.eth_keyfile_helper import KeyfileHelper
from ethereum_common.srv import Accounts, BlockNumber
from offsetting_agent.contracts import VCU

VCU_PRICE = 7.5     # $7.5 for 1 VCU token

WEB3_HTTP_PROVIDER = rospy.get_param('/liability/listener/web3_http_provider')

KEYFILE = rospy.get_param('/liability/infochan/eth/signer/keyfile')
KEYFILE_PASSWORD_FILE = rospy.get_param('/liability/infochan/eth/signer/keyfile_password_file')

def find_country_in_db(country: str) -> float:
    conn = sqlite3.connect(rospy.get_param("/trader/path_to_db"))
    c = conn.cursor()
    c.execute("SELECT coefficient FROM factors_by_countries WHERE country=?", (country,))
    coef = c.fetchone()[0]
    rospy.loginfo("Coefficient for {} is {}".format(country, coef))
    return float(coef)

def get_emission_factor(geo: str) -> float:
    # geo -> country
    # get coefficient from db for the country
    # return
    # country = coordinates_to_country(geo)
    country = geo
    emission_factor = find_country_in_db(country)   # g CO2 / W*h
    # one VCU == 1 tCO2; 1 W*h => 0.430 gCO2
    return emission_factor

# return kg
def calc_footprint(consumption: float, emission_factor: float) -> float:
    return consumption * emission_factor

def get_vcu_price() -> float:
    # Temporary let's assume it's always $7.5
    # TODO grab the price from coincap.io for example
    return VCU_PRICE

def sign_and_send(fn, account="", web3=""):
    nonce = web3.eth.getTransactionCount(account.address)

    fn_tx = fn.buildTransaction({
        'from': account.address,
        'gasPrice': web3.eth.gasPrice,
        # 'gasPrice': web3.toWei('0', 'gwei'), # Sidechain only
        'nonce': nonce
    })
    signed_tx = web3.eth.account.signTransaction(fn_tx, account.privateKey)
    tx = web3.eth.sendRawTransaction(signed_tx.rawTransaction)
    return tx

def offset_footprint(power_kwh: float, geo: str):
    """Offset carbon footprint for given amount of energy consumption in region
    """
    rospy.loginfo('going to offset {} from {}'.format(power_kwh, geo))

    emission_factor = get_emission_factor(geo)
    footprint_g_co2 = calc_footprint(float(power_kwh), emission_factor)

    rospy.loginfo("Footprint is {}".format(footprint_g_co2))

    if int(footprint_g_co2) < 1: # kg of CO2
        raise ValueError('Possible to offset only multiplies of kg CO2 (1000 gram)')

    def burn_credits(volume: int):
        rospy.loginfo('going to burn {}'.format(volume))
        account = KeyfileHelper(KEYFILE,
                keyfile_password_file=KEYFILE_PASSWORD_FILE
                ).get_local_account_from_keyfile()
        http_provider = HTTPProvider(WEB3_HTTP_PROVIDER)
        web3 = Web3(http_provider)

        vcu_token = web3.eth.contract(VCU.address, abi=VCU.abi)

        rospy.loginfo("My account is {}".format(account.address))

        rospy.wait_for_service('/eth/accounts')
        accounts = rospy.ServiceProxy('/eth/accounts', Accounts)()

        balance = vcu_token.functions.balanceOf(accounts.accounts[0].address).call()
        rospy.loginfo('VCU balance: {}'.format(balance))

        if volume > balance:
            raise ValueError('Volume exceed balance')

        rospy.loginfo('will burn {}'.format(volume))

        burn_call = vcu_token.functions.burn(volume)
        tx = sign_and_send(burn_call, account=account, web3=web3)
        rospy.loginfo("Tx is {}".format(tx.hex()))

        rospy.loginfo('burned')
        return tx.hex()

    volume = int(footprint_g_co2)

    tx = burn_credits(volume) # 1VCS means tCO2
    rospy.loginfo('offsetted {} kg co2'.format(footprint_g_co2))
    return (volume, tx)

