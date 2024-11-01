
import asyncio
import json
import os
import pytz
import queue
import requests
import shutil
import socket
import socketio
import socketio.exceptions
import sys
import time
import uuid 
import yaml

from multiprocessing import Manager, Pool
from flask import jsonify, send_from_directory
from flask import request 
from flask_socketio import SocketIO
from threading import Lock
from threading import Thread
from typing import cast
from zeroconf import ServiceBrowser, ServiceStateChange
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from device.debug_print import debug_print
from device.SocketIOTQDM import  MultiTargetSocketIOTQDM
from device.utils import get_source_by_mac_address, pbar_thread, address_in_list
import device.reindexMCAP as reindexMCAP
from device.workers import SendWorkerArg, hash_worker, metadata_worker, send_worker

class Device:
    def __init__(self, filename: str, local_dashboard_sio:SocketIO, salt=None) -> None:
        """
        Initialize the Device object with a configuration file.

        Args:
            filename (str): The path to the configuration file.
        """
        ## the device dashboard socket.  for showing connection status
        ## and echo console messages.  
        self.m_local_dashboard_sio = local_dashboard_sio
        self.m_config_filename = filename 
        self.m_config = None

        with open(filename, "r") as f:
            self.m_config = yaml.safe_load(f)
            debug_print(json.dumps(self.m_config, indent=True))

        robot_name = self.m_config.get("robot_name", "robot")
        self.m_config["source"] = get_source_by_mac_address(robot_name)

        if salt:
            self.m_config["source"] += str(salt)

        self.m_config["servers"] = self.m_config.get("servers", [])
        self.m_server = None

        self.m_signal = {}
        self.m_fs_info = {}
        self.m_send_offsets = {}
        self.m_send_lock = {}
        self.m_files = None
        self.m_md5 = {}
        self.m_updates = {}
        self.m_computeMD5 = self.m_config.get("computeMD5", True)
        self.m_chunk_size = self.m_config.get("chunk_size", 8192*1024)
        self.m_local_tz = self.m_config.get("local_tz", "America/New_York")

        debug_print(f"Setting source name to {self.m_config['source']}")

        # test to make sure time zone is set correctly. 
        try:
            pytz.timezone(self.m_local_tz)
        except pytz.UnknownTimeZoneError:
            debug_print(f"Invalid config option 'local_tz'. The string '{self.m_local_tz}' is not a valid time zone ")
            sys.exit(1)

        services = ['_http._tcp.local.']
        self.m_zeroconfig = AsyncZeroconf()
        self.m_zero_conf_name = "Airlab_storage._http._tcp.local."
        self.browser = ServiceBrowser(self.m_zeroconfig.zeroconf, services, handlers=[self._zero_config_on_change])


        self.session_lock = Lock()
        self.server_threads = {}  # Stores threads for each server
        self.server_can_run = {}  # Stores the "can run" flag for each server
        self.server_sessions = {}  # Stores session ID for each server
        self.server_sio = {} # maps server to socket. 
        self.server_should_run = {} # controls the busy loop during a session. Set to false for an address to reconnect. 
        self.source_to_server  = {} # maps source name to server address
        self.server_to_source = {}  # maps servers tos sources.

        # list of connected servers
        self.m_connected_servers = []

        # thread to do scaning.  
        self.m_scan_thread = None 
        self.m_reindex_thread = None 
        self.m_metadata_thread = None 
        self.m_hash_thread = None 
        self.m_send_threads = {}

    def _create_client(self):
        """Create a socket connection

        Returns:
            socketio.Client: A connection to a server
        """
        sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,  # Infinite attempts
            reconnection_delay=1,  # Start with 1 second delay
            reconnection_delay_max=5,  # Maximum 5 seconds delay
            randomization_factor=0.5,  # Randomize delays by +/- 50%
            logger=False,  # Enable logging for debugging
            engineio_logger=False  # Enable Engine.IO logging
        )
        return sio 

    # zero conf
    async def _resolve_service_info(self, zeroconf: AsyncZeroconf, service_type: str, name: str):
        info = AsyncServiceInfo(service_type, name)
        if await info.async_request(zeroconf, 3000):
            addresses = [
                f"{addr}:{cast(int, info.port)}" for addr in info.parsed_scoped_addresses()
            ]
            properties = {k.decode('utf-8'): v.decode('utf-8') if isinstance(v, bytes) else v for k, v in info.properties.items()}

            source = properties.get("source", None)
            if source is None: 
                return
            debug_print( f"source is: {source}")

        # todo: change this to dict of array.  Also need to add a timeout and remove when too many disconnects.  
        self.m_config["zero_conf"] = []

        for address in addresses:
            if address in self.m_config["servers"]:
                continue

            if address_in_list(address, self.m_config["servers"]):
                continue 
            self.m_config["zero_conf"] =self.m_config.get("zero_conf", [])
            if address in self.m_config["zero_conf"]:
                continue
            self.m_config["zero_conf"].append(address)
            debug_print(f"added {address}")

        status = ""
        for address in self.m_config["servers"]:
            status += f"Server: {address}\n"
        
        for address in self.m_config["zero_conf"]:
            status += f"ZeroConf: {address}\n"
        self.m_local_dashboard_sio.emit("status", {"msg": status})
        debug_print(status)

        if len(self.m_config.get("zero_conf", [])) > 0:
            self.start_zero_config_servers()

    def run_async_task(self, zeroconf, service_type, name):
        debug_print("enter")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._resolve_service_info(zeroconf, service_type, name))
        loop.close()
        debug_print("exit")

    def _zero_config_on_change(self, zeroconf: AsyncZeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is ServiceStateChange.Added or state_change is ServiceStateChange.Updated:
            self.m_local_dashboard_sio.start_background_task(self.run_async_task, zeroconf, service_type, name)

    def on_local_dashboard_connect(self):
        debug_print("Dashboard connected")

        self.update_connections()
        self.m_local_dashboard_sio.emit("title", self.m_config["source"])

    def on_local_dashboard_disconnect(self):
        debug_print("Dashboard disconnected")

    def on_refresh(self):
        debug_print("refresh")
        self.emitFiles()
        return "ok", 200

    def on_restart_connections(self):
        debug_print("Restart connections")
        self.disconnect_all()

        time.sleep(0.5)

        servers = self.m_config["servers"]
        for server_address in servers:
            if server_address not in self.server_threads:
                self.start_server_thread(server_address, "restart_connection server threads")

        self.start_zero_config_servers()
        return "ok", 200

    def _on_disconnect(self):
        debug_print(f"Got disconnected")
        self.m_local_dashboard_sio.emit("server_disconnect")

    def _on_device_candel_transfer(self, data, server_address):
        debug_print((data, server_address))
        if server_address in self.m_signal:
            self.m_signal[server_address].set()

    def _on_keep_alive_ack(self):
        pass

    def _on_control_msg(self, data, server_address):
        debug_print(data)
        if data.get("action", "") == "cancel":
            # self.m_signal[server_address] = "cancel"
            if server_address in self.m_signal:   
                self.m_signal[server_address].set()

    def _on_update_entry(self, data):
        source = data.get("source")
        if source != self.m_config["source"]:
            return

        relpath = data.get("relpath")
        basename = data.get("basename")
        filename = os.path.join(relpath, basename)
        update = data.get("update")

        self.m_updates[filename] = self.m_updates.get(filename, {})
        self.m_updates[filename].update( update )


    def _on_set_project(self, data):
        debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return

        project = data.get("project")
        self.m_config["project"] = project

        self.emitFiles()

    def _on_set_md5(self, data):
        debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return

        self.m_computeMD5 = data.get("value", False)



    def _include(self, filename: str) -> bool:
        """
        Check if a file should be included based on its suffix.

        This method checks the file's suffix against the include and exclude suffix lists in the configuration.
        If an include suffix list is present, the file must match one of the suffixes to be included.
        If an exclude suffix list is present, the file will be excluded if it matches any of the suffixes.

        Args:
            filename (str): The name of the file to check.

        Returns:
            bool: True if the file should be included, False otherwise.
        """
        if filename.startswith("."):
            return False

        if "include_suffix" in self.m_config:
            for suffix in self.m_config["include_suffix"]:
                if filename.endswith(suffix):
                    return True
            return False
        if "exclude_suffix" in self.m_config:
            for suffix in self.m_config["exclude_suffix"]:
                if filename.endswith(suffix):
                    return False
            return True

    def _remove_dirpath(self, filename:str):
        for dirroot in self.m_config["watch"]:
            if filename.startswith(dirroot):
                rtn = filename.replace(dirroot, "")
                return rtn.strip("/")
        return filename

    def _emit_to_all_servers(self, event, msg):
        for sio in self.server_sio.values():
            if sio and sio.connected:
                sio.emit(event, msg)

    def _background_reindex(self):
        if self.m_reindex_thread is not None:
            debug_print("already reindexing")
            return 
        
        # placeholder to keep the other threads out
        self.m_reindex_thread = True 

        all_files = []
        event = "device_status_tqdm"
        socket_events = [(self.m_local_dashboard_sio, event, None)]
        for sio in self.server_sio.values():
            if sio and sio.connected:
                socket_events.append((sio, event, None))
        bad_files = []
        total_size = 0

        source = self.m_config["source"]
        max_threads = self.m_config["threads"]
        message_queue = queue.Queue()
        desc = "reindex"


        self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "msg": "Scanning for files", "room": self.m_config["source"]})
        for dirroot in self.m_config["watch"]:
            self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "msg": f"Scanning {dirroot} for files", "room": self.m_config["source"]})
            for root, _, files in os.walk(dirroot):
                for basename in files:
                    if not self._include(basename):
                        continue
                    
                    if basename.startswith("._"):
                        continue

                    if basename.endswith(".mcap"):
                        filename = os.path.join(root, basename)
                        if os.path.exists(filename) and os.path.getsize(filename) > 0:
                            all_files.append(filename)
                            
        debug_print("Scan complete")
        self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "room": self.m_config["source"]})        

        with MultiTargetSocketIOTQDM(total=len(all_files), desc="Scanning files", position=0, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:
            for fullpath in all_files:
                if not reindexMCAP.test_mcap_file(fullpath):
                    bad_files.append(fullpath)
                    total_size += os.path.getsize(fullpath)
                main_pbar.update()
        
        if len(bad_files) > 0:

            with Manager() as manager:
                message_queue = manager.Queue()

                repaired_files = []
                pool_queue = [ (message_queue, filename) for filename in bad_files ]

                thread = Thread(target=pbar_thread, args=(message_queue, total_size, source, socket_events, desc, max_threads))    
                thread.start()

                try:
                    with Pool(max_threads) as pool:
                        for name, status in pool.imap_unordered(hash_worker, pool_queue):
                            repaired_files.append((name, status))

                finally:
                    message_queue.put({"close": True})

        self.m_reindex_thread = None

        self._background_metadata()
        pass 

    def _background_metadata(self):
        debug_print("enter")
        if self.m_metadata_thread is not None:
            debug_print("already doing metadata scan")
            return 
        self.m_metadata_thread = True 

        all_files = []
        event = "device_status_tqdm"
        socket_events = [(self.m_local_dashboard_sio, event, None)]
        for sio in self.server_sio.values():
            if sio and sio.connected:
                socket_events.append((sio, event, None))
        total_size = 0

        source = self.m_config["source"]
        max_threads = self.m_config["threads"]
        desc = "Get Metadata"

        self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "msg": "Scanning for files", "room": self.m_config["source"]})
        for dirroot in self.m_config["watch"]:
            self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "msg": f"Scanning {dirroot} for files", "room": self.m_config["source"]})
            for root, _, files in os.walk(dirroot):
                for basename in files:
                    if not self._include(basename):
                        continue
                    
                    if basename.startswith("._"):
                        continue

                    filename = os.path.join(root, basename).replace(dirroot, "")
                    filename = filename.strip("/")
                    fullpath = os.path.join(root, basename)
                    all_files.append((dirroot, filename, fullpath))
                    total_size += os.path.getsize(fullpath)

        self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "room": self.m_config["source"]})        

        if len(all_files) > 0:
            with Manager() as manager:
                robot_name = self.m_config.get("robot_name", None)
                message_queue = manager.Queue()
                updates = manager.dict(self.m_updates)
                entries = []

                pool_queue = [ (message_queue, dirroot, filename, fullpath, robot_name, self.m_local_tz, updates) for (dirroot, filename, fullpath) in all_files ]
                thread = Thread(target=pbar_thread, args=(message_queue, total_size, source, socket_events, desc, max_threads))    
                thread.start()

                try:
                    with Pool(max_threads) as pool:
                        for entry in pool.imap_unordered(metadata_worker, pool_queue):
                            if entry:
                                entries.append(entry)                                
                finally:
                    message_queue.put({"close": True})

                self.m_files = entries
        else:
            debug_print("No files")

        self.m_metadata_thread = None
        self._background_hash()

    def _background_hash(self):
        if self.m_hash_thread is not None:
            debug_print("Already doing hash creation")
            return 
        
        if self.m_files is None or len(self.m_files) == 0:
            debug_print("No files")
            return 
            
        self.m_hash_thread = True 
        # debug_print(self.m_files[0])
        entries = self.m_files.copy()

        event = "device_status_tqdm"
        socket_events = [(self.m_local_dashboard_sio, event, None)]
        for sio in self.server_sio.values():
            if sio and sio.connected:
                socket_events.append((sio, event, None))
        total_size = 0

        source = self.m_config["source"]
        max_threads = self.m_config["threads"]
        message_queue = queue.Queue()
        desc = "Get File Hash"

        with Manager() as manager:
            message_queue = manager.Queue()

            for entry in entries:
                if not entry or "filename" not in entry:
                    continue

                filename = os.path.join(entry["dirroot"], entry["filename"])
                if os.path.exists(filename):
                    total_size += os.path.getsize(filename)
                
            pool_queue = [ (message_queue, entry, self.m_chunk_size) for entry in entries ]
            entries = []

            thread = Thread(target=pbar_thread, args=(message_queue, total_size, source, socket_events, desc, max_threads))    
            thread.start()

            try:
                with Pool(max_threads) as pool:
                    for i, entry in enumerate(pool.imap_unordered(hash_worker, pool_queue)):
                        if entry:
                            entries.append(entry)
            finally:
                message_queue.put({"close": True})

        self.m_files = entries
        self.m_hash_thread = None

        self.emitFiles()

    def _background_scan(self):

        self.m_local_dashboard_sio.start_background_task(self._background_reindex)
        pass 

    def _background_send_files(self, server:str, filelist:list):
        # debug_print("enter")
        if self.m_send_threads.get(server, None) is not None:
            debug_print(f"Already getting file for {server}")
            return 

        if len(filelist) == 0:
            debug_print(f"No files from {server}")
            return 

        url = f"http://{server}/file"
        source = self.m_config["source"]
        api_key_token = self.m_config["API_KEY_TOKEN"]
        split_size_gb = int(self.m_config.get("split_size_gb", 1))
        chunk_size_mb = int(self.m_config.get("chunk_size_mb", 1))
        read_size_b = chunk_size_mb * 1024 * 1024

        # send message to each connected server. 
        event = "device_status_tqdm"
        socket_events = [(self.m_local_dashboard_sio, event, None)]
        for sio in self.server_sio.values():
            if sio and sio.connected:
                socket_events.append((sio, event, None))
        total_size = 0

        for  _, _, _, offset_b, file_size in filelist:
            total_size += file_size - offset_b

        source = self.m_config["source"]
        max_threads = self.m_config["threads"]
        desc = "File Transfer"

        with Manager() as manager:
            message_queue = manager.Queue()
            self.m_signal[server] = manager.Event()
            shared_offsets = manager.dict(self.m_send_offsets)
            pool_queue = []
            files = []

            for idx, (dirroot, relative_path, upload_id, offset_b, file_size) in enumerate(filelist):
                name = f"{upload_id}_{idx}_{os.path.basename(relative_path)}" 
                signal = self.m_signal[server]
                args = SendWorkerArg(message_queue, dirroot, relative_path, upload_id, offset_b, file_size, signal, server, shared_offsets, split_size_gb, api_key_token, name, url, source, read_size_b)
                pool_queue.append(args)

            thread = Thread(target=pbar_thread, args=(message_queue, total_size, source, socket_events, desc, max_threads))    
            thread.start()

            with Pool(max_threads) as pool:
                try:
                    for result in pool.imap_unordered(send_worker, pool_queue):
                        files.append(result)
                        if self.m_signal[server].is_set():
                            pool.terminate()
                            break
                finally:
                    message_queue.put({"close": True})

            self.m_signal[server].clear()

        # done 
        self.m_send_threads[server] = None 


        sio = self.server_sio.get(server)
        if sio and sio.connected:
            sio.emit("estimate_runs", {"source": self.m_config["source"]})

        pass 

    def _update_fs_info(self):
        self.m_fs_info = {}

        for dirroot in self.m_config["watch"]:
            if os.path.exists(dirroot):
                dev = os.stat(dirroot).st_dev
                if not dev in self.m_fs_info:
                    total, used, free = shutil.disk_usage(dirroot)
                    free_percentage = (free / total) * 100
                    self.m_fs_info[dev] = (dirroot, f"{free_percentage:0.2f}")


    def _on_device_scan(self, data):
        source = data.get("source")
        if source != self.m_config["source"]:
            return

        self._background_scan()

    def _on_device_send(self, data, server):
        source = data.get("source")
        if source != self.m_config["source"]:
            return
        files = data.get("files")
        self.m_local_dashboard_sio.start_background_task(self._background_send_files, server, files)

    def _on_duplicate(self):
        
        pass 

    def on_device_remove(self, data):
        # debug_print(data)
        source = data.get("source")
        if source != self.m_config["source"]:
            return
        files = data.get("files")

        self._removeFiles(files)

    def isConnected(self, server):
        connected = server in self.server_sio and self.server_can_run[server] and self.server_sio[server].connected        
        return connected

    def _removeFiles(self, files:list):

        debug_print("Enter")
        for item in files:
            dirroot, file, upload_id = item
            fullpath = os.path.join(dirroot, file)

            if os.path.exists(fullpath):
                debug_print(f"Removing {fullpath}")
                os.remove(fullpath)
                # only rename for testing. 
                # bak = fullpath + ".bak"
                # if os.path.exists(bak): 
                #     continue
                # os.rename(fullpath, bak)

            md5 = fullpath + ".md5"
            if os.path.exists(md5):
                debug_print(f"Removing {md5}")
                os.remove(md5)
                # only rename for testing. 
                # bak = md5 + ".bak"
                # if os.path.exists(bak): 
                #     continue
                # os.rename(md5, bak)

            metadata = fullpath + ".metadata"
            if os.path.exists(metadata):
                debug_print(f"Removing {metadata}")
                os.remove(metadata)
                # bak = metadata + ".bak"
                # if os.path.exists(bak): 
                #     continue
                # os.rename(md5, bak)


        self._background_scan()
        # self._scan()
        # self.emitFiles()


    def send_device_data(self):    
        debug_print("enter")

        N = 100
        blocks = [self.m_files[i:i + N] for i in range(0, len(self.m_files), N)]

        self._update_fs_info()

        robot_name = self.m_config.get("robot_name", None)
        project = self.m_config.get("project")
        if project is not None and len(project) < 1:
            project = None 
        source = self.m_config["source"]

        device_data = {
            "source": source,
            "project": project,
            "robot_name": robot_name,
            "total": len(blocks),
            "fs_info": self.m_fs_info
        }
        self._emit_to_all_servers("device_data", device_data)

        blocks_count = len(blocks)
        for i, block in enumerate(blocks):
            msg = {
                "source": self.m_config["source"],
                "room": self.m_config["source"],
                "total": blocks_count,
                "block": block,
                "id": i
            }

            self._emit_to_all_servers("device_data_block", msg)
            time.sleep(0.01)

    def emitFiles(self):
        '''
        Send the list of files to the server. 

        Breaks up the list into bite sized chunks. 
        '''

        self.m_local_dashboard_sio.start_background_task(target=self.send_device_data)
        return "Ok"

    def index(self):
        return send_from_directory("static", "index.html")

    def get_config(self):
        return jsonify(self.m_config)

    def save_config(self):
        changed = False
        rescan = False
        reconnect = False
 
        config = request.json
        with self.session_lock:
            for key in config:
                if key in self.m_config:
                    if self.m_config[key] != config[key]:
                        changed = True
                        if key == "watch":
                            rescan = True
                        if key == "robot_name":
                            reconnect = True

                    self.m_config[key] = config[key]
                
            debug_print("updated config")

            with open(self.m_config_filename, "w") as f:
                yaml.dump(config, f)

        os.chmod(self.m_config_filename, 0o777 )

        if reconnect:
            robot_name = self.m_config["robot_name"]
            
            self.m_config["source"] = get_source_by_mac_address(robot_name)
            self.m_local_dashboard_sio.emit("title", self.m_config["source"])

            self.disconnect_all()

            self.start_zero_config_servers()

        # add any server that was adding by the update
        servers = self.m_config["servers"]
        for server_address in servers:
            if server_address not in self.server_threads:
                self.start_server_thread(server_address, "save_config, server threads")

        
        to_remove = []

        # remove any server that was deleted by the update and isn't zero conf. 
        for server_address in self.server_threads:
            if server_address not in self.m_config["servers"] and server_address not in self.m_config.get("zero_conf", []):
                to_remove.append(server_address)

        for server_address in to_remove:
           self.stop_server_thread(server_address)

        if rescan:
            # self._scan()
            self._background_scan()


        return "Saved", 200

    def run(self):
        for server_address in self.m_config["servers"]:
            self.start_server_thread(server_address, "config server list")

        self.m_local_dashboard_sio.start_background_task(self.update_connections_thread)

    def start_zero_config_servers(self):
        server_list = self.m_config.get("zero_conf", [])

        for server_address in server_list:
            self.server_can_run[server_address] = True
            self.server_should_run[server_address] = True

        thread = Thread(target=self.manage_zero_conf_connection, args=(server_list,))
        thread.start()


    def start_server_thread(self, server_address, from_src):
        # Initialize the "can run" flag and spawn a thread for the server
        self.server_can_run[server_address] = True
        self.server_should_run[server_address] = True
        # thread = eventlet.spawn(self.manage_connection, server_address)
        thread = Thread(target=self.manage_connection, args=(server_address,from_src))
        thread.start()
        self.server_threads[server_address] = thread

    def stop_server_thread(self, server_address):
        debug_print(f"enter stop {server_address}")
        # Set the "can run" flag to False to stop the server's thread
        if server_address in self.server_can_run:
            self.server_can_run[server_address] = False
            if server_address in self.server_threads:
                thread = self.server_threads.pop(server_address, None)
            # if thread:
            #     thread.kill()  # Kill the thread if necessary

        sio = None
        with self.session_lock:
            if server_address in self.server_can_run:
                del self.server_can_run[server_address]
            if server_address in self.server_should_run:
                del self.server_should_run[server_address]

            if server_address in self.server_sio:
                sio = self.server_sio[server_address]
                del self.server_sio[server_address]

        if sio:
            sio.emit('leave', { 'room': self.m_config["source"], "type": "device" })                               
            debug_print("Disconnect!")
            sio.disconnect()

        self.m_local_dashboard_sio.emit("server_remove",  {"name": server_address})



    def stop_zero_config_servers(self):
        debug_print("enter")
        for server_address in self.m_config.get("zero_conf", []):
            debug_print("before")
            self.stop_server_thread(server_address)
            debug_print("after")

    def disconnect_all(self):
        debug_print("enter")
        servers = sorted(self.server_threads)
        for server_address in servers:
            self.stop_server_thread(server_address)
        self.stop_zero_config_servers()
        self.source_to_server.clear()
        self.server_to_source.clear()
        debug_print("exit")

    def update_connections_thread(self):
        while True:
            self.update_connections()
            time.sleep(5)

    def update_connections(self):
        connections = {}
        for server_address in self.server_can_run:
            if  not self.server_can_run[server_address]:
                continue 
            sio = self.server_sio.get(server_address, None)
            source = self.server_to_source.get(server_address, "None")
            connections[server_address] = (sio and sio.connected, source)

        self.m_local_dashboard_sio.emit("server_connections", connections)

    def manage_zero_conf_connection(self, server_list):
        can_run = True 
        while can_run:
            for server_address in server_list:
                if not self.server_can_run.get(server_address, False):
                    can_run = False
                    time.sleep(self.m_config["wait_s"])
                    break 
                 
                try:
                    if self.server_should_run.get(server_address, False):
                       none_dup =  self.test_connection(server_address, "manage_zero_conf")
                       if not none_dup:
                           # this is a duplicate address!  
                           self.server_can_run[server_address] = False
                except Exception as e:
                    debug_print(f"Error with server {server_address}: {e}")
                
                time.sleep(self.m_config["wait_s"])
            time.sleep(self.m_config["wait_s"])


    def manage_connection(self, server_address, from_src):
        debug_print(f"Testing to {server_address}")

        while self.server_can_run.get(server_address, False):
            try:
                if self.server_should_run.get(server_address, False):
                    self.test_connection(server_address, from_src)
            except Exception as e:
                debug_print(f"Error with server {server_address}: {e}")
                # eventlet.sleep(self.m_config["wait_s"])  
            finally:
                time.sleep(self.m_config["wait_s"])

    def test_connection(self, server_address, from_src):
        debug_print(f"Testing {server_address} from {from_src}")

        sio = self._create_client()
        session_id = str(uuid.uuid4())
        duplicated = False
        source = None 

        @sio.event
        def connect():
            time.sleep(0.5)
            debug_print(f"---- connected {server_address}")
            sio.emit('join', { 'room': self.m_config["source"], "type": "device", "session_token": session_id })                               

        @sio.event
        def disconnect():
            debug_print(f"disconnected {server_address}")

            if duplicated:
                debug_print("Duplication disconnected")
                return 
            
            with self.session_lock:
                self.server_sio[server_address] = None 

                if server_address in self.server_to_source:
                    source = self.server_to_source[server_address]
                    del self.server_to_source[server_address]

                    if source in self.source_to_server:
                        del self.source_to_server[source]
                
            self.m_local_dashboard_sio.emit("server_connect",  {"name": server_address, "connected": False})

        @sio.event
        def device_send(data):
            self._on_device_send(data, server_address)

        @sio.event
        def keep_alive_ack():
            pass 

        @sio.event
        def dashboard_info(data):
            debug_print(data)

            self.server_sio[server_address] = sio
            self.source_to_server[source] = server_address
            self.server_to_source[server_address] = source
            
            # source = self.server_to_source.get(server_address)
            self.m_local_dashboard_sio.emit("server_connect",  {"name": server_address, "connected": True, "source": source})
            self._background_scan()
            pass 

        @sio.event
        def control_msg(data):
            self._on_control_msg(data, server_address)
        
        @sio.event
        def device_cancel_transfer(data):
            self._on_device_candel_transfer(data, server_address)

        @sio.event
        def update_entry(data):
            self._on_update_entry(data)

        @sio.event
        def set_project(data):
            self._on_set_project(data)

        @sio.event
        def device_scan(data):
            self._on_device_scan(data)

        @sio.event
        def device_remove(data):
            self.on_device_remove(data)

        api_key_token = self.m_config["API_KEY_TOKEN"]
        headers = {"X-Api-Key": api_key_token }

        try:
            server, port = server_address.split(":")
            port = int(port)
            debug_print(f"Testing to {server}:{port}")
            socket.create_connection((server, port))
            url = f"http://{server}:{port}/name"

            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                debug_print(f"Failed to fetch source name from {url} with error code {response.status_code} {response.content.decode('utf-8')}")
                return False
            
            msg = response.json()
            source = msg.get("source")

            with self.session_lock:
                if source and source in self.source_to_server and self.source_to_server[source] != server_address:
                    debug_print(f"Duplication! {source}, have:{self.source_to_server[source]}, testing:{server_address}")
                    self.server_can_run[server_address] = False
                    self.server_should_run[server_address] = False
                    duplicated = True
                    return False 

                debug_print("Connecting....")
                sio.connect(f"http://{server}:{port}/socket.io", headers=headers, transports=['websocket'])
                debug_print(f"Connected to {server_address}")

                self.server_sio[server_address] = sio
                self.source_to_server[source] = server_address
                self.server_to_source[server_address] = source
                debug_print(self.server_to_source)


        except socketio.exceptions.ConnectionError as e:
            debug_print(f"Failed to connect to {server_address} because {e} {e.args}")
            sio.disconnect()
            return True

        while self.server_can_run.get(server_address, False) and self.server_should_run.get(server_address, False):
            ts = self.m_config.get("wait_s", 5)
            # eventlet.sleep(ts)
            time.sleep(ts)
        
        debug_print(f"lost connection to {server_address}")
        try:
            sio.disconnect()
        except Exception as e:
            debug_print(f"Caught {e.what()} when trying to disconnect")

        return True

    def debug_socket(self):
        debug_print("start\n\nstart")
        thread = Thread(target=self._debug_socket)
        thread.start()
        debug_print("complete\n\ncomplete")
        return "ok", 200

    def _debug_socket(self):

        event = "device_status_tqdm"
        socket_events = [ (self.m_sio, event, None), (self.m_local_dashboard_sio, event, None)]

        total_size = 15
        with MultiTargetSocketIOTQDM(total=total_size, desc="Debug socket", position=0, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:
            for i in range(total_size):
                main_pbar.update()
                self.m_local_dashboard_sio.emit("ping", "msg")
                #eventlet.sleep(1)
                time.sleep(1)


        pass 