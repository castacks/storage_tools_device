#!/usr/bin/env python

import eventlet 
eventlet.monkey_patch()


import threading
from flask import Flask, request 
from flask_socketio import SocketIO
import os 

from Device import Device



app = Flask(__name__)
sockethost = SocketIO(app, async_mode="eventlet")



"""
This class represents a device that can scan for files, send them to a server, and interact with the server.

Attributes:
    m_config (dict): The configuration of the device, loaded from a YAML file.
"""
if __name__ == "__main__":
    import sys 
    print("% " + " ".join(sys.argv))
    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument("-c", "--config", type=str, required=False, default="../config/config.yaml", help="Config file for this instance")
    args = parser.parse_args()

    device = Device(args.config, sockethost)

    def run_device():
        # Use a synchronous method compatible with Eventlet for running the device
        device.run()

    # Start the device's run method as an Eventlet-compatible background task
    sockethost.start_background_task(run_device)

    app.route("/")(device.index)
    app.route("/get_config", methods=["GET"])(device.get_config)
    app.route("/save_config", methods=["POST"])(device.save_config)
    app.route("/disconnect", methods=["GET"])(device.do_disconnect)
    app.route("/debug", methods=["GET"])(device.debug_socket)

    sockethost.on("connect")(device.on_local_dashboard_connect)
    sockethost.on("disconnect")(device.on_local_dashboard_disconnect)

    port = os.environ.get("CONFIG_PORT", "8811")
    if len(port) == 0:
        port = 8811
    else:
        port = int(port)

    sockethost.run(app=app, host="0.0.0.0", port=port)
    # device.run()