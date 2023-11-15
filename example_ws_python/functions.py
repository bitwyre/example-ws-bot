import json
import hmac
import logging

from websocket import WebSocket, _exceptions
from decimal import Decimal
from time import time_ns
from hashlib import sha256, sha512
from time import sleep
from random import choice, uniform, sample
from traceback import format_exc
from uuid import uuid4

from example_ws_python.config import (
    API_KEY,
    API_SECRET,
    URL_API_BITWYRE,
    URI_PUBLIC_API_BITWYRE,
    URI_PRIVATE_API_BITWYRE,
    WS_COMMANDS,
    TIMEOUT,
    SLEEP,
    OrderSide,
    OrderStatus,
)

logger = logging.getLogger("my_logger")
logger.setLevel(logging.DEBUG)  # Set the desired logging level

# Create a console handler
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)  # Set the desired logging level for the handler

# Create a formatter and add it to the handler
formatter = logging.Formatter("\n%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)

# Add the handler to the logger
logger.addHandler(ch)


class BitwyreWSBot:
    def __init__(
        self,
        instrument: str,
        mid_price: Decimal,
        qty: Decimal,
        price_precision: int,
        qty_precision: int,
        min_spread: Decimal,
        max_spread: Decimal,
    ):
        logger.debug("Starting BitwyreWSBot")

        # Initialize environments
        self.instrument = instrument
        self.base_asset = instrument.split("_")[0]
        self.quote_asset = instrument.split("_")[1]
        self.product = instrument.split("_")[2]
        self.api_key = API_KEY
        self.api_secret = API_SECRET
        self.timeout = TIMEOUT
        self.url = URL_API_BITWYRE
        self.uri_public = URI_PUBLIC_API_BITWYRE
        self.uri_private = URI_PRIVATE_API_BITWYRE
        self.sleep = SLEEP

        # Initialize orders
        self.open_bids = []
        self.open_asks = []
        self.closed_bids = []
        self.closed_asks = []

        # enums
        self.order_sides = [side.value for side in OrderSide]
        self.closed_status = [
            OrderStatus.DoneForToday.value,
            OrderStatus.Cancelled.value,
            OrderStatus.Replaced.value,
            OrderStatus.Stopped.value,
            OrderStatus.Rejected.value,
            OrderStatus.Suspended.value,
            OrderStatus.Expired.value,
            OrderStatus.Stopped.value,
        ]

        # configs
        self.mid_price = mid_price
        self.price_precision = price_precision
        self.qty_precision = qty_precision
        self.qty = qty
        self.min_spread = min_spread
        self.max_spread = max_spread

        # WS order control, delete and create
        self.cmd_create = WS_COMMANDS["CREATE_ORDER"]
        self.cmd_cancel = WS_COMMANDS["CANCEL_ORDER"]
        self.control_uri = URI_PRIVATE_API_BITWYRE["ORDER_CONTROL"]
        self.ws_control = self.connect(self.control_uri)

        # WS create info
        self.cmd_get = WS_COMMANDS["GET"]
        self.status_uri = URI_PRIVATE_API_BITWYRE["ORDER_STATUS"]
        self.ws_order_status = self.connect(self.status_uri)

    def connect(
        self,
        uri: str,
    ) -> WebSocket:
        url = self.url + uri
        ws = WebSocket()

        signature = self.sign(self.api_secret)

        header = {"api_key": self.api_key, "api_sign": signature}

        header = json.dumps(header)

        header = [f"API-Data: {header}"]

        logger.debug(f"opening ws connection to {url}")
        logger.debug(f"Header is {header}")

        logger.debug(f"Trying to handshake with {url}")
        try:
            ws.connect(url, header=header)
        except Exception as e:
            logger.error(f"Failed in connecting to WS client, url {url}, exiting")
            logger.error(e)
            logger.error(format_exc())
            exit(1)

        logger.debug("ws connection success")
        return ws

    @staticmethod
    def send_msg(
        ws: WebSocket,
        cmd: str,
        payload: str = "",
    ) -> (bool, dict):
        response = []
        success = False

        params = {}
        params["command"] = cmd
        params["payload"] = payload
        params["request_id"] = str(uuid4())
        params = json.dumps(params)

        try:
            logger.debug(f"Sending {params} through websocket")
            ws.send(params)
        except Exception as e:
            logger.error("Failed in sending message to WS client")
            logger.error(e)
            logger.error(format_exc())
            response = []
            success = False
            return (success, response)

        try:
            logger.debug(f"Receiving {params} through websocket")
            response = ws.send(params)
        except Exception as e:
            logger.error("Failed in sending message to WS client, exiting")
            logger.error(e)
            logger.error(format_exc())
            response = []
            success = False
            return (success, response)

        logger.debug(f"Raw WS response {response}")

        try:
            response = json.loads(response)
        except Exception as e:
            logger.error(f"Failed in parsing WS response, raw {response}, exiting")
            logger.error(e)
            logger.error(format_exc())
            response = []
            success = False
            return (success, response)

    def main(self):
        self.randomize_order()
        sleep(self.sleep)

        self.update_orders()
        sleep(self.sleep)

        self.random_cancel()
        sleep(self.sleep)

    def random_cancel(self):
        # delete random order to be cancelled
        order_tobe_cancelled = sample(
            self.open_bids, min(0, len(self.open_bids))
        ) + sample(self.open_asks, min(0, len(self.open_asks)))

        for order in order_tobe_cancelled:
            self.cancel_order(order_id=order["orderid"], qty="-1")  # cancel all qty

    def update_orders(self):
        updated_bids = []
        updated_asks = []
        bids_ids = [order["orderid"] for order in self.open_bids]
        bids_ask = [order["orderid"] for order in self.open_bids]

        # fetch order infos
        for order_id in bids_ids:
            success, result = self.order_info(order_id=order_id)
            if not success:
                continue
            updated_bids.append(result)

        for order_id in bids_ask:
            success, result = self.order_info(order_id=order_id)
            if not success:
                continue
            updated_asks.append(result)

        # replace order with updated ones
        for updated_order in updated_bids:
            logger.debug(f"Updating order {updated_order}")
            updated_order_id = updated_order["orderid"]
            updated_order_status = updated_order["ordstatus"]

            for index, order in enumerate(self.open_bids):
                order_id = order["orderid"]
                if (
                    order_id == updated_order_id
                    and updated_order_status not in self.closed_status
                ):
                    # Replace the order with the updated version
                    self.open_bids[index] = updated_order
                    logger.debug(
                        f"Order with orderid {updated_order_id} has been updated."
                    )
                    break
                    # Delete order if its already closed
                elif (
                    order_id == updated_order_id
                    and updated_order_status in self.closed_status
                ):
                    self.closed_bids.append(updated_order)
                    del self.open_bids[index]
                    break

        for updated_order in updated_asks:
            logger.debug(f"Updating order {updated_order}")
            updated_order_id = updated_order["orderid"]
            updated_order_status = updated_order["ordstatus"]

            for index, order in enumerate(self.open_asks):
                order_id = order["orderid"]
                if (
                    order_id == updated_order_id
                    and updated_order_status not in self.closed_status
                ):
                    # Replace the order with the updated version
                    self.open_asks[index] = updated_order
                    logger.debug(
                        f"Order with orderid {updated_order_id} has been updated."
                    )
                    break
                elif (
                    order_id == updated_order_id
                    and updated_order_status in self.closed_status
                ):
                    # Delete order if its already closed
                    del self.open_asks[index]
                    self.closed_asks.append(updated_order)
                    break

    def randomize_order(self):
        ordtype = 2  # limit order
        leverage = 1  # spot leverage is 1
        side = choice(self.order_sides)  # pick random side
        price = self.decim(round(self.mid_price, self.price_precision))
        qty = self.decim(round(self.qty, self.qty_precision))

        if len(self.open_bids + self.open_asks) == 0:
            # No open order, post original price
            return self.create_order(
                side=side,
                ordtype=ordtype,
                orderqty=str(qty),
                price=str(price),
                leverage=leverage,
            )

        self.mid_price = self.calculate_midprice()
        if side == OrderSide.Buy.value:
            price = self.mid_price * self.decim(
                1 - uniform(self.min_spread, self.max_spread)
            )
        else:
            price = self.mid_price * self.decim(
                1 + uniform(self.min_spread, self.max_spread)
            )

        price = self.decim(round(price, self.price_precision))
        return self.create_order(
            side=side,
            ordtype=ordtype,
            orderqty=str(qty),
            price=str(price),
            leverage=leverage,
        )

    def create_order(
        self,
        side: int,
        ordtype: int,
        orderqty: str,
        price: str = None,
        leverage: str = None,
        stoppx: str = None,
        clordid: str = None,
        timeinforce: int = None,
        expiretime: int = None,
        execinst: str = None,
    ):
        logger.debug("Inserting new order")
        payload = {
            "instrument": self.instrument,
            "side": side,
            "ordtype": ordtype,
            "orderqty": str(orderqty),
        }

        if price is not None:
            # Non-market orders (limit, ioc, etc) require price
            payload["price"] = str(price)

        if stoppx is not None:
            payload["stoppx"] = stoppx
        if clordid is not None:
            payload["clordid"] = clordid
        if timeinforce is not None:
            payload["timeinforce"] = timeinforce
        if expiretime is not None:
            payload["expiretime"] = expiretime
        if execinst is not None:
            payload["execinst"] = execinst

        if self.product == "futures":
            # Futures product requires leverage
            payload["leverage"] = int(leverage)
        else:
            # Spot product leverage is alwaus 1
            payload["leverage"] = int(leverage)

        payload = json.dumps(payload)

        success, result = self.send_msg(self.ws_control, self.cmd_create, payload)

        if not success:
            logger.error("Failed in posting order")
            return

        result = result["result"]
        """
        Exec report sample
        {
            "AvgPx": "0",
            "LastLiquidityInd": "0",
            "LastPx": "0",
            "LastQty": "0",
            "account": "a9e3d010-3169-489d-9063-ced912b0fdc8",
            "cancelondisconnect": 0,
            "clorderid": "",
            "cumqty": "0",
            "execid": "",
            "exectype": 0,
            "expiry": 0,
            "fill_price": "0",
            "instrument": "btc_usdt_spot",
            "leavesqty": "2.9301",
            "orderid": "a9e3d010-3169-489d-9063-ced912b0fdc9",
            "orderqty": "2.9301",
            "ordrejreason": "",
            "ordstatus": 0,
            "ordstatusReqID": "a9e3d010-3169-489d-9063-ced912b0fdc9",
            "ordtype": 1,
            "origclid": "a9e3d010-3169-489d-9063-ced912b0fdc9",
            "price": "10.0",
            "side": 2,
            "stoppx": "0",
            "time_in_force": 0,
            "timestamp": 123123132123,
            "transacttime": 0,
            "value": "100.0"
        }
        """
        if result["ordstatus"] in [0, 1, 11, 13]:
            # New, partial fill, calculating, open orders
            if side == 1:
                self.open_bids.append(result)
            elif side == 2:
                self.open_asks.append(result)
        else:
            # closed orders
            if side == 1:
                self.closed_bids.append(result)
            elif side == 2:
                self.closed_asks.append(result)
        return

    def order_info(
        self,
        order_id: str,
    ):
        success: bool = False
        result: dict = {}
        logger.debug(f"Gettiing info order {order_id}")
        payload = ""

        success, result = self.send_msg(self.ws_order_status, self.cmd_get, payload)
        if not success:
            logger.error("Failed in getting order info")
            return (success, result)

        result = result["result"][0]
        return (success, result)

    def cancel_order(self, order_id: str, qty: str):
        success: bool = False
        result: dict = {}
        logger.debug(f"Cancelling order {order_id} qty {qty}")

        payload = {"order_ids": [order_id], "qtys": [qty]}
        payload = json.dumps(payload)

        success, result = self.send_msg(self.ws_control, self.cmd_cancel, payload)
        if not success:
            logger.error("Failed in cancelling")
            return (success, result)

        return (success, result)

    @staticmethod
    def sign(secret_key: str):
        signature = hmac.new(
            secret_key.encode("utf-8"), "".encode("utf-8"), sha512
        ).hexdigest()
        return signature

    @staticmethod
    def decim(num):
        return Decimal(str(num))
