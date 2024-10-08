import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
import urllib
import socket 

import xxhash
from SocketIOTQDM import SocketIOTQDM, MultiTargetSocketIOTQDM
from debug_print import debug_print
import reindexMCAP
from flask import request 


import pytz
import requests
import socketio
import socketio.exceptions
import yaml
from flask import jsonify, send_from_directory
from zeroconf import ServiceBrowser, ServiceStateChange
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from flask_socketio import SocketIO
from eventlet import tpool
import eventlet 
from eventlet.lock import Semaphore as Lock


import json
import os
import queue
import shutil
import socket
import sys
from datetime import datetime
from threading import Thread
from typing import cast

from utils import get_source_by_mac_address, getDateFromFilename, getMetaData, pbar_thread




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

        debug_print(f"Setting source name to {self.m_config['source']}")

        self.m_config["servers"] = self.m_config.get("servers", [])

        self.m_server = None

        self.m_signal = {}
        self.m_fs_info = {}

        self.m_files = None

        self.m_md5 = {}
        self.m_updates = {}
        self.m_computeMD5 = self.m_config.get("computeMD5", True)
        self.m_chunk_size = self.m_config.get("chunk_size", 8192*1024)

        self.m_local_tz = self.m_config.get("local_tz", "America/New_York")

        # test to make sure time zone is set correctly. 
        try:
            pytz.timezone(self.m_local_tz)
        except pytz.UnknownTimeZoneError:
            debug_print(f"Invalid config option 'local_tz'. The string '{self.m_local_tz}' is not a valid time zone ")
            sys.exit(1)

        services = ['_http._tcp.local.']
        self.m_zeroconfig = AsyncZeroconf()
        self.m_zero_conf_name = "Airlab_storage._http._tcp.local."
        self.browser = ServiceBrowser(self.m_zeroconfig.zeroconf, services, handlers=[self.on_change])


        self.session_lock = Lock()
        self.server_threads = {}  # Stores threads for each server
        self.server_can_run = {}  # Stores the "can run" flag for each server
        self.server_sessions = {}  # Stores session ID for each server
        self.server_sio = {} # maps server to socket. 
        self.server_should_run = {} # controls the busy loop during a session. Set to false for an address to reconnect. 

        # list of connected servers
        self.m_connected_servers = []
        self.m_address_to_source = {}

        # thread to do scaning.  
        self.m_scan_thread = None 
        self.m_reindex_thread = None 
        self.m_metadata_thread = None 
        self.m_hash_thread = None 

    def _create_client(self):
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


    def on_local_dashboard_connect(self):
        debug_print("Dashboard connected")

        self.update_connections()
        self.m_local_dashboard_sio.emit("title", self.m_config["source"])
        # for server_name, sio in self.server_sio.items():
        #     connected = sio and sio.connected
        #     self.m_local_dashboard_sio.emit("server_connect", {"name": server_name, "connected": connected})


    def on_local_dashboard_disconnect(self):
        debug_print("Dashboard disconnected")
        pass

    def on_refresh(self):
        debug_print("refresh")
        self.emitFiles()
        return "ok", 200


    def on_restart_connections(self):
        debug_print("Restart connections")
        # self.server_sio[server_address] = sio
        for server_name in self.server_should_run:
            self.server_should_run[server_name] = False 

        time.sleep(1 + float(self.m_config["wait_s"]))

        for server_name in self.server_should_run:
            self.server_should_run[server_name] = True 

            
        return "ok", 200


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

        self.m_config["zero_conf"] = []

        for address in addresses:
            if address in self.m_config["servers"]:
                continue
            self.m_config["zero_conf"] =self.m_config.get("zero_conf", [])
            if address in self.m_config["zero_conf"]:
                continue
            self.m_config["zero_conf"].append(address)
            debug_print(f"added {address}")

        # pick the first server
        if len(self.m_config.get("zero_conf", [])) > 0:
            server = self.m_config["zero_conf"][0]
            self.start_server_thread(server)

    def run_async_task(self, zeroconf, service_type, name):
        debug_print("enter")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._resolve_service_info(zeroconf, service_type, name))
        loop.close()
        debug_print("exit")

    def on_change(self, zeroconf: AsyncZeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        debug_print(state_change)
        if state_change is ServiceStateChange.Added or state_change is ServiceStateChange.Updated:
            # self.m_local_dashboard_sio.start_background_task(asyncio.ensure_future, self._resolve_service_info(zeroconf, service_type, name))
            self.m_local_dashboard_sio.start_background_task(self.run_async_task, zeroconf, service_type, name)


    def _on_disconnect(self):
        debug_print(f"Got disconnected")
        self.m_local_dashboard_sio.emit("server_disconnect")


    def _on_keep_alive_ack(self):
        pass

    def _on_control_msg(self, data, server_address):
        debug_print(data)
        if data.get("action", "") == "cancel":
            self.m_signal[server_address] = "cancel"

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
                        all_files.append(os.path.join(root, basename))
        self._emit_to_all_servers("device_status", {"source": self.m_config["source"], "room": self.m_config["source"]})        

        with MultiTargetSocketIOTQDM(total=len(all_files), desc="Scanning files", position=0, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:
            for fullpath in all_files:
                if not reindexMCAP.test_mcap_file(fullpath):
                    bad_files.append(fullpath)
                    total_size += os.path.getsize(fullpath)
                main_pbar.update()
        
        if len(bad_files) > 0:

            def reindex_worker(args):
                message_queue, filename = args 
                size = os.path.getsize(filename)

                status, msg = reindexMCAP.recover_mcap(filename)
                if not status:
                    debug_print(msg)

                message_queue.put({"main_pbar": size})
                return filename, status


            repaired_files = []
            pool_queue = [ (message_queue, filename) for filename in bad_files ]

            thread = Thread(target=pbar_thread, args=(message_queue, total_size, source, socket_events, desc, max_threads))    
            thread.start()

            try:
                with ThreadPoolExecutor(max_workers=max_threads) as executor:
                    results = {}
                    for name, status in executor.map(reindex_worker, pool_queue):
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
        message_queue = queue.Queue()
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

            robot_name = self.m_config.get("robot_name", None)

            def metadata_worker(args):
                message_queue, dirroot, filename, fullpath = args 

                if not os.path.exists(fullpath):
                    message_queue.put({"main_pbar": 1})
                    return 

                size = os.path.getsize(fullpath)
                metadata_filename = fullpath + ".metadata"
                if os.path.exists(metadata_filename) and (os.path.getmtime(metadata_filename) > os.path.getmtime(fullpath)):
                    device_entry = json.load(open(metadata_filename, "r"))
                    if device_entry["site"] == None:
                        device_entry["site"] = "default"

                else:
                    metadata = getMetaData(fullpath, self.m_local_tz)
                    # eventlet.sleep(0)
                    if metadata is None:
                        # invalid file! silently ignore invalid files! 
                        message_queue.put({"main_pbar": size})
                        return 

                    formatted_date = getDateFromFilename(fullpath)
                    if formatted_date is None:
                        creation_date = datetime.fromtimestamp(os.path.getmtime(fullpath))
                        formatted_date = creation_date.strftime("%Y-%m-%d %H:%M:%S")
                    start_time = metadata.get("start_time", formatted_date)
                    end_time = metadata.get("end_time", formatted_date)

                    device_entry = {
                        "dirroot": dirroot,
                        "filename": filename,
                        "size": size,
                        "start_time": start_time,
                        "end_time": end_time,
                        "site": "default",
                        "robot_name": robot_name,
                        "md5": None
                    }
                    device_entry.update(metadata)

                if filename in self.m_updates:
                    device_entry.update( self.m_updates[filename])

                try:
                    with open(metadata_filename, "w") as fid:
                        json.dump(device_entry, fid, indent=True)
                    os.chmod(metadata_filename, 0o777)
                    
                except PermissionError as e:
                    debug_print(f"Failed to write [{metadata_filename}]. Permission Denied")
                except Exception as e:
                    debug_print(f"Error writing [{metadata_filename}]: {e}")

                message_queue.put({"main_pbar": size})

                return device_entry 

            entries = []
            pool_queue = [ (message_queue, dirroot, filename, fullpath) for (dirroot, filename, fullpath) in all_files ]

            thread = Thread(target=pbar_thread, args=(message_queue, total_size, source, socket_events, desc, max_threads))    
            thread.start()

            try:
                with ThreadPoolExecutor(max_workers=max_threads) as executor:
                    for entry in executor.map(metadata_worker, pool_queue):
                        if entry:
                            entries.append(entry)

            finally:
                message_queue.put({"close": True})

            self.m_files = entries

        self.m_metadata_thread = None

        self._background_hash()

    def _background_hash(self):
        debug_print("enter")
        if self.m_hash_thread is not None:
            debug_print("Already doing hash creation")
            return 
        
        if self.m_files is None:
            debug_print("No files")
            return 
        
        self.m_hash_thread = True 
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


        if len(entries) > 0:            
            for entry in entries:
                if not entry:
                    continue

                if "filename" not in entry:
                    return entry 

                filename = os.path.join(entry["dirroot"], entry["filename"])
                if os.path.exists(filename):
                    total_size += os.path.getsize(filename)
                

            def hash_worker(args):
                message_queue, entry, chunk_size = args 

                if "filename" not in entry:
                    return entry 
                
                filename = os.path.join(entry["dirroot"], entry["filename"])
                if not os.path.exists(filename):
                    return None 
                size = os.path.getsize(filename)
                cache_name = filename + ".md5"
                if os.path.exists(cache_name) and os.path.getmtime(cache_name) > os.path.getmtime(filename):
                    entry["md5"] = json.load(open(cache_name))
                    message_queue.put({"main_pbar": size})
                    return entry
                    

                x = xxhash.xxh128()
                name = urllib.parse.quote(filename).replace("/", "_")

                try:
                    with open(filename, 'rb') as f:
                        desc = os.path.basename(filename)
                        message_queue.put({"child_pbar": name, "desc": desc, "size": size, "action": "start"})

                        while chunk := f.read(chunk_size):
                            x.update(chunk)
                            update = len(chunk)
                            message_queue.put({"main_pbar": update})
                            message_queue.put({"child_pbar": name, "size": update, "action": "update"})

                        message_queue.put({"child_pbar": name, "action": "close"})
                except Exception as e:
                    debug_print(f"Caught exception {e}")

                entry["md5"] = x.hexdigest()
                with open(cache_name, "w") as fid:
                    json.dump(entry["md5"], fid)

                # debug_print(f"exit {os.path.basename(filename)}")
                return entry 

            pool_queue = [ (message_queue, entry, self.m_chunk_size) for entry in entries ]

            thread = Thread(target=pbar_thread, args=(message_queue, total_size, source, socket_events, desc, max_threads))    
            thread.start()

            entries = []

            try:
                with ThreadPoolExecutor(max_workers=max_threads) as executor:
                    results = {}
                    for entry in executor.map(hash_worker, pool_queue):
                        entries.append(entry)

            finally:
                message_queue.put({"close": True})

        self.m_files = entries
        self.m_hash_thread = None

        self.emitFiles()

    def _background_scan(self):

        self.m_local_dashboard_sio.start_background_task(self._background_reindex)
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
        # self._scan()
        # self.emitFiles()


    def _on_device_send(self, data, server):
        source = data.get("source")
        if source != self.m_config["source"]:
            return
        files = data.get("files")

        self._sendFiles(server, files)


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
        connected = server in self.server_sio and self.server_sio[server].connected        
        return connected

    def _sendFiles(self, server:str, filelist:list):
        ''' Send files to server. 

            Creates up to config["threads"] workers to send files via HTTP POST to the server. 

            Args:
                server hosname:port
                filelist list[(dirroot, relpath, upload_id, offset, size)]
        '''
        self.m_signal[server] = None

        num_threads = min(self.m_config["threads"], len(filelist))
        url = f"http://{server}/file"

        source = self.m_config["source"]
        api_key_token = self.m_config["API_KEY_TOKEN"]

        split_size_gb = self.m_config.get("split_size_gb", 1)

        debug_print(filelist)

        total_size = 0
        file_queue = queue.Queue()
        for file_pair in filelist:
            debug_print(f"add to queue {file_pair}")
            offset = file_pair[3]
            size = file_pair[4]
            try:
                total_size += int(size) - int(offset)
            except ValueError as e:
                debug_print(file_pair)
                raise e
            file_queue.put(file_pair)

        # Outer send loop progress bar. 

        event = "device_status_tqdm"
        socket_events = [(self.m_local_dashboard_sio, event, None)]
        if server in self.server_sio:
            socket_events.append( (self.server_sio[server], event, None) )

        with MultiTargetSocketIOTQDM(total=total_size, desc="File Transfer", position=0, unit="B", unit_scale=True, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:

            # Worker thread. 
            # Reads the file_queue until 
            #    queue is empty 
            #    the session is disconnected
            #    the "cancel" signal is received. 
            def worker(index:int):
                with requests.Session() as session:
                    
                    while self.isConnected(server):
                        try:
                            dirroot, file, upload_id, offset_, total_size = file_queue.get(block=False)
                            offset_b = int(offset_)
                            total_size = int(total_size)
                        except queue.Empty:
                            break

                        if self.m_signal == "cancel":
                            break

                        fullpath = os.path.join(dirroot, file)
                        if not os.path.exists(fullpath):
                            main_pbar.update()
                            continue

                        with open(fullpath, 'rb') as file:
                            params = {}
                            if offset_b > 0:
                                file.seek(offset_b)
                                params["offset"] = offset_b
                                total_size -= offset_b

                            split_size_b = 1024*1024*1024*split_size_gb
                            splits = total_size // split_size_b

                            params["splits"] = splits

                            headers = {
                                'Content-Type': 'application/octet-stream',
                                "X-Api-Key": api_key_token
                                }

                            # Setup the progress bar
                            
                            with MultiTargetSocketIOTQDM(total=total_size, unit="B", unit_scale=True, leave=False, position=1+index, source=self.m_config["source"], socket_events=socket_events) as pbar:
                            # with SocketIOTQDM(total=total_size, unit="B", unit_scale=True, leave=False, position=1+index, source=self.m_config["source"], socket=self.m_sio, event="device_status_tqdm") as pbar:
                                def read_and_update(offset_b, parent):

                                    read_count = 0
                                    while parent.isConnected(server):
                                        chunk = file.read(1024*1024)
                                        if not chunk:
                                            break
                                        yield chunk

                                        # Update the progress bars
                                        chunck_size = len(chunk)
                                        pbar.update(chunck_size)
                                        main_pbar.update(chunck_size)

                                        if self.m_signal:
                                            if self.m_signal == "cancel":
                                                break

                                        offset_b += chunck_size
                                        read_count += chunck_size

                                        if read_count >= split_size_b:
                                            break

                                for cid in range(1+splits):
                                    params["offset"] = offset_b
                                    params["cid"] = cid
                                    # debug_print(offset_b)
                                    # Make the POST request with the streaming data
                                    response = session.post(url + f"/{source}/{upload_id}", params=params, data=read_and_update(offset_b, self), headers=headers)

                                # debug_print("Complete")

                                if response.status_code != 200:
                                    print("Error uploading file:", response.text)

            # Set up the ThreadPoolExecutor with the desired number of threads
            with ThreadPoolExecutor(max_workers=num_threads) as pool:
                # Submit the worker tasks to the thread pool
                futures = [pool.submit(worker, i) for i in range(num_threads)]
                
                # Wait for all threads to finish
                for future in futures:
                    future.result()  # This will block until each worker is done


            # pool = eventlet.GreenPool(num_threads)
            # for i in range(num_threads):
            #     pool.spawn(worker, i)

            # pool.waitall()

        if self.m_signal.get(server, "") == "cancel":
            self.emitFiles()

        self.m_signal[server] = None


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


    def emitFiles(self, sio=None):
        '''
        Send the list of files to the server. 

        Breaks up the list into bite sized chunks. 
        '''

        self._update_fs_info()

        robot_name = self.m_config.get("robot_name", None)
        project = self.m_config.get("project")
        if project is not None and len(project) < 1:
            project = None 
        source = self.m_config["source"]


        if self.m_files is None:
            data = {
                "robot_name": robot_name,
                "project": project,
                "source": source,
                "fs_info": self.m_fs_info,
                "total": 0
                }
            
            # TODO: do a quick scan first, then do a longer scan
            # need to figure out a way to only do the longer scan once!
            # self._scan(do_md5=False)
            debug_print("Files is empty")
            return None

        # debug_print(files)
        if len(self.m_files) == 0:
            debug_print("No files to send")
            return None


        # debug_print(f"project name is: {project}")
        data = {
            "robot_name": robot_name,
            "project": project,
            "source": source,
            "fs_info": self.m_fs_info,
            "total": len(self.m_files)
            }

        debug_print(f"sending {len(self.m_files)}")

        if project and len(project) > 1:
            N = 100
            packs = [self.m_files[i:i + N] for i in range(0, len(self.m_files), N)]
            for pack in packs:
                msg = {
                    "source": source,
                    "files": pack,
                    "room": self.m_config["source"]
                }
                if sio:
                    sio.emit("device_files_items", msg)
                else:
                    self._emit_to_all_servers("device_files_items", msg)
            # eventlet.sleep(0.1)
            time.sleep(0.1)

        # eventlet.sleep(1)
        time.sleep(1)

        try:
            if sio:
                sio.emit("device_files", data)
            else:
                self._emit_to_all_servers("device_files", data)
        except socketio.exceptions.BadNamespaceError:
            pass 

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

        # add any server that was adding by the update
        servers = self.m_config["servers"]
        for server_address in servers:
            if server_address not in self.server_threads:
                self.start_server_thread(server_address)

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

        # # emit the files if the project name has changed!
        # if changed:
        #     self.emitFiles()

        self.update_connections()

        return "Saved", 200

    def run(self):
        for server_address in self.m_config["servers"]:
            self.start_server_thread(server_address)

    def start_server_thread(self, server_address):
        # Initialize the "can run" flag and spawn a thread for the server
        self.server_can_run[server_address] = True
        self.server_should_run[server_address] = True
        # thread = eventlet.spawn(self.manage_connection, server_address)
        thread = Thread(target=self.manage_connection, args=(server_address,))
        thread.start()
        self.server_threads[server_address] = thread

    def stop_server_thread(self, server_address):
        # Set the "can run" flag to False to stop the server's thread
        if server_address in self.server_can_run:
            self.server_can_run[server_address] = False
            thread = self.server_threads.pop(server_address, None)
            # if thread:
            #     thread.kill()  # Kill the thread if necessary

        with self.session_lock:
            del self.server_can_run[server_address]
            del self.server_should_run[server_address]

            if server_address in self.server_sio:
                del self.server_sio[server_address]

        self.m_local_dashboard_sio.emit("server_remove",  {"name": server_address})

    def disconnect_all(self):
        servers = sorted(self.server_threads)
        for server_address in servers:
            self.stop_server_thread(server_address)

    def update_connections(self):
        connections = {}
        for server_address in self.server_can_run:
            if  not self.server_can_run[server_address]:
                continue 
            sio = self.server_sio.get(server_address, None)
            connections[server_address] = sio and sio.connected

        debug_print(connections)
        self.m_local_dashboard_sio.emit("server_connections", connections)

    def manage_connection(self, server_address):
        debug_print(f"Testing to {server_address}")

        # check to see if this server is one of the zero conf server. 
        # if it is, remove it from the run list.  
        ip_address = None
        name, port = server_address.split(":")
        try:
            ip_address = socket.gethostbyname(name)
        except socket.gaierror as e:
            pass 

        # if ip_address and f"{ip_address}:{port}" in self.m_config.get("zero_config", []):
        #     self.server_can_run[server_address] = False

        # debug_print("-")
        

        while self.server_can_run.get(server_address, False):


            try:
                self.test_connection(server_address)
            except Exception as e:
                debug_print(f"Error with server {server_address}: {e}")
                # eventlet.sleep(self.m_config["wait_s"])  
                time.sleep(self.m_config["wait_s"])

    def test_connection(self, server_address):
        sio = self._create_client()

        @sio.event
        def connect():
            debug_print(f"connected {server_address}")
            with self.session_lock:
                self.server_sio[server_address] = sio

            sio.emit('join', { 'room': self.m_config["source"], "type": "device" })                               


        @sio.event
        def disconnect():
            with self.session_lock:
                self.server_sio[server_address] = None 

            self.m_local_dashboard_sio.emit("server_connect",  {"name": server_address, "connected": False})

        @sio.event
        def device_send(data):
            self._on_device_send(data, server_address)

        @sio.event
        def keep_alive_ack():
            pass 

        @sio.event
        def dashboard_info(data):
            self.m_local_dashboard_sio.emit("server_connect",  {"name": server_address, "connected": True})
            debug_print("scan")
            self._background_scan()
            pass 

        api_key_token = self.m_config["API_KEY_TOKEN"]
        headers = {"X-Api-Key": api_key_token }

        try:

            debug_print(f"Testing to {server_address}")
            server, port = server_address.split(":")
            socket.create_connection((server, port))

            sio.connect(f"http://{server_address}/socket.io", headers=headers, transports=['websocket'])
            debug_print(f"Connected to {server_address}")
    
            sio.on('control_msg')(self._on_control_msg)
            sio.on('update_entry')(self._on_update_entry)
            sio.on('set_project')(self._on_set_project)
            # self.m_sio.on('set_md5')(self._on_set_md5)
            sio.on("device_scan")(self._on_device_scan)
            # self.m_sio.on("device_send")(self._on_device_send)
            sio.on("device_remove")(self.on_device_remove)
            # self.m_sio.on("keep_alive_ack")(self._on_keep_alive_ack)

        except socketio.exceptions.ConnectionError as e:
            debug_print(f"Failed to connect to {server_address} because {e}")
            sio.disconnect()

        while self.server_can_run.get(server_address, False) and self.server_should_run.get(server_address, False):
            ts = self.m_config.get("wait_s", 5)
            # eventlet.sleep(ts)
            time.sleep(ts)
        
        try:
            sio.disconnect()
        except Exception as e:
            debug_print(f"Caught {e.what()} when trying to disconnect")


    def debug_socket(self):
        debug_print("start\n\nstart")
        thread = Thread(target=self._debug_socket)
        thread.start()
        debug_print("complete\n\ncomplete")
        return "ok", 200

    def _debug_socket(self):

        event = "device_status_tqdm"
        socket_events = [ (self.m_sio, event, None), (self.m_local_dashboard_sio, event, None)]

        # with SocketIOTQDM(total=total_size, desc="Compute MD5 sum", position=0, unit="B", unit_scale=True, leave=False, source=self.m_config["source"], socket=self.m_sio, event="device_status_tqdm") as main_pbar:
        total_size = 15
        with MultiTargetSocketIOTQDM(total=total_size, desc="Debug socket", position=0, leave=False, source=self.m_config["source"], socket_events=socket_events) as main_pbar:
            for i in range(total_size):
                main_pbar.update()
                self.m_local_dashboard_sio.emit("ping", "msg")
                #eventlet.sleep(1)
                time.sleep(1)


        pass 